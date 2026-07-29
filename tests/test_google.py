from __future__ import annotations

import pytest
from helpers import FakeTransport, body_json, path_of, qget, resp

from thinwrap.location import (
    ConnectorError,
    GeocodeOptions,
    Geocoding,
    GoogleConfig,
    LatLng,
    Matrix,
    MatrixOptions,
    ProviderCode,
    Routing,
    RoutingOptions,
    ReverseGeocodeOptions,
    AutocompleteOptions,
    PlaceDetailsOptions,
)

TWO = [LatLng(40.7, -74.0), LatLng(41.4, -73.0)]


def test_routing_complete():
    fake = FakeTransport(resp(200, '{"routes":[{"legs":[{"distanceMeters":1000,"duration":"600s"}],"distanceMeters":1000,"duration":"600s","polyline":{"encodedPolyline":"abc"}}]}'))
    r = Routing(GoogleConfig("k"), transport=fake)
    assert r.provider_id.value == "google"
    res = r.route(RoutingOptions(waypoints=TWO))
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 600 and res.polyline == "abc"
    assert len(res.legs) == 1 and res.legs[0].distance_meters == 1000
    assert fake.last.url == "https://routes.googleapis.com/directions/v2:computeRoutes"
    assert fake.last.headers.get("X-Goog-Api-Key") == "k"
    assert body_json(fake.last)["travelMode"] == "DRIVE"


def test_routing_waypoint_order():
    fake = FakeTransport(resp(200, '{"routes":[{"legs":[],"distanceMeters":1,"duration":"1s","polyline":{"encodedPolyline":"x"},"optimizedIntermediateWaypointIndex":[1,0]}]}'))
    r = Routing(GoogleConfig("k"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3), LatLng(4, 4)], optimize=True))
    assert res.waypoint_order == [0, 2, 1, 3]
    assert body_json(fake.last)["optimizeWaypointOrder"] is True


def test_routing_too_few():
    r = Routing(GoogleConfig("k"), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=[LatLng(1, 1)]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


@pytest.mark.parametrize("status,body,headers,want", [
    (401, '{"error":{"message":"no"}}', {}, ProviderCode.AUTH_FAILED),
    (403, '{"error":{"status":"QUOTA_EXCEEDED"}}', {}, ProviderCode.RATE_LIMITED),
    (403, '{"error":{"message":"denied"}}', {}, ProviderCode.AUTH_FAILED),
    (400, '{"error":{"message":"bad"}}', {}, ProviderCode.INVALID_REQUEST),
    (503, "{}", {}, ProviderCode.PROVIDER_UNAVAILABLE),
    (429, '{"error":{"message":"slow"}}', {"retry_after": "12"}, ProviderCode.RATE_LIMITED),
])
def test_routing_error_mapping(status, body, headers, want):
    fake = FakeTransport(resp(status, body, **headers))
    r = Routing(GoogleConfig("k"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=TWO))
    ce = ei.value
    assert ce.provider_code == want and ce.status_code == status
    if headers:
        assert ce.cause["retryAfter"] == "12"
        assert "12 seconds" in ce.provider_message


def test_reason_based_auth_mapping():
    # Google returns HTTP 400 INVALID_ARGUMENT for an invalid key; the structured
    # ErrorInfo reason lets us classify it as auth_failed.
    bad_key = '{"error":{"code":400,"status":"INVALID_ARGUMENT","message":"API key not valid. Please pass a valid API key.","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"API_KEY_INVALID","domain":"googleapis.com","metadata":{"service":"routes.googleapis.com"}}]}}'
    for facade, call in (
        (Routing(GoogleConfig("bad"), transport=FakeTransport(resp(400, bad_key))), lambda f: f.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)]))),
        (Matrix(GoogleConfig("bad"), transport=FakeTransport(resp(400, bad_key))), lambda f: f.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)]))),
    ):
        with pytest.raises(ConnectorError) as ei:
            call(facade)
        assert ei.value.provider_code == ProviderCode.AUTH_FAILED

    # rate-limit reason on a 400 normalizes to rate_limited
    quota = '{"error":{"code":400,"status":"INVALID_ARGUMENT","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"RATE_LIMIT_EXCEEDED","domain":"googleapis.com"}]}}'
    with pytest.raises(ConnectorError) as ei:
        Routing(GoogleConfig("k"), transport=FakeTransport(resp(400, quota))).route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)]))
    assert ei.value.provider_code == ProviderCode.RATE_LIMITED

    # a plain 400 without ErrorInfo details still falls back to invalid_request
    with pytest.raises(ConnectorError) as ei:
        Routing(GoogleConfig("k"), transport=FakeTransport(resp(400, '{"error":{"message":"Origin and destination must be set."}}'))).route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_matrix_ndjson():
    ndjson = '{"originIndex":0,"destinationIndex":0,"distanceMeters":1000,"duration":"600s"}\n{"originIndex":0,"destinationIndex":1,"status":{"code":3}}\n{"originIndex":1,"destinationIndex":0,"distanceMeters":2000,"duration":"1200s"}'
    fake = FakeTransport(resp(200, ndjson))
    m = Matrix(GoogleConfig("k"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert len(res.cells) == 2  # failed (0,1) omitted
    assert res.cells[0].distance_meters == 1000 and res.cells[0].duration_seconds == 600


def test_geocode():
    fake = FakeTransport(resp(200, '{"status":"OK","results":[{"formatted_address":"New York, NY","geometry":{"location":{"lat":40.7,"lng":-74.0},"viewport":{"southwest":{"lat":40.4,"lng":-74.3},"northeast":{"lat":40.9,"lng":-73.7}}},"place_id":"pid"}]}'))
    g = Geocoding(GoogleConfig("k"), transport=fake)
    res = g.geocode(GeocodeOptions(address="NYC", country_filter=["US", "CA"], language="en"))
    c = res.candidates[0]
    assert c.formatted_address == "New York, NY" and c.location.lat == 40.7 and c.place_id == "pid"
    assert c.viewport.northeast.lat == 40.9
    assert qget(fake.last, "components") == "country:US|country:CA"
    assert qget(fake.last, "language") == "en"


def test_geocode_in_body_status_error():
    fake = FakeTransport(resp(200, '{"status":"REQUEST_DENIED","error_message":"bad key"}'))
    g = Geocoding(GoogleConfig("k"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        g.geocode(GeocodeOptions(address="x"))
    assert ei.value.provider_code == ProviderCode.AUTH_FAILED and ei.value.provider_message == "bad key"


def test_geocode_invalid_country():
    g = Geocoding(GoogleConfig("k"), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        g.geocode(GeocodeOptions(address="x", country_filter=["USA"]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_reverse_geocode():
    fake = FakeTransport(resp(200, '{"status":"OK","results":[{"formatted_address":"Somewhere","geometry":{"location":{"lat":1,"lng":2}}}]}'))
    g = Geocoding(GoogleConfig("k"), transport=fake)
    res = g.reverse_geocode(ReverseGeocodeOptions(location=LatLng(1, 2)))
    assert res.candidates[0].formatted_address == "Somewhere"
    assert qget(fake.last, "latlng") == "1,2"


def test_autocomplete():
    fake = FakeTransport(resp(200, '{"suggestions":[{"placePrediction":{"placeId":"pid","text":{"text":"New York"}}},{"placePrediction":{"text":{"text":"Newark"}}}]}'))
    g = Geocoding(GoogleConfig("k"), transport=fake)
    res = g.autocomplete(AutocompleteOptions(input="New"))
    assert len(res.predictions) == 2
    assert res.predictions[0].description == "New York" and res.predictions[0].place_id == "pid"


def test_session_token_wire_spelling():
    """Google uses two DIFFERENT spellings for one concept.

    A body field on the autocomplete leg, a query param on place details. Both
    verified live, where a bogus name is rejected with INVALID_ARGUMENT — so these
    are recognized parameters, not silently-ignored ones. Without them Autocomplete
    is billed per REQUEST (per keystroke) instead of per session.
    """
    token = "3f2a1c58-9b4e-4d7a-8e21-6c5f0b7d9a34"

    # autocomplete -> body field
    fake = FakeTransport(resp(200, '{"suggestions":[{"placePrediction":{"placeId":"p1","text":{"text":"Diz"}}}]}'))
    Geocoding(GoogleConfig(api_key="k"), transport=fake).autocomplete(
        AutocompleteOptions(input="Diz", session_token=token)
    )
    assert body_json(fake.last)["sessionToken"] == token
    assert "sessionToken" not in (fake.last.url.split("?", 1)[1] if "?" in fake.last.url else "")

    # place_details -> query param
    fake2 = FakeTransport(
        resp(200, '{"id":"p1","formattedAddress":"Dizengoff St 50","location":{"latitude":32.0797,"longitude":34.7738}}')
    )
    Geocoding(GoogleConfig(api_key="k"), transport=fake2).place_details(
        PlaceDetailsOptions(place_id="p1", session_token=token)
    )
    assert qget(fake2.last, "sessionToken") == token

    # omitted when not supplied — never an empty parameter
    fake3 = FakeTransport(resp(200, '{"suggestions":[]}'))
    Geocoding(GoogleConfig(api_key="k"), transport=fake3).autocomplete(AutocompleteOptions(input="Diz"))
    assert "sessionToken" not in body_json(fake3.last)
