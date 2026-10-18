from __future__ import annotations

import pytest
from helpers import FakeTransport, path_of, qget, resp

from thinwrap.location import (
    ConnectorError,
    LatLng,
    Matrix,
    MatrixOptions,
    OsrmConfig,
    ProviderCode,
    Routing,
    RoutingOptions,
)

TWO = [LatLng(40, -74), LatLng(41, -73)]


def test_routing():
    fake = FakeTransport(resp(200, '{"code":"Ok","routes":[{"geometry":"_p~iF~ps|U_ulLnnqC","legs":[{"distance":1000,"duration":600}],"distance":1000,"duration":600}]}'))
    r = Routing(OsrmConfig("http://localhost:5000"), transport=fake)
    res = r.route(RoutingOptions(waypoints=TWO))
    assert res.total_distance_meters == 1000 and res.polyline == "_p~iF~ps|U_ulLnnqC"
    assert path_of(fake.last) == "/route/v1/driving/-74,40;-73,41"


def test_requires_base_url():
    r = Routing(OsrmConfig(""), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


@pytest.mark.parametrize("opts_kwargs,want", [
    ({"avoid_tolls": True}, ProviderCode.UNSUPPORTED_OPTION),
    ({"avoid_ferries": True}, ProviderCode.UNSUPPORTED_OPTION),
])
def test_preflight(opts_kwargs, want):
    r = Routing(OsrmConfig("http://x"), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO, **opts_kwargs))
    assert ei.value.provider_code == want


def test_plain_optimize_remaps_to_first_last():
    # Plain optimize would be source=any/destination=any/roundtrip=false — the
    # combo OSRM rejects with HTTP 400. It must be remapped to first/last.
    t = FakeTransport(resp(200, '{"code":"Ok","trips":[{"geometry":"_p~iF~ps|U","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1}]}'))
    r = Routing(OsrmConfig("http://x"), transport=t)
    r.route(RoutingOptions(waypoints=TWO, optimize=True))
    assert qget(t.last, "source") == "first"
    assert qget(t.last, "destination") == "last"
    assert qget(t.last, "roundtrip") == "false"


def test_trip_waypoint_order():
    # OSRM Trip service returns route objects under `trips`, not `routes`.
    fake = FakeTransport(resp(200, '{"code":"Ok","trips":[{"geometry":"_p~iF~ps|U","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":2},{"waypoint_index":1}]}'))
    r = Routing(OsrmConfig("http://x"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], is_round_trip=True))
    assert res.waypoint_order == [0, 2, 1]
    assert path_of(fake.last) == "/trip/v1/driving/1,1;2,2;3,3"


def test_in_body_error():
    fake = FakeTransport(resp(200, '{"code":"NoRoute","message":"Impossible route: profile not found"}'))
    r = Routing(OsrmConfig("http://x"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    # The in-body path is only reachable on a 2xx, so it reports the REAL status
    # instead of None. Nulling it made an answered request look like a transport
    # failure, and a consumer distinguishing the two had to read the
    # deliberately-sanitized cause.
    assert ei.value.provider_code == ProviderCode.PROFILE_NOT_CONFIGURED and ei.value.status_code == 200

    # A generic NoRoute is no_route: the request was well-formed and OSRM
    # answered; there is simply no connecting route. A missing profile is never
    # inferred from a bare NoRoute — it must be stated in the message (above).
    fake2 = FakeTransport(resp(200, '{"code":"NoRoute","message":"no route"}'))
    with pytest.raises(ConnectorError) as ei2:
        Routing(OsrmConfig("http://x"), transport=fake2).route(RoutingOptions(waypoints=TWO))
    assert ei2.value.provider_code == ProviderCode.NO_ROUTE


def test_matrix():
    fake = FakeTransport(resp(200, '{"code":"Ok","durations":[[0,60],[60,0]],"distances":[[0,1000],[1000,0]]}'))
    m = Matrix(OsrmConfig("http://x"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert len(res.cells) == 4
    assert res.cells[1].distance_meters == 1000 and res.cells[1].duration_seconds == 60
    assert qget(fake.last, "annotations") == "duration,distance"


def test_matrix_in_body_error():
    fake = FakeTransport(resp(200, '{"code":"NoTable","message":"no table"}'))
    m = Matrix(OsrmConfig("http://x"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_matrix_dimension_mismatch():
    fake = FakeTransport(resp(200, '{"code":"Ok","durations":[[0,60]],"distances":[[0,1000]]}'))
    m = Matrix(OsrmConfig("http://x"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert ei.value.provider_code == ProviderCode.UNKNOWN


@pytest.mark.parametrize(
    "body",
    ['{"code":"Ok","routes":[]}', '{"code":"Ok","routes":[],"trips":[]}'],
)
def test_route_ok_with_empty_routes_is_no_route(body):
    # An answered request with nothing to return. Unclassified it fell through to
    # UNKNOWN with the message "OSRM returned code: Ok", which reads like a
    # success — so a consumer branching on no_route had to keep a status heuristic
    # beside it.
    fake = FakeTransport(resp(200, body))
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig(base_url="http://localhost:5000"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    # The real status, not None — this path is only reachable on a 2xx.
    assert ei.value.status_code == 200
    assert "no routes" in ei.value.provider_message


_TRIP_OK = (
    '{"code":"Ok","trips":[{"geometry":"_p~iF~ps|U","legs":[{"distance":5,"duration":6},'
    '{"distance":7,"duration":8}],"distance":12,"duration":14}],'
    '"waypoints":[{"waypoint_index":0},{"waypoint_index":2},{"waypoint_index":1}]}'
)


# OSRM >= 5.27 (trip.cpp IsSupportedParameterCombination = roundtrip || fixed_start
# || fixed_end) answers first/any and any/last with roundtrip=false — live 200 Ok on
# router.project-osrm.org — so a single fixed endpoint is sent as asked.
@pytest.mark.parametrize("fixed,source,destination", [
    ({"optimize_fixed_origin": True}, "first", "any"),
    ({"optimize_fixed_destination": True}, "any", "last"),
])
def test_open_trip_with_a_single_fixed_endpoint_is_sent_as_is(fixed, source, destination):
    t = FakeTransport(resp(200, _TRIP_OK))
    r = Routing(OsrmConfig("https://x"), transport=t)
    res = r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True, **fixed))
    assert path_of(t.last).startswith("/trip/v1/")
    assert (qget(t.last, "source"), qget(t.last, "destination"), qget(t.last, "roundtrip")) == (source, destination, "false")
    assert res.waypoint_order == [0, 2, 1]


def test_open_trip_single_fixed_endpoint_without_plain_optimize_still_uses_trip():
    t = FakeTransport(resp(200, _TRIP_OK))
    Routing(OsrmConfig("https://x"), transport=t).route(
        RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize_fixed_origin=True)
    )
    assert path_of(t.last).startswith("/trip/v1/")
    assert (qget(t.last, "source"), qget(t.last, "destination")) == ("first", "any")


def test_not_implemented_from_an_older_server_is_unsupported_option():
    # A pre-5.27 server (or any/any, were it sent) answers 400 NotImplemented.
    body = '{"code":"NotImplemented","message":"This request is not supported"}'
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("https://x"), transport=FakeTransport(resp(400, body))).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize_fixed_origin=True)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_OPTION
    assert ei.value.status_code == 400


def test_invalid_base_url_message_does_not_echo_userinfo():
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("alice:s3cret@osrm.internal"), transport=FakeTransport()).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert "s3cret" not in str(ei.value) and "alice" not in str(ei.value)
    assert "osrm.internal" in str(ei.value)


def test_transport_failure_message_does_not_echo_userinfo():
    # The stdlib parses https://user:pass@host as a host of "user:pass@host" and
    # fails with "nonnumeric port: 'pass@host'" — the password without its scheme.
    from thinwrap.location.transport import UrllibTransport

    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("https://alice:s3cret@osrm.invalid"), transport=UrllibTransport()).route(RoutingOptions(waypoints=TWO))
    assert "s3cret" not in str(ei.value)
    assert "s3cret" not in str(ei.value.provider_message) and "s3cret" not in str(ei.value.cause)


def test_default_transport_refuses_http_with_invalid_request():
    from thinwrap.location.transport import UrllibTransport

    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("http://alice:s3cret@localhost:5000"), transport=UrllibTransport()).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert "HTTPS-only" in str(ei.value) and "Transport" in str(ei.value)
    assert "s3cret" not in str(ei.value) and "localhost" not in str(ei.value)


def test_config_repr_masks_base_url_userinfo():
    cfg = OsrmConfig("https://alice:s3cret@osrm.internal/osrm")
    assert "s3cret" not in repr(cfg) and "alice" not in repr(cfg)
    assert "osrm.internal/osrm" in repr(cfg)


@pytest.mark.parametrize("kwargs,source,destination,roundtrip", [
    # true/first/any — a supported round-trip combination.
    ({"is_round_trip": True, "optimize_fixed_origin": True}, "first", "any", "true"),
    # false/first/last — the only supported open combination.
    ({"optimize_fixed_origin": True, "optimize_fixed_destination": True}, "first", "last", "false"),
])
def test_supported_trip_endpoint_combinations_are_sent(kwargs, source, destination, roundtrip):
    t = FakeTransport(resp(200, '{"code":"Ok","trips":[{"geometry":"_p~iF~ps|U","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1}]}'))
    Routing(OsrmConfig("http://x"), transport=t).route(RoutingOptions(waypoints=TWO, optimize=True, **kwargs))
    assert path_of(t.last).startswith("/trip/v1/")
    assert (qget(t.last, "source"), qget(t.last, "destination"), qget(t.last, "roundtrip")) == (source, destination, roundtrip)


# --- matrix honours the operator-declared toll exclude class, like routing -----

_TABLE_OK = '{"code":"Ok","durations":[[60]],"distances":[[1000]]}'


def test_matrix_avoid_tolls_sends_exclude_when_the_class_is_declared():
    fake = FakeTransport(resp(200, _TABLE_OK))
    Matrix(OsrmConfig("http://x", supported_exclude_classes=("toll",)), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], avoid_tolls=True)
    )
    assert qget(fake.last, "exclude") == "toll"


def test_matrix_avoid_tolls_without_the_class_is_unsupported_option():
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Matrix(OsrmConfig("http://x", supported_exclude_classes=("ferry",)), transport=fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], avoid_tolls=True)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_OPTION
    assert fake.calls == []


def test_matrix_without_avoid_tolls_sends_no_exclude():
    fake = FakeTransport(resp(200, _TABLE_OK))
    Matrix(OsrmConfig("http://x", supported_exclude_classes=("toll",)), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
    )
    assert qget(fake.last, "exclude") == ""


# --- error codes: envelope codes decide on both paths, with the HTTP status ----

def test_trip_no_trips_on_a_400_is_no_route():
    fake = FakeTransport(resp(400, '{"code":"NoTrips","message":"No trip visiting all destinations possible."}'))
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("http://x"), transport=fake).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True)
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    assert ei.value.status_code == 400


@pytest.mark.parametrize("status", [200, 400])
def test_matrix_no_segment_is_no_route_with_status(status):
    fake = FakeTransport(resp(status, '{"code":"NoSegment","message":"Could not find a matching segment"}'))
    with pytest.raises(ConnectorError) as ei:
        Matrix(OsrmConfig("http://x"), transport=fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    assert ei.value.status_code == status


def test_matrix_in_body_error_carries_the_http_status():
    fake = FakeTransport(resp(200, '{"code":"NoTable","message":"no table"}'))
    with pytest.raises(ConnectorError) as ei:
        Matrix(OsrmConfig("http://x"), transport=fake).matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)]))
    assert ei.value.status_code == 200


# --- malformed 2xx bodies never become zero-valued successes --------------------

@pytest.mark.parametrize("body", [
    '{"code":"Ok","routes":[null]}',
    '{"code":"Ok","routes":[{"distance":1,"duration":1,"geometry":"","legs":[null]}]}',
    '{"code":"Ok","routes":[{"duration":1,"geometry":"","legs":[{"distance":1,"duration":1}]}]}',
    '{"code":"Ok","routes":[{"distance":1,"duration":1,"geometry":"","legs":[{"distance":1}]}]}',
])
def test_routing_malformed_route_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Routing(OsrmConfig("http://x"), transport=FakeTransport(resp(200, body))).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_matrix_non_list_row_is_unknown():
    fake = FakeTransport(resp(200, '{"code":"Ok","durations":[5],"distances":[[1]]}'))
    with pytest.raises(ConnectorError) as ei:
        Matrix(OsrmConfig("http://x"), transport=fake).matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)]))
    assert ei.value.provider_code == ProviderCode.UNKNOWN
