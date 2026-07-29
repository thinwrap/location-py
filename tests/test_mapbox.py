from __future__ import annotations

import math

import pytest
from helpers import FakeTransport, body_json, path_of, qget, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    Geocoding,
    GeocodeOptions,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    MapboxConfig,
    Matrix,
    MatrixOptions,
    ProviderCode,
    Routing,
    RoutingOptions,
    decode_polyline,
)

TWO = [LatLng(1, 1), LatLng(2, 2)]


def encode_p6(coords):
    out = []
    prev_lat = prev_lng = 0

    def enc(v):
        x = ~(v << 1) if v < 0 else (v << 1)
        while x >= 0x20:
            out.append(chr((0x20 | (x & 0x1F)) + 63))
            x >>= 5
        out.append(chr(x + 63))

    for c in coords:
        lat = math.floor(c.lat * 1e6 + 0.5)
        lng = math.floor(c.lng * 1e6 + 0.5)
        enc(lat - prev_lat)
        enc(lng - prev_lng)
        prev_lat, prev_lng = lat, lng
    return "".join(out)


def test_routing_directions():
    pts = [LatLng(38.5, -120.2), LatLng(40.7, -120.95)]
    geom = encode_p6(pts)
    fake = FakeTransport(resp(200, '{"code":"Ok","routes":[{"geometry":"' + geom + '","legs":[{"distance":1000,"duration":600}],"distance":1000,"duration":600}]}'))
    r = Routing(MapboxConfig("tok"), transport=fake)
    res = r.route(RoutingOptions(waypoints=pts))
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 600
    dec = decode_polyline(res.polyline)
    assert all(abs(a.lat - b.lat) < 1e-5 and abs(a.lng - b.lng) < 1e-5 for a, b in zip(dec, pts))
    assert path_of(fake.last).startswith("/directions/v5/mapbox/driving/")
    assert qget(fake.last, "access_token") == "tok" and qget(fake.last, "geometries") == "polyline6"


def test_routing_optimized():
    pts = [LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
    geom = encode_p6(pts)
    fake = FakeTransport(resp(200, '{"code":"Ok","trips":[{"geometry":"' + geom + '","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":2},{"waypoint_index":1}]}'))
    r = Routing(MapboxConfig("tok"), transport=fake)
    res = r.route(RoutingOptions(waypoints=pts, optimize=True))
    assert res.waypoint_order == [0, 2, 1]
    # GET /optimized-trips/v1/mapbox/{profile}/{coords}; plain optimize keeps both
    # endpoints (v1 rejects any/any + roundtrip=false), params in the query string.
    assert path_of(fake.last).startswith("/optimized-trips/v1/mapbox/driving/")
    assert qget(fake.last, "roundtrip") == "false"
    assert qget(fake.last, "source") == "first"
    assert qget(fake.last, "destination") == "last"


def test_routing_errors():
    # The envelope code, not the status, is the no-route signal — live-verified
    # that Mapbox serves it on 200 as well as 422.
    fake = FakeTransport(resp(422, '{"code":"NoRoute","message":"no route found"}'))
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("t"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.NO_ROUTE and ei.value.provider_message == "no route found"

    # NoSegment = no road near a coordinate to snap to, i.e. still "no usable
    # route from here", not a malformed request.
    fake2 = FakeTransport(resp(200, '{"code":"NoSegment"}'))
    with pytest.raises(ConnectorError) as ei2:
        Routing(MapboxConfig("t"), transport=fake2).route(RoutingOptions(waypoints=TWO))
    assert ei2.value.provider_code == ProviderCode.NO_ROUTE


def test_matrix():
    fake = FakeTransport(resp(200, '{"code":"Ok","durations":[[0,60],[60,0]],"distances":[[0,1000],[1000,0]]}'))
    m = Matrix(MapboxConfig("tok"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert len(res.cells) == 4
    assert res.cells[1].distance_meters == 1000 and res.cells[1].duration_seconds == 60
    assert qget(fake.last, "annotations") == "duration,distance"


def test_matrix_dimension_mismatch():
    fake = FakeTransport(resp(200, '{"code":"Ok","durations":[[0,60]],"distances":[[0,1000]]}'))
    m = Matrix(MapboxConfig("t"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_geocode():
    fake = FakeTransport(resp(200, '{"features":[{"geometry":{"coordinates":[-74.0,40.7]},"properties":{"mapbox_id":"mid","full_address":"New York, NY","bbox":[-74.3,40.4,-73.7,40.9]}}]}'))
    g = Geocoding(MapboxConfig("tok"), transport=fake)
    res = g.geocode(GeocodeOptions(address="NYC", country_filter=["US"]))
    c = res.candidates[0]
    assert c.location.lat == 40.7 and c.location.lng == -74.0 and c.place_id == "mid"
    assert c.viewport.southwest.lat == 40.4 and c.viewport.northeast.lng == -73.7
    assert qget(fake.last, "country") == "us"


def test_autocomplete():
    fake = FakeTransport(resp(200, '{"suggestions":[{"name":"NY","full_address":"New York, NY","mapbox_id":"mid"}]}'))
    g = Geocoding(MapboxConfig("tok"), transport=fake)
    res = g.autocomplete(AutocompleteOptions(input="New"))
    assert res.predictions[0].description == "New York, NY" and res.predictions[0].place_id == "mid"
    assert qget(fake.last, "session_token") != ""


def test_isochrone():
    fake = FakeTransport(resp(200, '{"features":[{"properties":{"contour":10},"geometry":{"coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}}]}'))
    iso = Isochrone(MapboxConfig("tok"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600]))
    assert len(res.contours) == 1 and res.contours[0].value == 600
    assert res.contours[0].geometry.type == "Polygon"
    assert qget(fake.last, "polygons") == "true" and qget(fake.last, "contours_minutes") == "10"
