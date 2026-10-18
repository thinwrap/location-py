"""The error contract: which caller mistakes are a ``ConnectorError`` and which are
a native exception.

1. **Operation failures are ``ConnectorError``** — including request data the
   library rejects before sending (non-finite values, too few points, an
   unparseable date) and capability rejections (``unsupported_*``). Raised by the
   operation call, ``status_code is None``, no request sent.
2. **API misuse is the native exception, as early as possible, and never a
   ``ConnectorError``** — a non-config, a provider lacking the operation, a bad
   transport, a value outside a closed vocabulary, a wrong argument type.

Every native row also asserts it is NOT a ``ConnectorError``, so a well-meant
wrap cannot quietly flip the rule. Every row asserts zero transport calls.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from helpers import FakeTransport, no_sleep, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    EsriConfig,
    Geocoding,
    GoogleConfig,
    HereConfig,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    MapboxConfig,
    Matrix,
    MatrixOptions,
    OsrmConfig,
    PlaceDetailsInclude,
    PlaceDetailsOptions,
    PolylineQuality,
    ProviderCode,
    Routing,
    RoutingInclude,
    RoutingOptions,
    TomTomConfig,
    TrafficMode,
    TravelMode,
)
from thinwrap.location._google import GoogleRoutingConnector

TWO = [LatLng(1, 1), LatLng(2, 2)]
THREE = [LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
NAN = float("nan")
INF = float("inf")

CONFIGS = {
    "google": GoogleConfig("k"),
    "mapbox": MapboxConfig("t"),
    "here": HereConfig("k"),
    "esri": EsriConfig(api_key="k"),
    "osrm": OsrmConfig("https://osrm.example"),
    "tomtom": TomTomConfig("k"),
}
ALL = list(CONFIGS)
GEOCODERS = ["google", "mapbox", "here", "esri", "tomtom"]
ISOCHRONES = ["mapbox", "here", "esri", "tomtom"]


def _routing(p: str, fake: FakeTransport) -> Routing:
    return Routing(CONFIGS[p], transport=fake)


def _matrix(p: str, fake: FakeTransport) -> Matrix:
    return Matrix(CONFIGS[p], transport=fake, sleep=no_sleep)


def _geocoding(p: str, fake: FakeTransport) -> Geocoding:
    return Geocoding(CONFIGS[p], transport=fake)


def _isochrone(p: str, fake: FakeTransport) -> Isochrone:
    return Isochrone(CONFIGS[p], transport=fake)


def _expect_ce(call: Callable[[FakeTransport], Any], code: ProviderCode) -> ConnectorError:
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        call(fake)
    assert ei.value.provider_code == code
    assert ei.value.status_code is None
    assert fake.calls == []
    return ei.value


def _expect_native(call: Callable[[FakeTransport], Any], exc: type) -> BaseException:
    fake = FakeTransport()
    with pytest.raises(exc) as ei:
        call(fake)
    assert not isinstance(ei.value, ConnectorError)
    assert fake.calls == []
    return ei.value


# ---------------------------------------------------------------------------
# Native: facade construction
# ---------------------------------------------------------------------------

FACADES = [Routing, Matrix, Geocoding, Isochrone]


@pytest.mark.parametrize("facade", FACADES, ids=lambda f: f.__name__)
@pytest.mark.parametrize("config", [None, {"api_key": "k"}, "google", object()], ids=["None", "dict", "str", "object"])
def test_non_config_is_type_error(facade, config):
    err = _expect_native(lambda fake: facade(config, transport=fake), TypeError)
    assert facade.__name__ in str(err) and "GoogleConfig" in str(err)


@pytest.mark.parametrize(
    "facade,provider",
    [(Geocoding, "osrm"), (Isochrone, "osrm"), (Isochrone, "google")],
    ids=lambda v: getattr(v, "__name__", v),
)
def test_provider_lacking_the_operation_is_value_error(facade, provider):
    err = _expect_native(lambda fake: facade(CONFIGS[provider], transport=fake), ValueError)
    assert "does not support" in str(err)


@pytest.mark.parametrize("facade", FACADES, ids=lambda f: f.__name__)
@pytest.mark.parametrize("transport", ["x", 42, object()], ids=["str", "int", "object"])
def test_transport_without_send_is_type_error(facade, transport):
    # Before: `transport="x"` was accepted and every call reported a retryable
    # provider_unavailable outage for what is a bug in the calling code.
    config = CONFIGS["mapbox"]
    with pytest.raises(TypeError) as ei:
        facade(config, transport=transport)
    assert not isinstance(ei.value, ConnectorError)
    assert "transport" in str(ei.value)


def test_transport_check_also_guards_direct_connector_construction():
    with pytest.raises(TypeError):
        GoogleRoutingConnector(GoogleConfig("k"), "x")


def test_non_callable_sleep_is_type_error():
    with pytest.raises(TypeError) as ei:
        Matrix(CONFIGS["here"], transport=FakeTransport(), sleep=5)
    assert not isinstance(ei.value, ConnectorError)


def test_a_send_lost_after_construction_is_not_reported_as_an_outage():
    # `send` is resolved outside the transport-failure handler, so the
    # AttributeError surfaces as itself instead of provider_unavailable.
    class Transport:
        def __init__(self) -> None:
            self.send = lambda request: resp(200, "{}")

    t = Transport()
    r = Routing(CONFIGS["google"], transport=t)
    del t.send
    with pytest.raises(AttributeError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    assert not isinstance(ei.value, ConnectorError)


def test_a_failure_inside_send_is_still_a_transport_failure():
    class Boom:
        def send(self, request):
            raise OSError("connection refused")

    with pytest.raises(ConnectorError) as ei:
        Routing(CONFIGS["google"], transport=Boom()).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.PROVIDER_UNAVAILABLE


# ---------------------------------------------------------------------------
# Native: closed vocabularies — ValueError for a bad string, TypeError for a
# non-string. Rows run through every provider so none can silently default.
# ---------------------------------------------------------------------------

ROUTING_ENUM_CASES = [
    ("travel_mode", "flying", ValueError),
    ("travel_mode", None, TypeError),
    ("travel_mode", 1, TypeError),
    ("traffic_mode", "rush-hour", ValueError),
    ("traffic_mode", True, TypeError),
    ("polyline_quality", "ultra", ValueError),
    ("polyline_quality", None, TypeError),
    ("include", ["bogus"], ValueError),
    ("include", [RoutingInclude.DURATION_WITHOUT_TRAFFIC, "bogus"], ValueError),
    ("include", "durationWithoutTraffic", TypeError),
    ("include", None, TypeError),
    ("include", [1], TypeError),
    ("departure_time", 1767225600, TypeError),
]


@pytest.mark.parametrize("provider", ALL)
@pytest.mark.parametrize("field,value,exc", ROUTING_ENUM_CASES, ids=lambda v: repr(v)[:30])
def test_routing_vocabulary_misuse_is_native(provider, field, value, exc):
    err = _expect_native(lambda fake: _routing(provider, fake).route(RoutingOptions(waypoints=TWO, **{field: value})), exc)
    assert f"RoutingOptions.{field}" in str(err)


MATRIX_ENUM_CASES = [
    ("travel_mode", "flying", ValueError),
    ("travel_mode", None, TypeError),
    ("traffic_mode", "rush-hour", ValueError),
    ("traffic_mode", 0, TypeError),
    ("departure_time", 1767225600, TypeError),
]


@pytest.mark.parametrize("provider", ALL)
@pytest.mark.parametrize("field,value,exc", MATRIX_ENUM_CASES, ids=lambda v: repr(v)[:30])
def test_matrix_vocabulary_misuse_is_native(provider, field, value, exc):
    _expect_native(
        lambda fake: _matrix(provider, fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], **{field: value})
        ),
        exc,
    )


ISOCHRONE_ENUM_CASES = [
    ("type", "bogus", ValueError),
    ("type", None, TypeError),
    ("travel_mode", "flying", ValueError),
    ("travel_mode", 2, TypeError),
    ("departure_time", 1767225600, TypeError),
]


@pytest.mark.parametrize("provider", ISOCHRONES)
@pytest.mark.parametrize("field,value,exc", ISOCHRONE_ENUM_CASES, ids=lambda v: repr(v)[:30])
def test_isochrone_vocabulary_misuse_is_native(provider, field, value, exc):
    # Before: `type="bogus"` silently became a DISTANCE isochrone on three providers.
    kwargs: dict[str, Any] = {"center": LatLng(1, 1), "type": IsochroneType.TIME, "values": [600], field: value}
    _expect_native(lambda fake: _isochrone(provider, fake).isochrone(IsochroneOptions(**kwargs)), exc)


@pytest.mark.parametrize("provider", GEOCODERS)
@pytest.mark.parametrize(
    "value,exc", [(["bogus"], ValueError), ("name", TypeError), (None, TypeError), (5, TypeError)], ids=repr
)
def test_place_details_include_misuse_is_native(provider, value, exc):
    _expect_native(
        lambda fake: _geocoding(provider, fake).place_details(PlaceDetailsOptions(place_id="p", include=value)), exc
    )


def test_valid_raw_strings_are_accepted_and_normalized():
    r = RoutingOptions(
        waypoints=TWO, travel_mode="walking", traffic_mode="traffic", polyline_quality="detailed",
        include=["durationWithoutTraffic"],
    )
    assert r.travel_mode is TravelMode.WALKING
    assert r.traffic_mode is TrafficMode.TRAFFIC
    assert r.polyline_quality is PolylineQuality.DETAILED
    assert r.include == (RoutingInclude.DURATION_WITHOUT_TRAFFIC,)
    assert r.includes(RoutingInclude.DURATION_WITHOUT_TRAFFIC)

    assert MatrixOptions(origins=TWO, destinations=TWO, traffic_mode="live").traffic_mode is TrafficMode.LIVE
    assert MatrixOptions(origins=TWO, destinations=TWO, traffic_mode="none").traffic_mode is TrafficMode.NONE

    iso = IsochroneOptions(center=LatLng(1, 1), type="distance", values=[500], travel_mode="cycling")
    assert iso.type is IsochroneType.DISTANCE and iso.travel_mode is TravelMode.CYCLING

    pd = PlaceDetailsOptions(place_id="p", include=["name"])
    assert pd.include == (PlaceDetailsInclude.NAME,)


def test_member_of_another_enum_is_rejected_even_though_it_is_a_str():
    with pytest.raises(ValueError):
        RoutingOptions(waypoints=TWO, travel_mode=TrafficMode.NONE)


def test_here_transport_mode_is_forwarded_verbatim_not_validated():
    # HERE judges it: the library does not translate it, so a local check would
    # change the error type the caller gets from the vendor.
    fake = FakeTransport(resp(400, '{"title":"Unknown transport mode"}'))
    with pytest.raises(ConnectorError) as ei:
        _routing("here", fake).route(RoutingOptions(waypoints=TWO, transport_mode="hovercraft"))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert ei.value.status_code == 400
    assert "transportMode=hovercraft" in fake.last.url


# ---------------------------------------------------------------------------
# Native: wrong argument types reaching a formatter
# ---------------------------------------------------------------------------

STR_DEPARTURE_ROUTING = [
    ("google", {"traffic_mode": TrafficMode.TRAFFIC}),
    ("here", {}),
    ("here", {"optimize": True}),
    ("tomtom", {}),
    ("mapbox", {"traffic_mode": TrafficMode.TRAFFIC}),
]


@pytest.mark.parametrize("provider,extra", STR_DEPARTURE_ROUTING, ids=lambda v: str(v)[:20])
def test_string_departure_time_is_type_error_where_only_a_datetime_is_accepted(provider, extra):
    waypoints = THREE if extra.get("optimize") else TWO
    err = _expect_native(
        lambda fake: _routing(provider, fake).route(
            RoutingOptions(waypoints=waypoints, departure_time="2026-09-28T12:00:00Z", **extra)
        ),
        TypeError,
    )
    assert "departure_time" in str(err)


@pytest.mark.parametrize(
    "provider,extra",
    [("google", {"traffic_mode": TrafficMode.TRAFFIC}), ("here", {}), ("tomtom", {"traffic_mode": TrafficMode.TRAFFIC})],
    ids=str,
)
def test_string_departure_time_is_type_error_on_matrix(provider, extra):
    _expect_native(
        lambda fake: _matrix(provider, fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], departure_time="2026-09-28T12:00:00Z", **extra)
        ),
        TypeError,
    )


def test_esri_keeps_accepting_an_iso_string_departure_time():
    fake = FakeTransport(resp(200, '{"routes":{"features":[]}}'))
    with pytest.raises(ConnectorError):  # the body is not a usable route; the point is it was SENT
        _routing("esri", fake).route(RoutingOptions(waypoints=TWO, departure_time="2026-09-28T12:00:00Z"))
    assert len(fake.calls) == 1


@pytest.mark.parametrize("provider", ["google", "here", "tomtom"])
@pytest.mark.parametrize("radius", ["500", True], ids=["str", "bool"])
def test_non_numeric_radius_is_type_error(provider, radius):
    _expect_native(
        lambda fake: _geocoding(provider, fake).autocomplete(
            AutocompleteOptions(input="pizza", location=LatLng(1, 1), radius=radius)
        ),
        TypeError,
    )


# ---------------------------------------------------------------------------
# ConnectorError: request data rejected before sending
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["google", "here", "tomtom"])
@pytest.mark.parametrize("radius", [NAN, INF, -INF], ids=["nan", "inf", "-inf"])
def test_non_finite_radius_is_invalid_request(provider, radius):
    # Before: a bare ValueError/OverflowError from the coordinate formatter.
    _expect_ce(
        lambda fake: _geocoding(provider, fake).autocomplete(
            AutocompleteOptions(input="pizza", location=LatLng(1, 1), radius=radius)
        ),
        ProviderCode.INVALID_REQUEST,
    )


@pytest.mark.parametrize("provider", ALL)
@pytest.mark.parametrize("waypoints", [[], [LatLng(1, 1)]], ids=["zero", "one"])
@pytest.mark.parametrize("optimize", [False, True], ids=["direct", "optimized"])
def test_fewer_than_two_waypoints_is_invalid_request(provider, waypoints, optimize):
    _expect_ce(
        lambda fake: _routing(provider, fake).route(RoutingOptions(waypoints=waypoints, optimize=optimize)),
        ProviderCode.INVALID_REQUEST,
    )


@pytest.mark.parametrize("provider", ALL)
@pytest.mark.parametrize(
    "origins,destinations",
    [([], [LatLng(1, 1)]), ([LatLng(1, 1)], []), (None, [LatLng(1, 1)]), ([LatLng(1, 1)], None)],
    ids=["no-origins", "no-destinations", "None-origins", "None-destinations"],
)
def test_empty_matrix_is_invalid_request(provider, origins, destinations):
    _expect_ce(
        lambda fake: _matrix(provider, fake).matrix(MatrixOptions(origins=origins, destinations=destinations)),
        ProviderCode.INVALID_REQUEST,
    )


@pytest.mark.parametrize("provider", ALL)
def test_non_finite_waypoint_is_invalid_request(provider):
    _expect_ce(
        lambda fake: _routing(provider, fake).route(RoutingOptions(waypoints=[LatLng(NAN, 1), LatLng(2, 2)])),
        ProviderCode.INVALID_REQUEST,
    )


@pytest.mark.parametrize("provider", ISOCHRONES)
@pytest.mark.parametrize("value", ["tomorrow", "2026-13-01T00:00"])
def test_unparseable_isochrone_departure_is_invalid_request(provider, value):
    _expect_ce(
        lambda fake: _isochrone(provider, fake).isochrone(
            IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=[600], departure_time=value)
        ),
        ProviderCode.INVALID_REQUEST,
    )


def test_unparseable_esri_routing_departure_is_invalid_request():
    _expect_ce(
        lambda fake: _routing("esri", fake).route(RoutingOptions(waypoints=TWO, departure_time="next tuesday")),
        ProviderCode.INVALID_REQUEST,
    )


@pytest.mark.parametrize("provider", ISOCHRONES)
@pytest.mark.parametrize("values", [[], [0], [NAN], [1, 2, 3, 4, 5]], ids=["empty", "zero", "nan", "five"])
def test_bad_isochrone_breaks_are_invalid_request(provider, values):
    _expect_ce(
        lambda fake: _isochrone(provider, fake).isochrone(
            IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=values)
        ),
        ProviderCode.INVALID_REQUEST,
    )


# ---------------------------------------------------------------------------
# ConnectorError: capability rejections and config that only fails at call time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["here", "esri", "tomtom"])
def test_cycling_isochrone_where_unsupported_is_unsupported_travel_mode(provider):
    _expect_ce(
        lambda fake: _isochrone(provider, fake).isochrone(
            IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=[600], travel_mode=TravelMode.CYCLING)
        ),
        ProviderCode.UNSUPPORTED_TRAVEL_MODE,
    )


@pytest.mark.parametrize("provider", ["esri", "tomtom"])
def test_cycling_matrix_where_unsupported_is_unsupported_travel_mode(provider):
    _expect_ce(
        lambda fake: _matrix(provider, fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], travel_mode=TravelMode.CYCLING)
        ),
        ProviderCode.UNSUPPORTED_TRAVEL_MODE,
    )


@pytest.mark.parametrize(
    "config,code",
    [
        (EsriConfig(), ProviderCode.AUTH_FAILED),
        (EsriConfig(api_key="k", arcgis_token="t"), ProviderCode.INVALID_REQUEST),
    ],
    ids=["neither", "both"],
)
def test_esri_credentials_are_checked_at_call_time(config, code):
    # Construction succeeds: EsriConfig is a plain dataclass, so the check runs
    # when the operation is called.
    r = Routing(config, transport=FakeTransport())
    fake = r._c._transport
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == code and ei.value.status_code is None
    assert fake.calls == []


def test_osrm_schemeless_base_url_is_invalid_request_at_call_time():
    fake = FakeTransport()
    r = Routing(OsrmConfig("osrm.example"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST and ei.value.status_code is None
    assert fake.calls == []
