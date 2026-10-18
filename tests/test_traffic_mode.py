"""The normalized traffic vocabulary: ``TrafficMode`` x departure time -> exact
wire parameters, per provider.

``NONE`` never adds time-of-day traffic, even with a departure time; ``TRAFFIC``
asks for the traffic expected at the departure time (now when none is given);
``LIVE`` is a deprecated alias that must produce byte-identical requests.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import parse_qs, urlsplit

import pytest
from helpers import body_form, body_json, path_of, qget

from thinwrap.location import (
    ConnectorError,
    EsriConfig,
    GoogleConfig,
    HereConfig,
    LatLng,
    MapboxConfig,
    Matrix,
    MatrixOptions,
    OsrmConfig,
    Passthrough,
    ProviderCode,
    Routing,
    RoutingOptions,
    TomTomConfig,
    TrafficMode,
    TravelMode,
)
from thinwrap.location._util import traffic_requested

TWO = [LatLng(1, 1), LatLng(2, 2)]
THREE = [LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
WHEN = dt.datetime(2026, 9, 28, 21, 30, tzinfo=dt.timezone.utc)
WHEN_ISO = "2026-09-28T21:30:00.000Z"
WHEN_ISO_S = "2026-09-28T21:30:00Z"
WHEN_MS = "1790631000000"

TRAFFIC_MODES = [TrafficMode.TRAFFIC, TrafficMode.LIVE]


class _Capture:
    """Records the first request and fails it, so only the wire shape is tested."""

    def __init__(self) -> None:
        self.calls = []

    def send(self, request):
        self.calls.append(request)
        raise RuntimeError("captured")


def _first(facade_call):
    cap = _Capture()
    try:
        facade_call(cap)
    except ConnectorError as exc:
        # The capture transport surfaces as a network error; anything else means
        # the connector refused the options before sending.
        assert cap.calls, f"refused before any request: {exc}"
    return cap.calls[0]


def _route(cfg, **kw):
    return _first(lambda t: Routing(cfg, transport=t).route(RoutingOptions(**kw)))


def _matrix(cfg, **kw):
    return _first(
        lambda t: Matrix(cfg, transport=t).matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], **kw))
    )


def _query(req) -> dict:
    return parse_qs(urlsplit(req.url).query)


# ---- vocabulary -------------------------------------------------------------


def test_traffic_value_and_live_alias_are_kept():
    assert TrafficMode.TRAFFIC.value == "traffic"
    assert TrafficMode.LIVE.value == "live"
    assert TrafficMode("traffic") is TrafficMode.TRAFFIC


@pytest.mark.parametrize(
    "mode,want",
    [(TrafficMode.NONE, False), (TrafficMode.TRAFFIC, True), (TrafficMode.LIVE, True), ("traffic", True), ("live", True), ("none", False)],
)
def test_traffic_requested(mode, want):
    assert traffic_requested(mode) is want


# ---- Google -----------------------------------------------------------------


@pytest.mark.parametrize("when", [None, WHEN])
def test_google_none_is_unaware_and_never_sends_a_time(when):
    for req in (
        _route(GoogleConfig("k"), waypoints=TWO, departure_time=when),
        _matrix(GoogleConfig("k"), departure_time=when),
    ):
        body = body_json(req)
        assert body["routingPreference"] == "TRAFFIC_UNAWARE"
        assert "departureTime" not in body


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
@pytest.mark.parametrize("when,want", [(None, None), (WHEN, WHEN_ISO)])
def test_google_traffic_is_aware_with_the_time_when_given(mode, when, want):
    for req in (
        _route(GoogleConfig("k"), waypoints=TWO, departure_time=when, traffic_mode=mode),
        _matrix(GoogleConfig("k"), departure_time=when, traffic_mode=mode),
    ):
        body = body_json(req)
        assert body["routingPreference"] == "TRAFFIC_AWARE"
        assert body.get("departureTime") == want


def test_google_passthrough_time_still_wins():
    req = _route(GoogleConfig("k"), waypoints=TWO, passthrough=Passthrough(body={"departureTime": WHEN_ISO}))
    assert body_json(req)["departureTime"] == WHEN_ISO


# ---- HERE -------------------------------------------------------------------


def test_here_none_without_time_is_departure_any():
    assert qget(_route(HereConfig("k"), waypoints=TWO), "departureTime") == "any"
    assert body_json(_matrix(HereConfig("k")))["departureTime"] == "any"


def test_here_none_with_time_keeps_it_and_disables_traffic():
    req = _route(HereConfig("k"), waypoints=TWO, departure_time=WHEN)
    assert qget(req, "departureTime") == WHEN_ISO
    assert qget(req, "traffic[mode]") == "disabled"
    body = body_json(_matrix(HereConfig("k"), departure_time=WHEN))
    assert body["departureTime"] == WHEN_ISO
    assert body["traffic"] == {"mode": "disabled"}


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
@pytest.mark.parametrize("when,want", [(None, None), (WHEN, WHEN_ISO)])
def test_here_traffic_uses_the_vendor_default(mode, when, want):
    q = _query(_route(HereConfig("k"), waypoints=TWO, departure_time=when, traffic_mode=mode))
    assert q.get("departureTime", [None])[0] == want
    assert "traffic[mode]" not in q
    body = body_json(_matrix(HereConfig("k"), departure_time=when, traffic_mode=mode))
    assert body.get("departureTime") == want
    assert "traffic" not in body


@pytest.mark.parametrize(
    "mode,traffic", [(TrafficMode.NONE, "disabled"), (TrafficMode.TRAFFIC, "enabled"), (TrafficMode.LIVE, "enabled")]
)
@pytest.mark.parametrize("when,want", [(None, ""), (WHEN, WHEN_ISO_S)])
def test_here_findsequence_traffic_segment(mode, traffic, when, want):
    req = _route(HereConfig("k"), waypoints=THREE, optimize=True, departure_time=when, traffic_mode=mode)
    assert f";traffic:{traffic}" in qget(req, "mode")
    assert qget(req, "departure") == want


def test_here_passthrough_traffic_mode_still_wins():
    req = _route(HereConfig("k"), waypoints=TWO, departure_time=WHEN, passthrough=Passthrough(query={"traffic[mode]": "default"}))
    assert qget(req, "traffic[mode]") == "default"


# ---- Esri -------------------------------------------------------------------


@pytest.mark.parametrize("when", [None, WHEN])
def test_esri_none_never_sends_a_time(when):
    form = body_form(_route(EsriConfig(api_key="k"), waypoints=TWO, departure_time=when))
    assert "startTime" not in form and "startTimeIsUTC" not in form
    form = body_form(_matrix(EsriConfig(api_key="k"), departure_time=when))
    assert "timeOfDay" not in form and "timeOfDayIsUTC" not in form


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
def test_esri_traffic_without_time_is_now(mode):
    form = body_form(_route(EsriConfig(api_key="k"), waypoints=TWO, traffic_mode=mode))
    assert form["startTime"] == "now"
    assert "startTimeIsUTC" not in form
    form = body_form(_matrix(EsriConfig(api_key="k"), traffic_mode=mode))
    assert form["timeOfDay"] == "now"
    assert "timeOfDayIsUTC" not in form


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
def test_esri_traffic_with_time_is_utc_epoch(mode):
    form = body_form(_route(EsriConfig(api_key="k"), waypoints=TWO, departure_time=WHEN, traffic_mode=mode))
    assert (form["startTime"], form["startTimeIsUTC"]) == (WHEN_MS, "true")
    form = body_form(_matrix(EsriConfig(api_key="k"), departure_time=WHEN, traffic_mode=mode))
    assert (form["timeOfDay"], form["timeOfDayIsUTC"]) == (WHEN_MS, "true")


def test_esri_passthrough_time_still_wins_under_none():
    form = body_form(
        _route(EsriConfig(api_key="k"), waypoints=TWO, departure_time=WHEN, passthrough=Passthrough(body={"startTime": "now"}))
    )
    assert form["startTime"] == "now"
    form = body_form(_matrix(EsriConfig(api_key="k"), passthrough=Passthrough(body={"timeOfDay": WHEN_MS})))
    assert form["timeOfDay"] == WHEN_MS


# ---- Mapbox -----------------------------------------------------------------


@pytest.mark.parametrize("when", [None, WHEN])
def test_mapbox_directions_none_is_driving_without_depart_at(when):
    req = _route(MapboxConfig("t"), waypoints=TWO, departure_time=when)
    assert path_of(req).startswith("/directions/v5/mapbox/driving/")
    assert "depart_at" not in _query(req)


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
@pytest.mark.parametrize("when,want", [(None, ""), (WHEN, WHEN_ISO_S)])
def test_mapbox_directions_traffic_is_driving_traffic(mode, when, want):
    req = _route(MapboxConfig("t"), waypoints=TWO, departure_time=when, traffic_mode=mode)
    assert path_of(req).startswith("/directions/v5/mapbox/driving-traffic/")
    assert qget(req, "depart_at") == want


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
@pytest.mark.parametrize("travel_mode,profile", [(TravelMode.WALKING, "walking"), (TravelMode.CYCLING, "cycling")])
def test_mapbox_directions_ignores_traffic_for_walking_and_cycling(mode, travel_mode, profile):
    req = _route(MapboxConfig("t"), waypoints=TWO, departure_time=WHEN, traffic_mode=mode, travel_mode=travel_mode)
    assert path_of(req).startswith(f"/directions/v5/mapbox/{profile}/")
    assert "depart_at" not in _query(req)


def test_mapbox_directions_passthrough_depart_at_still_wins():
    req = _route(MapboxConfig("t"), waypoints=TWO, passthrough=Passthrough(query={"depart_at": WHEN_ISO_S}))
    assert qget(req, "depart_at") == WHEN_ISO_S


@pytest.mark.parametrize("when", [None, WHEN])
def test_mapbox_matrix_none_is_driving_and_drops_the_time(when):
    req = _matrix(MapboxConfig("t"), departure_time=when)
    assert path_of(req).startswith("/directions-matrix/v1/mapbox/driving/")
    assert "depart_at" not in _query(req)


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
def test_mapbox_matrix_traffic_without_time_is_driving_traffic(mode):
    req = _matrix(MapboxConfig("t"), traffic_mode=mode)
    assert path_of(req).startswith("/directions-matrix/v1/mapbox/driving-traffic/")


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
def test_mapbox_matrix_and_optimization_refuse_traffic_at_a_time(mode):
    with pytest.raises(ConnectorError) as ei:
        Matrix(MapboxConfig("t"), transport=_Capture()).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], departure_time=WHEN, traffic_mode=mode)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_FIELD
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("t"), transport=_Capture()).route(
            RoutingOptions(waypoints=THREE, optimize=True, departure_time=WHEN, traffic_mode=mode)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_FIELD


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
@pytest.mark.parametrize("travel_mode,profile", [(TravelMode.WALKING, "walking"), (TravelMode.CYCLING, "cycling")])
def test_mapbox_matrix_and_optimization_drop_the_time_for_walking_and_cycling(mode, travel_mode, profile):
    # No traffic variant exists there, so traffic + time is treated exactly like NONE.
    req = _matrix(MapboxConfig("t"), departure_time=WHEN, traffic_mode=mode, travel_mode=travel_mode)
    assert path_of(req).startswith(f"/directions-matrix/v1/mapbox/{profile}/")
    assert "depart_at" not in _query(req)
    req = _route(MapboxConfig("t"), waypoints=THREE, optimize=True, departure_time=WHEN, traffic_mode=mode, travel_mode=travel_mode)
    assert path_of(req).startswith(f"/optimized-trips/v1/mapbox/{profile}/")
    assert "depart_at" not in _query(req)


@pytest.mark.parametrize("mode,when", [(TrafficMode.NONE, WHEN), (TrafficMode.TRAFFIC, None), (TrafficMode.LIVE, None)])
def test_mapbox_optimization_is_plain_driving(mode, when):
    # Optimization v1 has no driving-traffic profile: traffic is ignored, and a
    # time under NONE is dropped.
    req = _route(MapboxConfig("t"), waypoints=THREE, optimize=True, departure_time=when, traffic_mode=mode)
    assert path_of(req).startswith("/optimized-trips/v1/mapbox/driving/")
    assert "depart_at" not in _query(req)


# ---- TomTom -----------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,traffic", [(TrafficMode.NONE, "false"), (TrafficMode.TRAFFIC, "true"), (TrafficMode.LIVE, "true")]
)
@pytest.mark.parametrize("when,want", [(None, ""), (WHEN, WHEN_ISO)])
def test_tomtom_routing(mode, traffic, when, want):
    # No time-independent routing mode exists (departAt=any is rejected), so NONE
    # keeps the caller's time and only switches live traffic off.
    req = _route(TomTomConfig("k"), waypoints=TWO, departure_time=when, traffic_mode=mode)
    assert qget(req, "traffic") == traffic
    assert qget(req, "departAt") == want


def test_tomtom_routing_passthrough_traffic_still_wins():
    req = _route(TomTomConfig("k"), waypoints=TWO, passthrough=Passthrough(query={"traffic": "true"}))
    assert qget(req, "traffic") == "true"


@pytest.mark.parametrize("when", [None, WHEN])
def test_tomtom_matrix_none_is_historical_any(when):
    options = body_json(_matrix(TomTomConfig("k"), departure_time=when))["options"]
    assert (options["traffic"], options["departAt"]) == ("historical", "any")


@pytest.mark.parametrize("mode", TRAFFIC_MODES)
@pytest.mark.parametrize("when,want", [(None, "now"), (WHEN, WHEN_ISO)])
def test_tomtom_matrix_traffic_is_live(mode, when, want):
    options = body_json(_matrix(TomTomConfig("k"), departure_time=when, traffic_mode=mode))["options"]
    assert (options["traffic"], options["departAt"]) == ("live", want)


# ---- OSRM -------------------------------------------------------------------


@pytest.mark.parametrize("mode", [TrafficMode.NONE, TrafficMode.TRAFFIC, TrafficMode.LIVE])
@pytest.mark.parametrize("when", [None, WHEN])
def test_osrm_ignores_traffic_and_departure_time(mode, when):
    for req in (
        _route(OsrmConfig("http://osrm.test"), waypoints=TWO, departure_time=when, traffic_mode=mode),
        _matrix(OsrmConfig("http://osrm.test"), departure_time=when, traffic_mode=mode),
    ):
        assert "2026" not in req.url
        assert "traffic" not in req.url


# ---- LIVE is byte-identical to TRAFFIC -----------------------------------------


@pytest.mark.parametrize(
    "cfg",
    [GoogleConfig("k"), HereConfig("k"), EsriConfig(api_key="k"), MapboxConfig("t"), TomTomConfig("k"), OsrmConfig("http://osrm.test")],
    ids=lambda c: type(c).__name__,
)
@pytest.mark.parametrize("when", [None, WHEN])
def test_live_alias_sends_the_same_request_as_traffic(cfg, when):
    def wire(mode):
        r = _route(cfg, waypoints=TWO, departure_time=when, traffic_mode=mode)
        if isinstance(cfg, MapboxConfig) and when is not None:
            m = None  # Mapbox Matrix refuses traffic at a time (covered above).
        else:
            mr = _matrix(cfg, departure_time=when, traffic_mode=mode)
            m = (mr.method, mr.url, mr.body)
        return (r.method, r.url, r.body), m

    assert wire(TrafficMode.LIVE) == wire(TrafficMode.TRAFFIC)
