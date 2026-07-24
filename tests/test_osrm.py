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
    t = FakeTransport(resp(200, '{"code":"Ok","trips":[{"geometry":"_p~iF~ps|U","legs":[],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1}]}'))
    r = Routing(OsrmConfig("http://x"), transport=t)
    r.route(RoutingOptions(waypoints=TWO, optimize=True))
    assert qget(t.last, "source") == "first"
    assert qget(t.last, "destination") == "last"
    assert qget(t.last, "roundtrip") == "false"


def test_trip_waypoint_order():
    # OSRM Trip service returns route objects under `trips`, not `routes`.
    fake = FakeTransport(resp(200, '{"code":"Ok","trips":[{"geometry":"_p~iF~ps|U","legs":[],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":2},{"waypoint_index":1}]}'))
    r = Routing(OsrmConfig("http://x"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], is_round_trip=True))
    assert res.waypoint_order == [0, 2, 1]
    assert path_of(fake.last) == "/trip/v1/driving/1,1;2,2;3,3"


def test_in_body_error():
    fake = FakeTransport(resp(200, '{"code":"NoRoute","message":"Impossible route: profile not found"}'))
    r = Routing(OsrmConfig("http://x"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.PROFILE_NOT_CONFIGURED and ei.value.status_code is None

    fake2 = FakeTransport(resp(200, '{"code":"NoRoute","message":"no route"}'))
    with pytest.raises(ConnectorError) as ei2:
        Routing(OsrmConfig("http://x"), transport=fake2).route(RoutingOptions(waypoints=TWO))
    assert ei2.value.provider_code == ProviderCode.INVALID_REQUEST


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
