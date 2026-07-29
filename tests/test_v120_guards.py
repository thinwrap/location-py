"""Phase-1 guards for the 1.2.0 scope: waypoint_order permutation validation,
Null-Island candidate skipping, OSRM baseUrl scheme validation, and the Mapbox
geometries/decoder coupling.
"""

from __future__ import annotations

import pytest
from helpers import FakeTransport, path_of, resp

from thinwrap.location import (
    ConnectorError,
    EsriConfig,
    Geocoding,
    GeocodeOptions,
    GoogleConfig,
    HereConfig,
    LatLng,
    MapboxConfig,
    OsrmConfig,
    Passthrough,
    ProviderCode,
    Routing,
    RoutingOptions,
    TomTomConfig,
)
from thinwrap.location._waypoint_order import (
    invert_waypoint_positions,
    is_complete_waypoint_order,
)
from thinwrap.location.polyline import decode_polyline, encode_polyline

FOUR = [LatLng(0, 0), LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
THREE = [LatLng(0, 0), LatLng(1, 1), LatLng(2, 2)]
TWO = [LatLng(38.5, -120.2), LatLng(40.7, -120.95)]


# ---------------------------------------------------------------------------
# the shared helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order,length,expected", [
    ([0, 2, 1, 3], 4, True),
    ([0, 1, 2], 3, True),
    ([], 0, True),
    ([0, 1, 2], 4, False),           # short
    ([0, 1, 2, 3], 3, False),        # long
    ([0, 1, 1, 3], 4, False),        # duplicates
    # Google answers [-1] when it declines to optimize; projected to absolute
    # input indices that becomes [0, 0, N-1] — the shape the consumer reported
    # as corrupting their reordering.
    ([0, 0, 3], 4, False),
    ([0, -1, 2], 3, False),
    ([0, 1, 9], 3, False),
    ([0, 1.5, 2], 3, False),
    ([0, "1", 2], 3, False),
    ([0, None, 2], 3, False),
    # bool is an int subclass in Python — True must not pass as index 1.
    ([0, True, 2], 3, False),
])
def test_is_complete_waypoint_order(order, length, expected):
    assert is_complete_waypoint_order(order, length) is expected


def test_invert_waypoint_positions_inverts():
    # Input waypoint 0 is visited 1st, 1 is visited 3rd, 2 is visited 2nd.
    assert invert_waypoint_positions([0, 2, 1], 3) == [0, 2, 1]
    # A true inverse, not a copy.
    assert invert_waypoint_positions([0, 2, 3, 1], 4) == [0, 3, 1, 2]
    assert invert_waypoint_positions([0, 1, 2, 3], 4) == [0, 1, 2, 3]


@pytest.mark.parametrize("positions,length", [
    ([0, 1], 3),              # truncated
    ([0, 1, 2, 3], 3),        # too long
    # Before the shared helper, a duplicate position overwrote one slot and left
    # another at its filler value, reading as a real index.
    ([0, 0, 2], 3),
    ([0, -1, 2], 3),
    ([0, 1, 3], 3),
    ([0, None, 2], 3),
    ([0, 1.5, 2], 3),
    ([0, True, 2], 3),
])
def test_invert_waypoint_positions_rejects(positions, length):
    assert invert_waypoint_positions(positions, length) is None


# ---------------------------------------------------------------------------
# waypoint_order permutation guards, per connector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("optimized_index", [
    "[-1]",           # the reported sentinel
    "[-1,-1]",
    "[0]",            # short
    "[0,0]",          # duplicates
    "[1,9]",          # out of range
    '[0,"x"]',        # non-numeric
])
def test_google_omits_corrupt_waypoint_order(optimized_index):
    body = (
        '{"routes":[{"legs":[{"distanceMeters":5000,"duration":"300s"}],'
        '"distanceMeters":8000,"duration":"480s","polyline":{"encodedPolyline":"p"},'
        '"optimizedIntermediateWaypointIndex":' + optimized_index + "}]}"
    )
    r = Routing(GoogleConfig("k"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=FOUR, optimize=True))
    assert res.waypoint_order is None
    # The route itself is still returned.
    assert res.total_distance_meters == 8000


def test_google_keeps_valid_round_trip_order():
    body = (
        '{"routes":[{"legs":[{"distanceMeters":5000,"duration":"300s"}],'
        '"distanceMeters":8000,"duration":"480s","polyline":{"encodedPolyline":"p"},'
        '"optimizedIntermediateWaypointIndex":[2,0,1]}]}'
    )
    r = Routing(GoogleConfig("k"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=FOUR, is_round_trip=True))
    # For a round trip every non-origin waypoint is an intermediate, so origin +
    # projected intermediates already covers all four inputs.
    assert res.waypoint_order == [0, 3, 1, 2]


@pytest.mark.parametrize("optimized", [
    '[{"providedIndex":0,"optimizedIndex":0}]',                                          # short
    '[{"providedIndex":0,"optimizedIndex":0},{"providedIndex":0,"optimizedIndex":1}]',   # duplicate
    '[{"providedIndex":9,"optimizedIndex":0},{"providedIndex":0,"optimizedIndex":1}]',   # out of range
    '[{"providedIndex":-1,"optimizedIndex":0},{"providedIndex":0,"optimizedIndex":1}]',  # sentinel
])
def test_tomtom_omits_corrupt_waypoint_order(optimized):
    legs = '"legs":[{"summary":{"lengthInMeters":5000,"travelTimeInSeconds":300},"points":[{"latitude":1,"longitude":1}]}]'
    body = (
        '{"routes":[{"summary":{"lengthInMeters":8000,"travelTimeInSeconds":480},'
        + legs
        + '}],"optimizedWaypoints":'
        + optimized
        + "}"
    )
    r = Routing(TomTomConfig("k"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=FOUR, optimize=True))
    assert res.waypoint_order is None
    assert res.total_distance_meters == 8000


@pytest.mark.parametrize("waypoints", [
    '[{"waypoint_index":0},{"waypoint_index":0},{"waypoint_index":2}]',  # duplicate
    '[{"waypoint_index":0},{"waypoint_index":1}]',                       # truncated
    '[{"waypoint_index":0},{"waypoint_index":1},{"waypoint_index":7}]',  # out of range
    '[{"waypoint_index":0},{},{"waypoint_index":2}]',                    # missing
])
def test_osrm_trip_omits_corrupt_waypoint_order(waypoints):
    body = (
        '{"code":"Ok","trips":[{"geometry":"abc","legs":[{"distance":1500,"duration":90}],'
        '"distance":4000,"duration":240}],"waypoints":' + waypoints + "}"
    )
    r = Routing(OsrmConfig("http://localhost:5000"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=THREE, optimize=True))
    assert res.waypoint_order is None
    assert res.total_distance_meters == 4000


# ---------------------------------------------------------------------------
# Null-Island guards: a row without real coordinates is skipped, never (0,0)
# ---------------------------------------------------------------------------


def _assert_no_null_island(candidates, want):
    assert len(candidates) == want, candidates
    for c in candidates:
        assert not (c.location.lat == 0 and c.location.lng == 0), f"fabricated (0,0): {c}"


def test_google_geocode_skips_rows_without_coordinates():
    body = (
        '{"status":"OK","results":['
        '{"formatted_address":"no geometry"},'
        '{"formatted_address":"empty geometry","geometry":{}},'
        '{"formatted_address":"lat only","geometry":{"location":{"lat":1.5}}},'
        '{"formatted_address":"lat null","geometry":{"location":{"lat":null,"lng":2.5}}},'
        '{"formatted_address":"good","geometry":{"location":{"lat":1.5,"lng":2.5}},"place_id":"p"}'
        "]}"
    )
    g = Geocoding(GoogleConfig("k"), transport=FakeTransport(resp(200, body)))
    res = g.geocode(GeocodeOptions(address="x"))
    _assert_no_null_island(res.candidates, 1)
    assert res.candidates[0].location == LatLng(1.5, 2.5)


def test_google_geocode_drops_partial_viewport():
    body = (
        '{"status":"OK","results":[{"formatted_address":"x","geometry":'
        '{"location":{"lat":1.5,"lng":2.5},"viewport":{"northeast":{"lat":1,"lng":2}}}}]}'
    )
    g = Geocoding(GoogleConfig("k"), transport=FakeTransport(resp(200, body)))
    res = g.geocode(GeocodeOptions(address="x"))
    assert len(res.candidates) == 1
    # No half-populated viewport with a (0,0) corner.
    assert res.candidates[0].viewport is None


def test_here_geocode_skips_items_without_position():
    body = '{"items":[{"title":"no position","id":"a"},{"title":"good","id":"b","position":{"lat":1.5,"lng":2.5}}]}'
    g = Geocoding(HereConfig("k"), transport=FakeTransport(resp(200, body)))
    res = g.geocode(GeocodeOptions(address="x"))
    _assert_no_null_island(res.candidates, 1)


def test_tomtom_geocode_skips_results_without_position():
    body = (
        '{"results":[{"id":"a","address":{"freeformAddress":"no position"}},'
        '{"id":"b","address":{"freeformAddress":"good"},"position":{"lat":1.5,"lon":2.5}}]}'
    )
    g = Geocoding(TomTomConfig("k"), transport=FakeTransport(resp(200, body)))
    res = g.geocode(GeocodeOptions(address="x"))
    _assert_no_null_island(res.candidates, 1)


def test_esri_geocode_skips_candidates_without_location():
    body = (
        '{"candidates":[{"address":"no location"},{"address":"empty location","location":{}},'
        '{"address":"good","location":{"x":2.5,"y":1.5}}]}'
    )
    g = Geocoding(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body)))
    res = g.geocode(GeocodeOptions(address="x"))
    _assert_no_null_island(res.candidates, 1)


# ---------------------------------------------------------------------------
# OSRM baseUrl validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", [
    "router.example.com",
    "//router.example.com",
    "ftp://router.example.com",
    "/osrm",
])
def test_osrm_rejects_schemeless_base_url(base_url):
    fake = FakeTransport(resp(200, "{}"))
    r = Routing(OsrmConfig(base_url), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=THREE))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert ei.value.provider_message == "OSRM baseUrl must start with http:// or https://"
    # Rejected before any HTTP work.
    assert fake.calls == []


@pytest.mark.parametrize("base_url,want_path", [
    # A reverse-proxied OSRM under a path prefix is a normal deployment.
    ("https://router.example.com/osrm", "/osrm/route/v1/driving/-74,40;-73,41"),
    ("https://router.example.com/", "/route/v1/driving/-74,40;-73,41"),
    ("https://router.example.com///", "/route/v1/driving/-74,40;-73,41"),
])
def test_osrm_allows_path_prefix_and_strips_trailing_slashes(base_url, want_path):
    body = '{"code":"Ok","routes":[{"geometry":"_p~iF~ps|U","legs":[{"distance":1000,"duration":600}],"distance":1000,"duration":600}]}'
    fake = FakeTransport(resp(200, body))
    r = Routing(OsrmConfig(base_url), transport=fake)
    r.route(RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)]))
    assert path_of(fake.last) == want_path
    assert "//route/v1" not in path_of(fake.last)


# ---------------------------------------------------------------------------
# Mapbox geometries / decoder coupling
# ---------------------------------------------------------------------------


def test_mapbox_treats_overridden_polyline_as_precision5():
    precision5 = encode_polyline(TWO)
    import json as _json

    body = _json.dumps({
        "code": "Ok",
        "routes": [{
            "geometry": precision5,
            "legs": [{"distance": 1, "duration": 1}],
            "distance": 1,
            "duration": 1,
        }],
    })
    r = Routing(MapboxConfig("pk.test"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=TWO, passthrough=Passthrough(query={"geometries": "polyline"})))
    # Emitted verbatim — NOT run through the precision-6 decoder, which would
    # have divided every coordinate by 10.
    assert res.polyline == precision5
    decoded = decode_polyline(res.polyline)
    assert abs(decoded[0].lat - 38.5) < 5e-5
    assert decoded[0].lat > 30


def test_mapbox_encodes_geojson_geometry():
    body = (
        '{"code":"Ok","routes":[{"geometry":{"type":"LineString",'
        '"coordinates":[[-120.2,38.5],[-120.95,40.7]]},'
        '"legs":[{"distance":1,"duration":1}],"distance":1,"duration":1}]}'
    )
    r = Routing(MapboxConfig("pk.test"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=TWO, passthrough=Passthrough(query={"geometries": "geojson"})))
    assert res.polyline == encode_polyline(TWO)


def test_mapbox_malformed_geojson_yields_empty_polyline():
    body = (
        '{"code":"Ok","routes":[{"geometry":{"type":"LineString","coordinates":[["x","y"]]},'
        '"legs":[{"distance":1,"duration":1}],"distance":5000,"duration":1}]}'
    )
    r = Routing(MapboxConfig("pk.test"), transport=FakeTransport(resp(200, body)))
    res = r.route(RoutingOptions(waypoints=TWO, passthrough=Passthrough(query={"geometries": "geojson"})))
    assert res.polyline == ""
    # The route itself is still returned.
    assert res.total_distance_meters == 5000
