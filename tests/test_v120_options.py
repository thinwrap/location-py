"""Cross-provider contract for the 1.2.0 routing inputs and the normalized
``no_route`` code.

These assert the **request**, not just the result, because all three inputs are
cost-bearing: ``include`` and ``traffic_mode`` change what the vendor bills, and
``polyline_quality`` changes response size by up to 31x.

Every ``no_route`` fixture is the LIVE-OBSERVED body for an unroutable request,
captured from the real API — change one only against a fresh live capture, never
against the vendor's docs, which were wrong for three of the six. The vendors agree on
nothing: Google 200 with ``routes`` absent, HERE 200 with ``routes: []`` plus
``notices[]``, Mapbox ``code: "NoRoute"`` on 200 or 422, OSRM the same codes on a
400, TomTom a 400 with ``detailedError.code``, Esri a 200 with an in-body
``error.code: 400`` whose ``details[]`` say a stop is *unlocated*.
"""

from __future__ import annotations

import json

import pytest
from helpers import FakeTransport, qget, resp

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
    PolylineQuality,
    ProviderCode,
    Routing,
    RoutingInclude,
    RoutingOptions,
    TomTomConfig,
    TrafficMode,
)

TWO = [LatLng(0, 0), LatLng(1, 1)]
THREE = [LatLng(0, 0), LatLng(1, 1), LatLng(2, 2)]

GOOGLE_BODY = json.dumps({
    "routes": [{
        "legs": [{"distanceMeters": 5000, "duration": "300s", "staticDuration": "280s"}],
        "distanceMeters": 5000,
        "duration": "300s",
        "staticDuration": "280s",
        "polyline": {"encodedPolyline": "abc"},
    }]
})
GOOGLE_BODY_NO_STATIC = json.dumps({
    "routes": [{
        "legs": [{"distanceMeters": 5000, "duration": "300s"}],
        "distanceMeters": 5000,
        "duration": "300s",
        "polyline": {"encodedPolyline": "abc"},
    }]
})
MAPBOX_BODY = json.dumps({
    "code": "Ok",
    "routes": [{"geometry": "", "legs": [{"distance": 5000, "duration": 300}], "distance": 5000, "duration": 300}],
    "waypoints": [{"waypoint_index": 0}, {"waypoint_index": 1}],
})
OSRM_BODY = json.dumps({
    "code": "Ok",
    "routes": [{"geometry": "abc", "legs": [{"distance": 5000, "duration": 300}], "distance": 5000, "duration": 300}],
})
HERE_BODY = json.dumps({
    "routes": [{"sections": [{
        "polyline": "BGwl_lgDo-6-T",
        "summary": {"length": 5000, "duration": 300, "baseDuration": 280},
    }]}]
})
TOMTOM_SUMMARY = {
    "lengthInMeters": 5000,
    "travelTimeInSeconds": 300,
    "noTrafficTravelTimeInSeconds": 280,
}
TOMTOM_BODY = json.dumps({
    "routes": [{
        "summary": TOMTOM_SUMMARY,
        "legs": [{"summary": TOMTOM_SUMMARY, "points": [{"latitude": 0, "longitude": 0}]}],
    }]
})


def body_of(fake: FakeTransport) -> dict:
    return json.loads(fake.last.body.decode("utf-8"))


# ---------------------------------------------------------------------------
# polyline_quality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quality,expected", [
    (PolylineQuality.SIMPLIFIED, "OVERVIEW"),
    (PolylineQuality.DETAILED, "HIGH_QUALITY"),
])
def test_google_polyline_quality(quality, expected):
    fake = FakeTransport(resp(200, GOOGLE_BODY))
    Routing(GoogleConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, polyline_quality=quality)
    )
    assert body_of(fake)["polylineQuality"] == expected


@pytest.mark.parametrize("quality,expected", [
    (PolylineQuality.SIMPLIFIED, "simplified"),
    (PolylineQuality.DETAILED, "full"),
])
def test_mapbox_and_osrm_overview(quality, expected):
    mb = FakeTransport(resp(200, MAPBOX_BODY))
    Routing(MapboxConfig("pk"), transport=mb).route(
        RoutingOptions(waypoints=TWO, polyline_quality=quality)
    )
    assert qget(mb.last, "overview") == expected

    os_ = FakeTransport(resp(200, OSRM_BODY))
    Routing(OsrmConfig("http://x"), transport=os_).route(
        RoutingOptions(waypoints=TWO, polyline_quality=quality)
    )
    assert qget(os_.last, "overview") == expected


def test_mapbox_and_osrm_request_neither_steps_nor_annotations():
    # Neither is read by any normalized field, and steps are the largest part of a
    # Mapbox response.
    mb = FakeTransport(resp(200, MAPBOX_BODY))
    Routing(MapboxConfig("pk"), transport=mb).route(RoutingOptions(waypoints=TWO))
    assert qget(mb.last, "steps") == ""
    assert qget(mb.last, "annotations") == ""

    os_ = FakeTransport(resp(200, OSRM_BODY))
    Routing(OsrmConfig("http://x"), transport=os_).route(RoutingOptions(waypoints=TWO))
    assert qget(os_.last, "steps") == ""
    assert qget(os_.last, "annotations") == ""


def test_here_and_tomtom_ignore_polyline_quality_without_raising():
    # Silently ignoring is the documented contract: fidelity is cosmetic, so extra
    # vertices cannot make a caller's result wrong.
    here = FakeTransport(resp(200, HERE_BODY))
    Routing(HereConfig("k"), transport=here).route(
        RoutingOptions(waypoints=TWO, polyline_quality=PolylineQuality.DETAILED)
    )
    assert qget(here.last, "overview") == ""
    assert qget(here.last, "polylineQuality") == ""

    tt = FakeTransport(resp(200, TOMTOM_BODY))
    Routing(TomTomConfig("k"), transport=tt).route(
        RoutingOptions(waypoints=TWO, polyline_quality=PolylineQuality.DETAILED)
    )
    assert qget(tt.last, "overview") == ""
    assert qget(tt.last, "polylineQuality") == ""


# ---------------------------------------------------------------------------
# traffic_mode — the Pro-tier SKU billing fix
# ---------------------------------------------------------------------------


def test_google_routing_departure_time_alone_stays_traffic_unaware():
    from datetime import datetime, timezone

    fake = FakeTransport(resp(200, GOOGLE_BODY))
    Routing(GoogleConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, departure_time=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
    )
    body = body_of(fake)
    # The departure time is still sent — it affects scheduled routing — but it no
    # longer upgrades the SKU on its own.
    assert "departureTime" in body
    assert body["routingPreference"] == "TRAFFIC_UNAWARE"


def test_google_routing_traffic_live_enables_traffic_aware():
    fake = FakeTransport(resp(200, GOOGLE_BODY))
    Routing(GoogleConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, traffic_mode=TrafficMode.LIVE)
    )
    assert body_of(fake)["routingPreference"] == "TRAFFIC_AWARE"


# Route Matrix bills PER ELEMENT, so the implicit promotion cost
# origins x destinations times more than the routing one.
def test_google_matrix_departure_time_alone_stays_traffic_unaware():
    from datetime import datetime, timezone

    cell = json.dumps({
        "originIndex": 0, "destinationIndex": 0,
        "distanceMeters": 100, "duration": "10s", "condition": "ROUTE_EXISTS",
    })
    fake = FakeTransport(resp(200, cell))
    Matrix(GoogleConfig("k"), transport=fake).matrix(
        MatrixOptions(
            origins=[LatLng(0, 0)],
            destinations=[LatLng(1, 1)],
            departure_time=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
    )
    assert body_of(fake)["routingPreference"] == "TRAFFIC_UNAWARE"


def test_google_matrix_traffic_live_enables_traffic_aware():
    cell = json.dumps({
        "originIndex": 0, "destinationIndex": 0,
        "distanceMeters": 100, "duration": "10s", "condition": "ROUTE_EXISTS",
    })
    fake = FakeTransport(resp(200, cell))
    Matrix(GoogleConfig("k"), transport=fake).matrix(
        MatrixOptions(
            origins=[LatLng(0, 0)], destinations=[LatLng(1, 1)], traffic_mode=TrafficMode.LIVE
        )
    )
    assert body_of(fake)["routingPreference"] == "TRAFFIC_AWARE"


def test_tomtom_sends_traffic_explicitly_in_both_directions():
    off = FakeTransport(resp(200, TOMTOM_BODY))
    Routing(TomTomConfig("k"), transport=off).route(RoutingOptions(waypoints=TWO))
    # Explicit false: TomTom's own default is traffic ON.
    assert qget(off.last, "traffic") == "false"

    on = FakeTransport(resp(200, TOMTOM_BODY))
    Routing(TomTomConfig("k"), transport=on).route(
        RoutingOptions(waypoints=TWO, traffic_mode=TrafficMode.LIVE)
    )
    assert qget(on.last, "traffic") == "true"


# L7: the toll modifier is free so it applies unconditionally; enabling traffic on
# findsequence2 is billable so it follows the opt-in.
@pytest.mark.parametrize("avoid_tolls,traffic,expected", [
    (False, TrafficMode.NONE, "fastest;car;traffic:disabled"),
    (False, TrafficMode.LIVE, "fastest;car;traffic:enabled"),
    (True, TrafficMode.NONE, "fastest;car;traffic:disabled;tollroad:-3"),
    (True, TrafficMode.LIVE, "fastest;car;traffic:enabled;tollroad:-3"),
])
def test_here_findsequence_mode_splits_toll_from_traffic(avoid_tolls, traffic, expected):
    seq = json.dumps({"results": [{"waypoints": [
        {"id": "start", "sequence": 0},
        {"id": "destination1", "sequence": 1},
        {"id": "end", "sequence": 2},
    ]}]})
    fake = FakeTransport(resp(200, seq), resp(200, HERE_BODY))
    Routing(HereConfig("k"), transport=fake).route(
        RoutingOptions(
            waypoints=THREE, optimize=True, avoid_tolls=avoid_tolls, traffic_mode=traffic
        )
    )
    assert qget(fake.calls[0], "mode") == expected


# ---------------------------------------------------------------------------
# include: durationWithoutTraffic
# ---------------------------------------------------------------------------


def test_google_omits_static_duration_from_the_field_mask_by_default():
    fake = FakeTransport(resp(200, GOOGLE_BODY))
    result = Routing(GoogleConfig("k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert "staticDuration" not in fake.last.headers["X-Goog-FieldMask"]
    # Not surfaced even though this fixture carries it — the field follows the
    # opt-in, not the response.
    assert result.legs[0].duration_without_traffic_seconds is None
    assert result.total_duration_without_traffic_seconds is None


def test_google_requests_and_surfaces_static_duration_when_included():
    fake = FakeTransport(resp(200, GOOGLE_BODY))
    result = Routing(GoogleConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC])
    )
    mask = fake.last.headers["X-Goog-FieldMask"]
    assert "routes.legs.staticDuration" in mask and "routes.staticDuration" in mask
    assert result.legs[0].duration_without_traffic_seconds == 280
    assert result.total_duration_without_traffic_seconds == 280


def test_tomtom_gates_compute_travel_time_for_behind_the_opt_in():
    plain = FakeTransport(resp(200, TOMTOM_BODY))
    res = Routing(TomTomConfig("k"), transport=plain).route(RoutingOptions(waypoints=TWO))
    assert qget(plain.last, "computeTravelTimeFor") == ""
    assert res.legs[0].duration_without_traffic_seconds is None

    incl = FakeTransport(resp(200, TOMTOM_BODY))
    res2 = Routing(TomTomConfig("k"), transport=incl).route(
        RoutingOptions(waypoints=TWO, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC])
    )
    assert qget(incl.last, "computeTravelTimeFor") == "all"
    assert res2.legs[0].duration_without_traffic_seconds == 280
    assert res2.total_duration_without_traffic_seconds == 280


def test_here_surfaces_base_duration_with_no_extra_request_param():
    plain = FakeTransport(resp(200, HERE_BODY))
    res = Routing(HereConfig("k"), transport=plain).route(RoutingOptions(waypoints=TWO))
    assert res.legs[0].duration_without_traffic_seconds is None
    # HERE ships baseDuration inside the summary already requested.
    assert qget(plain.last, "return") == "polyline,summary"

    incl = FakeTransport(resp(200, HERE_BODY))
    res2 = Routing(HereConfig("k"), transport=incl).route(
        RoutingOptions(waypoints=TWO, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC])
    )
    assert res2.legs[0].duration_without_traffic_seconds == 280
    assert res2.total_duration_without_traffic_seconds == 280
    assert qget(incl.last, "return") == "polyline,summary"


# The never-synthesized rule: if the provider does not return the value, the field
# stays None even though the token was passed. Absence is information.
@pytest.mark.parametrize("cfg,body", [
    (MapboxConfig("pk"), MAPBOX_BODY),
    (OsrmConfig("http://x"), OSRM_BODY),
])
def test_mapbox_and_osrm_leave_the_field_none(cfg, body):
    res = Routing(cfg, transport=FakeTransport(resp(200, body))).route(
        RoutingOptions(waypoints=TWO, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC])
    )
    assert res.legs[0].duration_without_traffic_seconds is None
    assert res.total_duration_without_traffic_seconds is None
    # The traffic-aware duration is still there — only the optional extra is not.
    assert res.legs[0].duration_seconds == 300


def test_google_leaves_the_field_none_when_the_vendor_omits_it():
    res = Routing(GoogleConfig("k"), transport=FakeTransport(resp(200, GOOGLE_BODY_NO_STATIC))).route(
        RoutingOptions(waypoints=TWO, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC])
    )
    assert res.legs[0].duration_without_traffic_seconds is None
    assert res.total_duration_without_traffic_seconds is None


def test_here_omits_the_total_when_only_some_sections_carry_base_duration():
    body = json.dumps({"routes": [{"sections": [
        {"polyline": "BGwl_lgDo-6-T", "summary": {"length": 100, "duration": 10, "baseDuration": 9}},
        {"polyline": "BGwl_lgDo-6-T", "summary": {"length": 100, "duration": 10}},
    ]}]})
    res = Routing(HereConfig("k"), transport=FakeTransport(resp(200, body))).route(
        RoutingOptions(waypoints=TWO, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC])
    )
    # Under-reporting a total is worse than omitting it.
    assert res.total_duration_without_traffic_seconds is None
    assert res.legs[0].duration_without_traffic_seconds == 9
    assert res.legs[1].duration_without_traffic_seconds is None


# ---------------------------------------------------------------------------
# OsrmConfig.supported_exclude_classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs,exclude_class", [
    ({"avoid_tolls": True}, "toll"),
    ({"avoid_ferries": True}, "ferry"),
    ({"avoid_highways": True}, "motorway"),
])
def test_osrm_rejects_an_undeclared_exclude_class(kwargs, exclude_class):
    fake = FakeTransport(resp(200, OSRM_BODY))
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("http://x"), transport=fake).route(
            RoutingOptions(waypoints=TWO, **kwargs)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_OPTION
    assert exclude_class in ei.value.provider_message
    assert "supported_exclude_classes" in ei.value.provider_message
    assert fake.calls == []


def test_osrm_sends_exclude_for_declared_classes():
    fake = FakeTransport(resp(200, OSRM_BODY))
    Routing(
        OsrmConfig("http://x", supported_exclude_classes=("toll", "ferry")), transport=fake
    ).route(RoutingOptions(waypoints=TWO, avoid_tolls=True, avoid_ferries=True))
    assert qget(fake.last, "exclude") == "toll,ferry"


def test_osrm_still_rejects_a_flag_outside_the_declared_classes():
    with pytest.raises(ConnectorError) as ei:
        Routing(
            OsrmConfig("http://x", supported_exclude_classes=("ferry",)),
            transport=FakeTransport(resp(200, OSRM_BODY)),
        ).route(RoutingOptions(waypoints=TWO, avoid_tolls=True))
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_OPTION


def test_osrm_omits_exclude_when_no_avoid_flag_is_set():
    fake = FakeTransport(resp(200, OSRM_BODY))
    Routing(OsrmConfig("http://x", supported_exclude_classes=("toll",)), transport=fake).route(
        RoutingOptions(waypoints=TWO)
    )
    assert qget(fake.last, "exclude") == ""


# ---------------------------------------------------------------------------
# no_route, from live-captured vendor bodies
# ---------------------------------------------------------------------------


def test_google_no_route_is_200_with_the_routes_key_absent():
    with pytest.raises(ConnectorError) as ei:
        Routing(GoogleConfig("k"), transport=FakeTransport(resp(200, "{}"))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE


def test_here_no_route_surfaces_the_critical_notice():
    body = json.dumps({
        "notices": [{
            "title": "Route calculation failed: Couldn't match origin.",
            "code": "couldNotMatchOrigin",
            "severity": "critical",
        }],
        "routes": [],
    })
    with pytest.raises(ConnectorError) as ei:
        Routing(HereConfig("k"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    assert "couldNotMatchOrigin" in ei.value.provider_message


@pytest.mark.parametrize("status,code", [(200, "NoRoute"), (422, "NoRoute"), (200, "NoSegment")])
def test_mapbox_no_route_is_driven_by_the_envelope_code(status, code):
    with pytest.raises(ConnectorError) as ei:
        Routing(
            MapboxConfig("pk"), transport=FakeTransport(resp(status, json.dumps({"code": code})))
        ).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.NO_ROUTE


# THE path that actually fires in production: OSRM serves every non-Ok envelope
# code with a 4xx, so the code — not the status — must be read.
@pytest.mark.parametrize("code,expected", [
    ("NoRoute", ProviderCode.NO_ROUTE),
    ("NoSegment", ProviderCode.NO_ROUTE),
    ("InvalidOptions", ProviderCode.INVALID_REQUEST),
    ("InvalidValue", ProviderCode.INVALID_REQUEST),
])
def test_osrm_classifies_a_400_by_its_envelope_code(code, expected):
    body = json.dumps({"code": code, "message": "vendor text"})
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("http://x"), transport=FakeTransport(resp(400, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == expected
    # The real status is preserved.
    assert ei.value.status_code == 400


def test_osrm_keeps_profile_not_configured_ahead_of_no_route():
    body = json.dumps({"code": "NoRoute", "message": "profile not found"})
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("http://x"), transport=FakeTransport(resp(400, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.PROFILE_NOT_CONFIGURED


@pytest.mark.parametrize("code", ["MAP_MATCHING_FAILURE", "NO_ROUTE_FOUND"])
def test_tomtom_no_route_is_a_400_with_a_detailed_error_code(code):
    body = json.dumps({"detailedError": {"code": code, "message": "Engine error"}})
    with pytest.raises(ConnectorError) as ei:
        Routing(TomTomConfig("k"), transport=FakeTransport(resp(400, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    assert ei.value.status_code == 400


def test_tomtom_keeps_auth_failures_ahead_of_no_route():
    body = json.dumps({"detailedError": {"code": "MAP_MATCHING_FAILURE"}})
    with pytest.raises(ConnectorError) as ei:
        Routing(TomTomConfig("k"), transport=FakeTransport(resp(403, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.AUTH_FAILED


def test_esri_no_route_is_an_in_body_400_naming_an_unlocated_stop():
    body = json.dumps({"error": {
        "code": 400,
        "message": "Unable to complete operation.",
        "details": ['Location "Location 1" in "Stops" is unlocated.  Need at least 2 valid stops.'],
    }})
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE


# The guard against over-reaching: a genuine 400 must NOT become no_route just
# because it shares Esri's in-body error code.
def test_esri_in_body_400_without_an_unlocated_stop_stays_invalid_request():
    body = json.dumps({"error": {
        "code": 400,
        "message": "Unable to complete operation.",
        "details": ["Invalid value for parameter travelMode."],
    }})
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


@pytest.mark.parametrize("cfg,body", [
    (MapboxConfig("pk"), json.dumps({
        "code": "Ok",
        "routes": [{"geometry": "", "legs": [], "distance": 5000, "duration": 300}],
        "waypoints": [{"waypoint_index": 0}, {"waypoint_index": 1}],
    })),
    (OsrmConfig("http://x"), json.dumps({
        "code": "Ok",
        "routes": [{"geometry": "abc", "legs": [], "distance": 5000, "duration": 300}],
    })),
])
def test_a_route_with_no_legs_raises_no_route(cfg, body):
    with pytest.raises(ConnectorError) as ei:
        Routing(cfg, transport=FakeTransport(resp(200, body))).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    # Totals looked plausible; there was simply nothing to iterate.
    assert "no legs" in ei.value.provider_message


# ---------------------------------------------------------------------------
# timeout classification
# ---------------------------------------------------------------------------


def test_a_transport_timeout_is_classified_as_timeout():
    class TimingOutTransport:
        def send(self, request):
            raise TimeoutError("timed out")

    with pytest.raises(ConnectorError) as ei:
        Routing(GoogleConfig("k"), transport=TimingOutTransport()).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.TIMEOUT


def test_a_wrapped_transport_timeout_is_still_classified_as_timeout():
    # urllib surfaces a read timeout as URLError(TimeoutError(...)).
    class WrappedTransport:
        def send(self, request):
            raise OSError("urlopen error") from TimeoutError("timed out")

    with pytest.raises(ConnectorError) as ei:
        Routing(GoogleConfig("k"), transport=WrappedTransport()).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.TIMEOUT


def test_a_non_timeout_transport_failure_stays_provider_unavailable():
    class BrokenTransport:
        def send(self, request):
            raise ConnectionRefusedError("connection refused")

    with pytest.raises(ConnectorError) as ei:
        Routing(GoogleConfig("k"), transport=BrokenTransport()).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.PROVIDER_UNAVAILABLE
