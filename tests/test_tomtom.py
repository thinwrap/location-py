from __future__ import annotations

import pytest
from helpers import FakeTransport, no_sleep, path_of, qget, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    Geocoding,
    GeocodeOptions,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    Matrix,
    MatrixOptions,
    ProviderCode,
    Routing,
    RoutingOptions,
    ReverseGeocodeOptions,
    TomTomConfig,
    TravelMode,
)


def test_routing():
    fake = FakeTransport(resp(200, '{"routes":[{"summary":{"lengthInMeters":1000,"travelTimeInSeconds":600},"legs":[{"summary":{"lengthInMeters":1000,"travelTimeInSeconds":600},"points":[{"latitude":40,"longitude":-74},{"latitude":41,"longitude":-73}]}]}]}'))
    r = Routing(TomTomConfig("k"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)]))
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 600 and res.polyline != ""
    assert "40,-74:41,-73" in path_of(fake.last)
    assert qget(fake.last, "travelMode") == "car"


def test_routing_optimized():
    fake = FakeTransport(resp(200, '{"routes":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"legs":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"points":[{"latitude":1,"longitude":1}]}]}],"optimizedWaypoints":[{"providedIndex":0,"optimizedIndex":0},{"providedIndex":2,"optimizedIndex":1},{"providedIndex":1,"optimizedIndex":2}]}'))
    r = Routing(TomTomConfig("k"), transport=fake)
    # Input [A,B,C,D,E]; origin(0)/destination(4) fixed, 3 intermediates
    # (providedIndex 0,1,2). optimizedWaypoints is intermediate-relative;
    # projected to input indices + bracketed by origin/dest -> [0,1,3,2,4].
    res = r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3), LatLng(4, 4), LatLng(5, 5)], optimize=True))
    assert res.waypoint_order == [0, 1, 3, 2, 4]
    assert qget(fake.last, "computeBestOrder") == "true"


def test_matrix_sync():
    fake = FakeTransport(resp(200, '{"data":[{"originIndex":0,"destinationIndex":0,"routeSummary":{"lengthInMeters":0,"travelTimeInSeconds":0}},{"originIndex":0,"destinationIndex":1,"routeSummary":{"lengthInMeters":1000,"travelTimeInSeconds":60}}]}'))
    m = Matrix(TomTomConfig("k"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2), LatLng(3, 3)]))
    assert len(res.cells) == 2
    assert res.cells[1].distance_meters == 1000 and res.cells[1].duration_seconds == 60
    assert path_of(fake.last) == "/routing/matrix/2"


def test_matrix_rejects_cycling():
    m = Matrix(TomTomConfig("k"), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], travel_mode=TravelMode.CYCLING))
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_TRAVEL_MODE


def test_matrix_async():
    no, nd = 51, 50  # 2550 > 2500 -> async
    origins = [LatLng(i, 1) for i in range(no)]
    dests = [LatLng(i, 2) for i in range(nd)]
    data = '{"data":[' + ",".join(
        f'{{"originIndex":{i},"destinationIndex":{j},"routeSummary":{{"lengthInMeters":100,"travelTimeInSeconds":60}}}}'
        for i in range(no) for j in range(nd)
    ) + "]}"
    fake = FakeTransport(resp(200, '{"jobId":"job1"}'), resp(200, '{"state":"Completed"}'), resp(200, data))
    m = Matrix(TomTomConfig("k"), transport=fake, sleep=no_sleep)
    res = m.matrix(MatrixOptions(origins=origins, destinations=dests))
    assert len(res.cells) == no * nd
    assert len(fake.calls) == 3
    assert path_of(fake.calls[2]).endswith("/result")


def test_geocode():
    fake = FakeTransport(resp(200, '{"results":[{"id":"g1","address":{"freeformAddress":"New York, NY"},"position":{"lat":40.7,"lon":-74.0},"viewport":{"topLeftPoint":{"lat":40.9,"lon":-74.3},"btmRightPoint":{"lat":40.4,"lon":-73.7}}}]}'))
    g = Geocoding(TomTomConfig("k"), transport=fake)
    res = g.geocode(GeocodeOptions(address="NYC", country_filter=["US"]))
    c = res.candidates[0]
    assert c.location.lat == 40.7 and c.location.lng == -74.0 and c.place_id == "g1"
    assert c.viewport.southwest.lat == 40.4 and c.viewport.northeast.lat == 40.9
    assert qget(fake.last, "countrySet") == "US"


def test_reverse_geocode():
    fake = FakeTransport(resp(200, '{"addresses":[{"address":{"freeformAddress":"123 Main St"},"position":"40.7,-74.0","id":"r1"}]}'))
    g = Geocoding(TomTomConfig("k"), transport=fake)
    res = g.reverse_geocode(ReverseGeocodeOptions(location=LatLng(40.7, -74.0)))
    assert res.candidates[0].location.lat == 40.7 and res.candidates[0].location.lng == -74.0


def test_autocomplete():
    fake = FakeTransport(resp(200, '{"results":[{"id":"a1","address":{"freeformAddress":"New York, NY"},"poi":{"name":"Statue of Liberty"}}]}'))
    g = Geocoding(TomTomConfig("k"), transport=fake)
    res = g.autocomplete(AutocompleteOptions(input="Statue"))
    assert res.predictions[0].description == "Statue of Liberty, New York, NY" and res.predictions[0].place_id == "a1"
    assert qget(fake.last, "typeahead") == "true"


def test_isochrone_single_band():
    fake = FakeTransport(resp(200, '{"reachableRange":{"boundary":[{"latitude":40,"longitude":-74},{"latitude":41,"longitude":-74},{"latitude":41,"longitude":-73}]}}'))
    iso = Isochrone(TomTomConfig("k"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600]))
    assert res.contours[0].value == 600 and res.meta is None
    assert len(res.contours[0].geometry.coordinates[0]) == 4  # closed ring


def test_isochrone_multi_band():
    band = '{"reachableRange":{"boundary":[{"latitude":40,"longitude":-74},{"latitude":41,"longitude":-74}]}}'
    fake = FakeTransport(resp(200, band), resp(200, band))
    iso = Isochrone(TomTomConfig("k"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600, 300]))
    assert len(fake.calls) == 2
    assert res.meta is not None and res.meta.request_count == 2
    assert res.contours[0].value == 300 and res.contours[1].value == 600
