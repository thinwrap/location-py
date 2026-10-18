"""Isochrone contour geometry across providers: every part and hole is kept, a
multi-part contour is a MultiPolygon, and rings are closed and wound per
RFC 7946 (exterior counter-clockwise, holes clockwise) whatever the vendor sent."""

from __future__ import annotations

import json

import pytest
from helpers import FakeTransport, resp

from thinwrap.location import (
    ConnectorError,
    EsriConfig,
    HereConfig,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    MapboxConfig,
    MultiPolygon,
    Polygon,
    ProviderCode,
    TomTomConfig,
    decode_flex_polyline,
)

_OPTS = IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=[600])


def _area(ring):
    return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring))) / 2


def _square(x0, y0, x1, y1, ccw=True):
    """Open square ring of [lng, lat] points in the requested winding."""
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return ring if ccw else list(reversed(ring))


def _closed(ring):
    return ring + [ring[0]]


def _assert_rfc7946(polygon_coords):
    for i, ring in enumerate(polygon_coords):
        assert ring[0] == ring[-1], "ring must be closed"
        if i == 0:
            assert _area(ring) > 0, "exterior must be counter-clockwise"
        else:
            assert _area(ring) < 0, "hole must be clockwise"


def _bbox(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _within(inner, outer):
    ix0, iy0, ix1, iy1 = _bbox(inner)
    ox0, oy0, ox1, oy1 = _bbox(outer)
    return ox0 <= ix0 and oy0 <= iy0 and ix1 <= ox1 and iy1 <= oy1


# ---- HERE ---------------------------------------------------------------------

_FLEX_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _flex_unsigned(v):
    out = ""
    while v > 0x1F:
        out += _FLEX_CHARS[(v & 0x1F) | 0x20]
        v >>= 5
    return out + _FLEX_CHARS[v]


def _flex(ring):
    """Encode [lng, lat] points as a precision-5, 2D HERE flexible polyline."""
    out = _flex_unsigned(1) + _flex_unsigned(5)
    last_lat = last_lng = 0
    for lng, lat in ring:
        ilat, ilng = round(lat * 1e5), round(lng * 1e5)
        for d in (ilat - last_lat, ilng - last_lng):
            out += _flex_unsigned((~(d << 1)) if d < 0 else (d << 1))
        last_lat, last_lng = ilat, ilng
    return out


def test_flex_encoder_round_trips():
    ring = _square(0, 0, 4, 4)
    assert [[p.lng, p.lat] for p in decode_flex_polyline(_flex(ring))] == ring


def _here(body):
    return Isochrone(HereConfig("k"), transport=FakeTransport(resp(200, json.dumps(body)))).isochrone(_OPTS)


def test_here_multi_part_isoline_keeps_every_part_and_hole():
    # Vendor winding deliberately inverted (clockwise outer, counter-clockwise hole).
    body = {"isolines": [{"range": {"value": 600}, "polygons": [
        {"outer": _flex(_square(0, 0, 4, 4, ccw=False)), "inner": [_flex(_square(1, 1, 2, 2))]},
        {"outer": _flex(_square(10, 0, 12, 2))},
    ]}]}
    geom = _here(body).contours[0].geometry
    assert isinstance(geom, MultiPolygon) and geom.type == "MultiPolygon"
    assert len(geom.coordinates) == 2
    first, second = geom.coordinates
    assert len(first) == 2 and len(second) == 1
    assert _within(first[1], first[0])
    for part in geom.coordinates:
        _assert_rfc7946(part)


def test_here_single_part_with_hole_stays_polygon():
    body = {"isolines": [{"range": {"value": 600}, "polygons": [
        {"outer": _flex(_square(0, 0, 4, 4)), "inner": [_flex(_square(1, 1, 2, 2))]},
    ]}]}
    geom = _here(body).contours[0].geometry
    assert isinstance(geom, Polygon) and geom.type == "Polygon"
    assert len(geom.coordinates) == 2
    _assert_rfc7946(geom.coordinates)


def test_here_part_without_outer_is_skipped_not_fatal():
    body = {"isolines": [{"range": {"value": 600}, "polygons": [
        {"outer": ""},
        {"outer": _flex(_square(0, 0, 4, 4)), "inner": ["", _flex(_square(1, 1, 2, 2))]},
    ]}]}
    geom = _here(body).contours[0].geometry
    assert geom.type == "Polygon" and len(geom.coordinates) == 2


# ---- Esri ---------------------------------------------------------------------

def _esri(*rings):
    body = {"saPolygons": {"features": [{"attributes": {"ToBreak": 10}, "geometry": {"rings": [_closed(r) for r in rings]}}]}}
    return Isochrone(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, json.dumps(body)))).isochrone(_OPTS)


def test_esri_multi_part_with_holes_and_rings0_being_a_hole():
    # Esri convention: exterior clockwise, hole counter-clockwise, any order.
    hole_b = _square(10.5, 0.5, 11, 1, ccw=True)
    ext_a = _square(0, 0, 4, 4, ccw=False)
    hole_a = _square(1, 1, 2, 2, ccw=True)
    ext_b = _square(10, 0, 12, 2, ccw=False)
    geom = _esri(hole_b, ext_a, hole_a, ext_b).contours[0].geometry
    assert isinstance(geom, MultiPolygon)
    assert len(geom.coordinates) == 2
    for part in geom.coordinates:
        assert len(part) == 2
        assert _within(part[1], part[0])
        _assert_rfc7946(part)
    exteriors = sorted(_bbox(p[0]) for p in geom.coordinates)
    assert exteriors == [(0, 0, 4, 4), (10, 0, 12, 2)]


def test_esri_single_exterior_with_hole_first_is_polygon():
    geom = _esri(_square(1, 1, 2, 2, ccw=True), _square(0, 0, 4, 4, ccw=False)).contours[0].geometry
    assert isinstance(geom, Polygon)
    assert _bbox(geom.coordinates[0]) == (0, 0, 4, 4)
    assert _bbox(geom.coordinates[1]) == (1, 1, 2, 2)
    _assert_rfc7946(geom.coordinates)


def test_esri_hole_goes_to_the_innermost_containing_exterior():
    # An island (exterior) inside a lake (hole) of a bigger part, with its own hole.
    big = _square(0, 0, 10, 10, ccw=False)
    lake = _square(2, 2, 8, 8, ccw=True)
    island = _square(4, 4, 6, 6, ccw=False)
    pond = _square(4.5, 4.5, 5.5, 5.5, ccw=True)
    geom = _esri(pond, big, island, lake).contours[0].geometry
    assert isinstance(geom, MultiPolygon)
    by_exterior = {_bbox(p[0]): [_bbox(h) for h in p[1:]] for p in geom.coordinates}
    assert by_exterior == {(0, 0, 10, 10): [(2, 2, 8, 8)], (4, 4, 6, 6): [(4.5, 4.5, 5.5, 5.5)]}


def test_esri_uncontained_hole_goes_to_the_largest_exterior():
    geom = _esri(
        _square(0, 0, 1, 1, ccw=False), _square(10, 10, 20, 20, ccw=False), _square(30, 30, 31, 31, ccw=True)
    ).contours[0].geometry
    by_exterior = {_bbox(p[0]): len(p) for p in geom.coordinates}
    assert by_exterior == {(0, 0, 1, 1): 1, (10, 10, 20, 20): 2}


def test_esri_without_any_clockwise_ring_reads_every_ring_as_exterior():
    geom = _esri(_square(0, 0, 1, 1, ccw=True), _square(5, 5, 6, 6, ccw=True)).contours[0].geometry
    assert isinstance(geom, MultiPolygon) and len(geom.coordinates) == 2
    for part in geom.coordinates:
        _assert_rfc7946(part)


def test_esri_malformed_rings_are_ignored_but_none_usable_is_unknown():
    body = {"saPolygons": {"features": [{"attributes": {"ToBreak": 10}, "geometry": {"rings": [[[1]], "x"]}}]}}
    with pytest.raises(ConnectorError) as ei:
        Isochrone(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, json.dumps(body)))).isochrone(_OPTS)
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# ---- Mapbox -------------------------------------------------------------------

def _mapbox(geometry):
    body = {"features": [{"properties": {"contour": 10}, "geometry": geometry}]}
    return Isochrone(MapboxConfig("tok"), transport=FakeTransport(resp(200, json.dumps(body)))).isochrone(_OPTS)


def test_mapbox_multipolygon_feature_keeps_every_part():
    geometry = {"type": "MultiPolygon", "coordinates": [
        [_closed(_square(0, 0, 4, 4, ccw=False)), _closed(_square(1, 1, 2, 2, ccw=True))],
        [_closed(_square(10, 0, 12, 2))],
    ]}
    geom = _mapbox(geometry).contours[0].geometry
    assert isinstance(geom, MultiPolygon) and len(geom.coordinates) == 2
    assert len(geom.coordinates[0]) == 2 and len(geom.coordinates[1]) == 1
    for part in geom.coordinates:
        _assert_rfc7946(part)


def test_mapbox_polygon_hole_is_kept_and_rewound():
    geometry = {"type": "Polygon", "coordinates": [
        _closed(_square(0, 0, 4, 4, ccw=False)), _closed(_square(1, 1, 2, 2, ccw=True)),
    ]}
    geom = _mapbox(geometry).contours[0].geometry
    assert isinstance(geom, Polygon) and len(geom.coordinates) == 2
    _assert_rfc7946(geom.coordinates)


def test_mapbox_unusable_hole_is_dropped_from_its_polygon():
    geometry = {"type": "Polygon", "coordinates": [
        _closed(_square(0, 0, 4, 4)), [[1, None]], _closed(_square(1, 1, 2, 2, ccw=False)),
    ]}
    geom = _mapbox(geometry).contours[0].geometry
    assert isinstance(geom, Polygon) and len(geom.coordinates) == 2
    assert _bbox(geom.coordinates[1]) == (1, 1, 2, 2)
    _assert_rfc7946(geom.coordinates)


def test_mapbox_multipolygon_part_with_unusable_exterior_is_dropped():
    geometry = {"type": "MultiPolygon", "coordinates": [
        [[[0, None]], _closed(_square(1, 1, 2, 2, ccw=False))],
        [_closed(_square(10, 0, 12, 2))],
        [_closed(_square(20, 0, 22, 2))],
    ]}
    geom = _mapbox(geometry).contours[0].geometry
    assert isinstance(geom, MultiPolygon)
    assert sorted(_bbox(p[0]) for p in geom.coordinates) == [(10, 0, 12, 2), (20, 0, 22, 2)]
    assert all(len(p) == 1 for p in geom.coordinates)


def test_mapbox_multipolygon_with_one_usable_part_is_a_polygon():
    geometry = {"type": "MultiPolygon", "coordinates": [[[[0, None]]], [_closed(_square(10, 0, 12, 2))]]}
    geom = _mapbox(geometry).contours[0].geometry
    assert isinstance(geom, Polygon) and _bbox(geom.coordinates[0]) == (10, 0, 12, 2)


@pytest.mark.parametrize("geometry", [
    {"type": "MultiPolygon", "coordinates": []},
    {"type": "MultiPolygon", "coordinates": [[[[0, None]]]]},
    {"type": "MultiPolygon", "coordinates": [[[[0, None]]], [[]], "x", [[["a", 1]], _closed(_square(1, 1, 2, 2))]]},
    {"type": "MultiPolygon", "coordinates": "x"},
    {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
])
def test_mapbox_nothing_usable_is_unknown_with_raw_and_status(geometry):
    with pytest.raises(ConnectorError) as ei:
        _mapbox(geometry)
    assert ei.value.provider_code == ProviderCode.UNKNOWN
    assert ei.value.status_code == 200
    assert ei.value.cause == {"features": [{"properties": {"contour": 10}, "geometry": geometry}]}


# ---- TomTom -------------------------------------------------------------------

def _tomtom(ring):
    boundary = [{"longitude": lng, "latitude": lat} for lng, lat in ring]
    body = {"reachableRange": {"boundary": boundary}}
    return Isochrone(TomTomConfig("k"), transport=FakeTransport(resp(200, json.dumps(body)))).isochrone(_OPTS)


def test_tomtom_boundary_is_closed_and_counter_clockwise():
    geom = _tomtom(_square(0, 0, 4, 4, ccw=False)).contours[0].geometry
    assert isinstance(geom, Polygon) and len(geom.coordinates) == 1
    assert len(geom.coordinates[0]) == 5
    _assert_rfc7946(geom.coordinates)


def test_tomtom_already_closed_boundary_is_not_closed_twice():
    geom = _tomtom(_closed(_square(0, 0, 4, 4))).contours[0].geometry
    assert len(geom.coordinates[0]) == 5
