from __future__ import annotations

import datetime as dt
import math
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from helpers import FakeTransport, body_json, path_of, qget, resp

from thinwrap.location import (
    TrafficMode,
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
    PlaceDetailsOptions,
    ProviderCode,
    Routing,
    RoutingOptions,
    TravelMode,
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


def test_depart_at_uses_a_documented_iso_form():
    """Mapbox enumerates exactly three accepted ISO 8601 forms for depart_at
    (YYYY-MM-DDThh:mm:ssZ, ...±hh:mm, YYYY-MM-DDThh:mm); the millisecond form is
    not one of them."""
    pts = [LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
    geom = encode_p6(pts)
    body = '{"code":"Ok","routes":[{"geometry":"' + geom + '","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}]}'
    fake = FakeTransport(resp(200, body))
    departure = dt.datetime(2026, 8, 7, 3, 6, 0, 750_000, tzinfo=dt.timezone.utc)
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=pts, departure_time=departure, traffic_mode=TrafficMode.TRAFFIC)
    )
    assert path_of(fake.last).startswith("/directions/v5/mapbox/driving-traffic/")
    assert qget(fake.last, "depart_at") == "2026-08-07T03:06:00Z"


# Directions documents depart_at for the driving profiles only.
@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.CYCLING])
def test_depart_at_is_not_sent_for_walking_or_cycling(mode):
    body = '{"code":"Ok","routes":[{"geometry":"","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}]}'
    fake = FakeTransport(resp(200, body))
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=TWO, travel_mode=mode, departure_time=dt.datetime(2026, 8, 7, 3, 6, tzinfo=dt.timezone.utc))
    )
    assert path_of(fake.last).startswith(f"/directions/v5/mapbox/{mode.value}/")
    assert "depart_at" not in parse_qs(urlsplit(fake.last.url).query)


# Optimization v1's documented query parameters are annotations, approaches,
# bearings, destination, distributions, geometries, language, overview, radiuses,
# source, steps, roundtrip — unrecognized ones are an InvalidInput error.
@pytest.mark.parametrize("kwargs,want", [
    # A time under NONE is dropped instead (it has no effect by definition).
    (
        {"departure_time": dt.datetime(2026, 8, 7, 3, 6, tzinfo=dt.timezone.utc), "traffic_mode": TrafficMode.TRAFFIC},
        ProviderCode.UNSUPPORTED_FIELD,
    ),
    ({"avoid_tolls": True}, ProviderCode.UNSUPPORTED_OPTION),
    ({"avoid_ferries": True}, ProviderCode.UNSUPPORTED_OPTION),
    ({"avoid_highways": True}, ProviderCode.UNSUPPORTED_OPTION),
])
def test_optimized_path_refuses_parameters_v1_does_not_accept(kwargs, want):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("tok"), transport=fake).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True, **kwargs)
        )
    assert ei.value.provider_code == want
    assert fake.calls == []


# Optimization v1 supports roundtrip=false only as source=first +
# destination=last: first/any and any/last live-return 200 {"code":"NotImplemented"}.
@pytest.mark.parametrize("fixed", [{"optimize_fixed_origin": True}, {"optimize_fixed_destination": True}])
def test_open_trip_with_a_single_fixed_endpoint_is_refused(fixed):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("tok"), transport=fake).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], **fixed)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_OPTION
    assert fake.calls == []


def test_round_trip_with_a_fixed_origin_is_still_allowed():
    pts = [LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
    body = '{"code":"Ok","trips":[{"geometry":"","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1},{"waypoint_index":2}]}'
    fake = FakeTransport(resp(200, body))
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=pts, is_round_trip=True, optimize_fixed_origin=True)
    )
    assert path_of(fake.last).startswith("/optimized-trips/v1/")
    assert qget(fake.last, "roundtrip") == "true" and qget(fake.last, "source") == "first"
    query = parse_qs(urlsplit(fake.last.url).query)
    assert "exclude" not in query and "depart_at" not in query


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


def test_round_trip_with_a_fixed_destination_sends_destination_last():
    # Live: Optimization v1 roundtrip=true&source=any&destination=last → 200 Ok.
    pts = [LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]
    body = '{"code":"Ok","trips":[{"geometry":"","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1},{"waypoint_index":2}]}'
    fake = FakeTransport(resp(200, body))
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=pts, is_round_trip=True, optimize_fixed_destination=True)
    )
    assert qget(fake.last, "roundtrip") == "true"
    assert qget(fake.last, "source") == "first" and qget(fake.last, "destination") == "last"


def test_plain_round_trip_leaves_destination_unset():
    body = '{"code":"Ok","trips":[{"geometry":"","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1}]}'
    fake = FakeTransport(resp(200, body))
    Routing(MapboxConfig("tok"), transport=fake).route(RoutingOptions(waypoints=TWO, is_round_trip=True))
    assert "destination" not in parse_qs(urlsplit(fake.last.url).query)


def test_422_invalid_input_is_invalid_request():
    # Live: cycling exclude=toll → 422 with an InvalidInput envelope.
    body = '{"code":"InvalidInput","message":"exclude value must be one of: ferry, cash_only_tolls, unpaved, tunnel"}'
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("t"), transport=FakeTransport(resp(422, body))).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert ei.value.status_code == 422
    assert ei.value.provider_message.startswith("exclude value must be one of")


def test_422_processing_error_stays_unknown():
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("t"), transport=FakeTransport(resp(422, '{"code":"ProcessingError"}'))).route(
            RoutingOptions(waypoints=TWO)
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_200_not_implemented_is_unsupported_option():
    # Live: Optimization v1 roundtrip=false&source=first&destination=any →
    # HTTP 200 {"code":"NotImplemented","message":"This request is not supported"}.
    body = '{"code":"NotImplemented","message":"This request is not supported"}'
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("t"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=TWO, optimize=True)
        )
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_OPTION


def test_422_no_segment_stays_no_route():
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("t"), transport=FakeTransport(resp(422, '{"code":"NoSegment","message":"x"}'))).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.NO_ROUTE


_DIRECTIONS_OK = '{"code":"Ok","routes":[{"geometry":"","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}]}'


@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.CYCLING])
def test_walking_and_cycling_send_only_ferry_exclude(mode):
    # Live: walking toll/motorway → 422 "one of: ferry, cash_only_tolls,
    # border_crossing, ..."; cycling toll → 422 "one of: ferry, cash_only_tolls,
    # unpaved, tunnel"; ferry → 200 on both.
    fake = FakeTransport(resp(200, _DIRECTIONS_OK))
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=TWO, travel_mode=mode, avoid_tolls=True, avoid_highways=True, avoid_ferries=True)
    )
    assert qget(fake.last, "exclude") == "ferry"


@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.CYCLING])
def test_walking_and_cycling_without_ferry_send_no_exclude(mode):
    fake = FakeTransport(resp(200, _DIRECTIONS_OK))
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=TWO, travel_mode=mode, avoid_tolls=True, avoid_highways=True)
    )
    assert "exclude" not in parse_qs(urlsplit(fake.last.url).query)


def test_driving_sends_every_exclude_class():
    fake = FakeTransport(resp(200, _DIRECTIONS_OK))
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=TWO, avoid_tolls=True, avoid_highways=True, avoid_ferries=True)
    )
    assert qget(fake.last, "exclude") == "toll,ferry,motorway"


def test_live_traffic_selects_the_driving_traffic_profile():
    fake = FakeTransport(resp(200, _DIRECTIONS_OK))
    departure = dt.datetime(2026, 8, 7, 3, 6, tzinfo=dt.timezone.utc)
    Routing(MapboxConfig("tok"), transport=fake).route(
        RoutingOptions(waypoints=TWO, traffic_mode=TrafficMode.LIVE, departure_time=departure, avoid_tolls=True)
    )
    assert path_of(fake.last).startswith("/directions/v5/mapbox/driving-traffic/")
    # driving-traffic is a driving profile: depart_at and the full exclude set apply.
    assert qget(fake.last, "depart_at") == "2026-08-07T03:06:00Z"
    assert qget(fake.last, "exclude") == "toll"


@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.CYCLING])
def test_live_traffic_does_not_change_walking_or_cycling(mode):
    fake = FakeTransport(resp(200, _DIRECTIONS_OK))
    Routing(MapboxConfig("tok"), transport=fake).route(RoutingOptions(waypoints=TWO, travel_mode=mode, traffic_mode=TrafficMode.LIVE))
    assert path_of(fake.last).startswith(f"/directions/v5/mapbox/{mode.value}/")


def test_live_traffic_on_the_optimized_path_stays_on_driving():
    # Optimization v1 has no driving-traffic profile.
    body = '{"code":"Ok","trips":[{"geometry":"","legs":[{"distance":5,"duration":6}],"distance":5,"duration":6}],"waypoints":[{"waypoint_index":0},{"waypoint_index":1}]}'
    fake = FakeTransport(resp(200, body))
    Routing(MapboxConfig("tok"), transport=fake).route(RoutingOptions(waypoints=TWO, optimize=True, traffic_mode=TrafficMode.LIVE))
    assert path_of(fake.last).startswith("/optimized-trips/v1/mapbox/driving/")


def test_matrix_live_traffic_selects_the_driving_traffic_profile():
    fake = FakeTransport(resp(200, '{"code":"Ok","durations":[[60]],"distances":[[1000]]}'))
    Matrix(MapboxConfig("tok"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], traffic_mode=TrafficMode.LIVE)
    )
    assert path_of(fake.last).startswith("/directions-matrix/v1/mapbox/driving-traffic/")


@pytest.mark.parametrize("kwargs,want", [
    # Live: Matrix with exclude=toll or depart_at → 422 "Request too large for
    # custom parameters"; exclude is undocumented there, depart_at a sign-up beta.
    ({"avoid_tolls": True}, ProviderCode.UNSUPPORTED_OPTION),
    # Only traffic at a given time is refused; a time under NONE is dropped.
    (
        {"departure_time": dt.datetime(2026, 8, 7, 3, 6, tzinfo=dt.timezone.utc), "traffic_mode": TrafficMode.TRAFFIC},
        ProviderCode.UNSUPPORTED_FIELD,
    ),
])
def test_matrix_refuses_options_it_cannot_honour_before_any_request(kwargs, want):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Matrix(MapboxConfig("tok"), transport=fake).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], **kwargs)
        )
    assert ei.value.provider_code == want
    assert fake.calls == []


def test_config_repr_hides_the_access_token():
    assert "sk.secret-token" not in repr(MapboxConfig("sk.secret-token"))


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
    # session_token is required on /suggest: without a caller token a UUIDv4 is generated.
    assert uuid.UUID(qget(fake.last, "session_token")).version == 4


def test_autocomplete_forwards_the_callers_session_token():
    """Search Box groups every /suggest sharing a session_token into one billable
    session, closed by the /retrieve carrying the same value — so the caller's
    token must reach the wire on both calls, never a per-call random one."""
    fake = FakeTransport(
        resp(200, '{"suggestions":[{"name":"NY","mapbox_id":"mid"}]}'),
        resp(200, '{"features":[{"geometry":{"coordinates":[-74.0,40.7]},"properties":{"mapbox_id":"mid","full_address":"New York, NY"}}]}'),
    )
    g = Geocoding(MapboxConfig("tok"), transport=fake)
    token = "0f8fad5b-d9cb-469f-a165-70867728950e"
    g.autocomplete(AutocompleteOptions(input="Ne", session_token=token))
    g.place_details(PlaceDetailsOptions(place_id="mid", session_token=token))
    assert path_of(fake.calls[0]).endswith("/suggest") and qget(fake.calls[0], "session_token") == token
    assert "/retrieve/" in path_of(fake.calls[1]) and qget(fake.calls[1], "session_token") == token


def test_isochrone():
    fake = FakeTransport(resp(200, '{"features":[{"properties":{"contour":10},"geometry":{"coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}}]}'))
    iso = Isochrone(MapboxConfig("tok"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600]))
    assert len(res.contours) == 1 and res.contours[0].value == 600
    assert res.contours[0].geometry.type == "Polygon"
    assert qget(fake.last, "polygons") == "true" and qget(fake.last, "contours_minutes") == "10"


# --- isochrone breaks: whole minutes/meters, ascending, caller values back ------

_RING = [[0, 0], [1, 0], [1, 1], [0, 0]]


def _iso_features(*contours):
    import json

    return json.dumps({"features": [{"properties": {"contour": c}, "geometry": {"coordinates": [_RING]}} for c in contours]})


def test_isochrone_time_breaks_sent_ascending_and_read_back_as_caller_values():
    fake = FakeTransport(resp(200, _iso_features(2, 10)))
    res = Isochrone(MapboxConfig("tok"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600, 90])
    )
    assert qget(fake.last, "contours_minutes") == "2,10"
    # 90 s was sent as 2 min, and comes back as the caller's 90 s — not 120 s.
    assert [c.value for c in res.contours] == [90, 600]


def test_isochrone_distance_breaks_are_sent_as_integers_and_read_back():
    fake = FakeTransport(resp(200, _iso_features(1500, 250)))
    res = Isochrone(MapboxConfig("tok"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.DISTANCE, values=[1500.4, 249.6])
    )
    assert qget(fake.last, "contours_meters") == "250,1500"
    assert [c.value for c in res.contours] == [249.6, 1500.4]


@pytest.mark.parametrize("kind,values", [
    (IsochroneType.TIME, [29]),
    (IsochroneType.TIME, [59.9]),
    (IsochroneType.TIME, [600, 620]),  # both round to 10 min
    (IsochroneType.DISTANCE, [0.4]),
    (IsochroneType.DISTANCE, [100.2, 100.4]),
])
def test_isochrone_breaks_mapbox_cannot_express_are_rejected_before_sending(kind, values):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Isochrone(MapboxConfig("tok"), transport=fake).isochrone(
            IsochroneOptions(center=LatLng(40, -74), type=kind, values=values)
        )
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert fake.calls == []


@pytest.mark.parametrize("body", [
    '{"features":[]}',
    '{"features":[{"properties":{},"geometry":{"coordinates":[[[0,0],[1,1],[0,0]]]}}]}',
    '{"features":[{"properties":{"contour":10},"geometry":{"coordinates":[]}}]}',
    '{"features":[{"properties":{"contour":10},"geometry":{"coordinates":[[[0,null]]]}}]}',
])
def test_isochrone_without_a_usable_polygon_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Isochrone(MapboxConfig("tok"), transport=FakeTransport(resp(200, body))).isochrone(
            IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# A non-string, non-datetime value (an epoch int) is a TypeError, not a
# ConnectorError: tests/test_error_contract.py pins that side.
@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-01T00:00", "2026-01-01"])
def test_isochrone_departure_time_is_validated(bad):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Isochrone(MapboxConfig("tok"), transport=fake).isochrone(
            IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600], departure_time=bad)
        )
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert fake.calls == []


def test_isochrone_departure_time_string_is_sent_verbatim():
    fake = FakeTransport(resp(200, _iso_features(10)))
    Isochrone(MapboxConfig("tok"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600], departure_time="2026-09-01T08:00")
    )
    assert qget(fake.last, "depart_at") == "2026-09-01T08:00"


# --- routing: malformed routes never become zero-valued successes ---------------

@pytest.mark.parametrize("body", [
    '{"code":"Ok","routes":[null]}',
    '{"code":"Ok","routes":[{"distance":1,"duration":1,"geometry":"","legs":[null]}]}',
    '{"code":"Ok","routes":[{"distance":1,"geometry":"","legs":[{"distance":1,"duration":1}]}]}',
    '{"code":"Ok","routes":[{"distance":1,"duration":1,"geometry":"","legs":[{"duration":1}]}]}',
])
def test_routing_malformed_route_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("tok"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# --- matrix: body codes decide, as on routing -----------------------------------

@pytest.mark.parametrize("status,code,expected", [
    (422, "NoRoute", ProviderCode.NO_ROUTE),
    (422, "NoSegment", ProviderCode.NO_ROUTE),
    (422, "InvalidInput", ProviderCode.INVALID_REQUEST),
    (200, "NoRoute", ProviderCode.NO_ROUTE),
    (200, "NoSegment", ProviderCode.NO_ROUTE),
])
def test_matrix_body_code_mapping(status, code, expected):
    body = '{"code":"%s","message":"m"}' % code
    with pytest.raises(ConnectorError) as ei:
        Matrix(MapboxConfig("tok"), transport=FakeTransport(resp(status, body))).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == expected
    assert ei.value.status_code == status


@pytest.mark.parametrize("body", [
    '{"code":"Ok","durations":[null],"distances":[[1]]}',
    '{"code":"Ok","durations":[[1]]}',
])
def test_matrix_malformed_grid_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Matrix(MapboxConfig("tok"), transport=FakeTransport(resp(200, body))).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_matrix_non_numeric_entry_is_omitted_not_zero():
    body = '{"code":"Ok","durations":[[5,"x"]],"distances":[[7,8]]}'
    res = Matrix(MapboxConfig("tok"), transport=FakeTransport(resp(200, body))).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2), LatLng(3, 3)])
    )
    assert [(c.destination_index, c.distance_meters, c.duration_seconds) for c in res.cells] == [(0, 7, 5)]
