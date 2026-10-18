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
    no, nd = 51, 50  # 2550 cells > 100 -> async
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


# TomTom's synchronous Matrix v2 caps a standard request at 100 cells; the
# async endpoint takes up to 2500.
@pytest.mark.parametrize("no,nd,want_async", [(10, 10, False), (1, 100, False), (101, 1, True), (11, 10, True)])
def test_matrix_sync_async_boundary_is_100_cells(no, nd, want_async):
    origins = [LatLng(i, 1) for i in range(no)]
    dests = [LatLng(i, 2) for i in range(nd)]
    data = '{"data":[{"originIndex":0,"destinationIndex":0,"routeSummary":{"lengthInMeters":1,"travelTimeInSeconds":1}}]}'
    responses = (resp(200, '{"jobId":"job1"}'), resp(200, '{"state":"Completed"}'), resp(200, data)) if want_async else (resp(200, data),)
    fake = FakeTransport(*responses)
    Matrix(TomTomConfig("k"), transport=fake, sleep=no_sleep).matrix(MatrixOptions(origins=origins, destinations=dests))
    if want_async:
        assert path_of(fake.calls[0]) == "/routing/matrix/2/async" and len(fake.calls) == 3
    else:
        assert path_of(fake.calls[0]) == "/routing/matrix/2" and len(fake.calls) == 1


# calculateReachableRange: "the values bicycle and pedestrian must not be used".
@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.CYCLING])
def test_isochrone_rejects_walking_and_cycling_before_any_request(mode):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Isochrone(TomTomConfig("k"), transport=fake).isochrone(
            IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600], travel_mode=mode)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_TRAVEL_MODE
    assert fake.calls == []


_TOMTOM_ROUTE_OK = '{"routes":[{"summary":{"lengthInMeters":1000,"travelTimeInSeconds":600},"legs":[{"summary":{"lengthInMeters":1000,"travelTimeInSeconds":600},"points":[{"latitude":40,"longitude":-74}]}]}]}'


def test_avoid_flags_are_sent_as_repeated_params():
    # Live: avoid=tollRoads,motorways → 400 "Invalid avoid value:
    # [tollRoads,motorways]"; avoid=tollRoads&avoid=motorways → 200.
    from urllib.parse import parse_qs, urlsplit

    fake = FakeTransport(resp(200, _TOMTOM_ROUTE_OK))
    Routing(TomTomConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)], avoid_tolls=True, avoid_ferries=True, avoid_highways=True)
    )
    assert parse_qs(urlsplit(fake.last.url).query)["avoid"] == ["tollRoads", "ferries", "motorways"]
    assert "%2C" not in fake.last.url


def test_single_avoid_flag_is_one_param():
    from urllib.parse import parse_qs, urlsplit

    fake = FakeTransport(resp(200, _TOMTOM_ROUTE_OK))
    Routing(TomTomConfig("k"), transport=fake).route(RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)], avoid_tolls=True))
    assert parse_qs(urlsplit(fake.last.url).query)["avoid"] == ["tollRoads"]


@pytest.mark.parametrize("flag", ["optimize_fixed_origin", "optimize_fixed_destination"])
def test_a_fixed_endpoint_flag_alone_requests_best_order(flag):
    body = '{"routes":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"legs":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"points":[]}]}],"optimizedWaypoints":[{"providedIndex":1,"optimizedIndex":0},{"providedIndex":0,"optimizedIndex":1}]}'
    fake = FakeTransport(resp(200, body))
    res = Routing(TomTomConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3), LatLng(4, 4)], **{flag: True})
    )
    assert qget(fake.last, "computeBestOrder") == "true"
    assert res.waypoint_order == [0, 2, 1, 3]


def test_config_repr_hides_the_api_key():
    assert "tt-secret" not in repr(TomTomConfig("tt-secret"))


# --- matrix honours trafficMode -------------------------------------------------

_SYNC_OK = '{"data":[{"originIndex":0,"destinationIndex":0,"routeSummary":{"lengthInMeters":100,"travelTimeInSeconds":10}}]}'


@pytest.mark.parametrize("mode,expected", [(None, "historical"), ("live", "live"), ("none", "historical")])
def test_matrix_sends_options_traffic(mode, expected):
    from helpers import body_json

    from thinwrap.location import TrafficMode

    kwargs = {} if mode is None else {"traffic_mode": TrafficMode(mode)}
    fake = FakeTransport(resp(200, _SYNC_OK))
    Matrix(TomTomConfig("k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], **kwargs)
    )
    assert body_json(fake.last)["options"]["traffic"] == expected


# --- matrix: malformed bodies are errors, failed cells are omitted --------------

@pytest.mark.parametrize("body", ["{}", '{"data":null}', '{"data":{}}', "[]"])
def test_matrix_without_a_data_array_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Matrix(TomTomConfig("k"), transport=FakeTransport(resp(200, body))).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


@pytest.mark.parametrize("entry", [
    '{"destinationIndex":0,"routeSummary":{"lengthInMeters":1,"travelTimeInSeconds":1}}',
    '{"originIndex":0,"destinationIndex":5,"routeSummary":{"lengthInMeters":1,"travelTimeInSeconds":1}}',
    "null",
])
def test_matrix_cell_without_valid_indexes_is_unknown(entry):
    with pytest.raises(ConnectorError) as ei:
        Matrix(TomTomConfig("k"), transport=FakeTransport(resp(200, '{"data":[' + entry + "]}"))).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_matrix_cell_missing_a_value_is_omitted():
    body = '{"data":[{"originIndex":0,"destinationIndex":0,"routeSummary":{"lengthInMeters":100}},{"originIndex":0,"destinationIndex":1,"detailedError":{"code":"NO_ROUTE_FOUND"}}]}'
    res = Matrix(TomTomConfig("k"), transport=FakeTransport(resp(200, body))).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2), LatLng(3, 3)])
    )
    assert res.cells == []


# --- routing: malformed routes never become zero-valued successes --------------

_LEG = '{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"points":[{"latitude":1,"longitude":1}]}'


@pytest.mark.parametrize("body", [
    '{"routes":[null]}',
    '{"routes":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"legs":[null]}]}',
    '{"routes":[{"summary":{"lengthInMeters":1},"legs":[' + _LEG + "]}]}",
    '{"routes":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"legs":[{"summary":{"travelTimeInSeconds":1}}]}]}',
    '{"routes":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"legs":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"points":[{"latitude":1}]}]}]}',
])
def test_routing_malformed_route_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Routing(TomTomConfig("k"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_routing_route_without_legs_is_an_error():
    body = '{"routes":[{"summary":{"lengthInMeters":1,"travelTimeInSeconds":1},"legs":[]}]}'
    with pytest.raises(ConnectorError):
        Routing(TomTomConfig("k"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)])
        )


# --- isochrone ------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    '{"reachableRange":{"boundary":[]}}',
    '{"reachableRange":{"boundary":[{"latitude":1}]}}',
    '{"reachableRange":{}}',
])
def test_isochrone_without_a_usable_boundary_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Isochrone(TomTomConfig("k"), transport=FakeTransport(resp(200, body))).isochrone(
            IsochroneOptions(center=LatLng(52, 4), type=IsochroneType.TIME, values=[600])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_isochrone_departure_time_is_validated_before_any_request():
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Isochrone(TomTomConfig("k"), transport=fake).isochrone(
            IsochroneOptions(center=LatLng(52, 4), type=IsochroneType.TIME, values=[600, 900], departure_time="soon")
        )
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert fake.calls == []


def test_matrix_live_without_a_departure_time_sends_depart_at_now():
    # Live: traffic "live" with TomTom's default departAt=any is an HTTP 400.
    from helpers import body_json

    from thinwrap.location import TrafficMode

    fake = FakeTransport(resp(200, _SYNC_OK))
    Matrix(TomTomConfig("k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], traffic_mode=TrafficMode.LIVE)
    )
    assert body_json(fake.last)["options"]["departAt"] == "now"


@pytest.mark.parametrize("mode", ["live", "traffic"])
def test_matrix_departure_time_is_sent_as_is_with_traffic(mode):
    import datetime as dt

    from helpers import body_json

    from thinwrap.location import TrafficMode

    fake = FakeTransport(resp(200, _SYNC_OK))
    Matrix(TomTomConfig("k"), transport=fake).matrix(
        MatrixOptions(
            origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], traffic_mode=TrafficMode(mode),
            departure_time=dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.timezone.utc),
        )
    )
    assert body_json(fake.last)["options"]["departAt"] == "2026-09-01T08:00:00.000Z"


def test_matrix_none_sends_depart_at_any_even_with_a_departure_time():
    """``historical`` + a real departAt is typical traffic for that time; ``any``
    is TomTom's time-independent average, which is what NONE promises."""
    import datetime as dt

    from helpers import body_json

    for when in (None, dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.timezone.utc)):
        fake = FakeTransport(resp(200, _SYNC_OK))
        Matrix(TomTomConfig("k"), transport=fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], departure_time=when)
        )
        options = body_json(fake.last)["options"]
        assert options["traffic"] == "historical"
        assert options["departAt"] == "any"


# --- async result: TomTom redirects a large result to a temporary download URL --

_ASYNC_ORIGINS = [LatLng(i, 1) for i in range(11)]
_ASYNC_DESTS = [LatLng(i, 2) for i in range(10)]  # 110 cells -> async
_ASYNC_DATA = '{"data":[{"originIndex":0,"destinationIndex":0,"routeSummary":{"lengthInMeters":100,"travelTimeInSeconds":60}}]}'
_DOWNLOAD_URL = "https://storage.example.net/matrix/job1?sig=abc"


def _async_matrix(*result_responses):
    fake = FakeTransport(resp(200, '{"jobId":"job1"}'), resp(200, '{"state":"Completed"}'), *result_responses)
    matrix = Matrix(TomTomConfig("k"), transport=fake, sleep=no_sleep)
    return fake, lambda: matrix.matrix(MatrixOptions(origins=_ASYNC_ORIGINS, destinations=_ASYNC_DESTS))


def test_async_result_302_is_followed_once_without_the_api_key():
    fake, run = _async_matrix(resp(302, "", location=_DOWNLOAD_URL), resp(200, _ASYNC_DATA))
    res = run()
    assert len(res.cells) == 1
    assert len(fake.calls) == 4
    assert qget(fake.calls[2], "key") == "k"
    download = fake.calls[3]
    assert download.url == _DOWNLOAD_URL
    assert "key=" not in download.url
    assert all("key" not in k.lower() for k in download.headers)


def test_async_result_302_without_location_is_unknown():
    fake, run = _async_matrix(resp(302, ""))
    with pytest.raises(ConnectorError) as ei:
        run()
    assert ei.value.provider_code == ProviderCode.UNKNOWN
    assert ei.value.status_code == 302
    assert len(fake.calls) == 3


def test_async_result_302_to_a_non_https_location_is_unknown():
    fake, run = _async_matrix(resp(302, "", location="http://storage.example.net/matrix/job1"))
    with pytest.raises(ConnectorError) as ei:
        run()
    assert ei.value.provider_code == ProviderCode.UNKNOWN
    assert len(fake.calls) == 3


def test_async_result_download_failure_is_mapped():
    _, run = _async_matrix(resp(302, "", location=_DOWNLOAD_URL), resp(503, ""))
    with pytest.raises(ConnectorError) as ei:
        run()
    assert ei.value.provider_code == ProviderCode.PROVIDER_UNAVAILABLE
