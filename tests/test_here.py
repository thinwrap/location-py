from __future__ import annotations

import datetime as dt
import types

import pytest
from helpers import FakeTransport, body_json, gzip_resp, no_sleep, qget, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    Geocoding,
    GeocodeOptions,
    HereConfig,
    HereTransportMode,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    Matrix,
    MatrixOptions,
    Passthrough,
    ProviderCode,
    Routing,
    RoutingOptions,
    ReverseGeocodeOptions,
    TravelMode,
)

FLEX = "BFoz5xJ67i1B1B7PzIhaxL7Y"


def test_routing_plain():
    fake = FakeTransport(resp(200, '{"routes":[{"sections":[{"polyline":"' + FLEX + '","summary":{"length":1000,"duration":600}}]}]}'))
    r = Routing(HereConfig("k"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(50.1, 8.6), LatLng(50.0, 8.7)]))
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 600 and res.polyline != ""
    assert qget(fake.last, "transportMode") == "car" and qget(fake.last, "apiKey") == "k"


def test_routing_transport_mode_override():
    fake = FakeTransport(resp(200, '{"routes":[{"sections":[{"polyline":"' + FLEX + '","summary":{"length":1,"duration":1}}]}]}'))
    r = Routing(HereConfig("k"), transport=fake)
    r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)], transport_mode=HereTransportMode.TRUCK))
    assert qget(fake.last, "transportMode") == "truck"


# bus and privateBus are distinct HERE modes, not synonyms: bus may use
# bus-exclusive streets, privateBus only where a waypoint sits on one.
@pytest.mark.parametrize("mode", [HereTransportMode.BUS, HereTransportMode.PRIVATE_BUS])
def test_routing_bus_transport_modes(mode):
    fake = FakeTransport(resp(200, '{"routes":[{"sections":[{"polyline":"' + FLEX + '","summary":{"length":1,"duration":1}}]}]}'))
    r = Routing(HereConfig("k"), transport=fake)
    r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)], transport_mode=mode))
    assert qget(fake.last, "transportMode") == mode.value


# The mode is forwarded verbatim into the findsequence2 mode grammar. bus is
# accepted there; privateBus is not, which is why it is documented as
# incompatible with optimize rather than silently rewritten.
def test_routing_optimized_bus_mode():
    findseq = resp(200, '{"results":[{"waypoints":[{"id":"start","sequence":0},{"id":"end","sequence":1},{"id":"destination1","sequence":2}]}]}')
    routes = resp(200, '{"routes":[{"sections":[{"polyline":"' + FLEX + '","summary":{"length":10,"duration":20}}]}]}')
    fake = FakeTransport(findseq, routes)
    r = Routing(HereConfig("k"), transport=fake)
    r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True, transport_mode=HereTransportMode.BUS))
    assert qget(fake.calls[0], "mode") == "fastest;bus;traffic:disabled"


def test_routing_optimized():
    findseq = resp(200, '{"results":[{"waypoints":[{"id":"start","sequence":0},{"id":"end","sequence":1},{"id":"destination1","sequence":2}]}]}')
    routes = resp(200, '{"routes":[{"sections":[{"polyline":"' + FLEX + '","summary":{"length":10,"duration":20}}]}]}')
    fake = FakeTransport(findseq, routes)
    r = Routing(HereConfig("k"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True))
    assert res.waypoint_order == [0, 2, 1]


def test_findsequence_departure_has_no_milliseconds():
    """Live-verified wire grammar of the legacy WPS endpoint: findsequence2
    answers 400 'Bad Format for Date and Time' to '2026-08-07T03:06:00.000Z' and
    200 to '2026-08-07T03:06:00Z'. /v8/routes keeps the fractional form."""
    findseq = resp(200, '{"results":[{"waypoints":[{"id":"start","sequence":0},{"id":"end","sequence":1},{"id":"destination1","sequence":2}]}]}')
    routes = resp(200, '{"routes":[{"sections":[{"polyline":"' + FLEX + '","summary":{"length":10,"duration":20}}]}]}')
    fake = FakeTransport(findseq, routes)
    departure = dt.datetime(2026, 8, 7, 3, 6, 0, 999_000, tzinfo=dt.timezone.utc)
    Routing(HereConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True, departure_time=departure)
    )

    emitted = qget(fake.calls[0], "departure")
    assert emitted == "2026-08-07T03:06:00Z"
    assert "." not in emitted
    assert qget(fake.calls[1], "departureTime") == "2026-08-07T03:06:00.999Z"


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "Bad Format for Date and Time"),
        # The legacy endpoint also reports a rejection as HTTP 200 + responseCode.
        (200, "returned no sequence: Bad Format for Date and Time"),
    ],
)
def test_findsequence_surfaces_errors_array(status, expected):
    body = '{"results":null,"errors":["Bad Format for Date and Time: 2026-08-07T03:06:00.000Z."],"responseCode":"400"}'
    fake = FakeTransport(resp(status, body))
    r = Routing(HereConfig("k"), transport=fake)
    with pytest.raises(ConnectorError) as excinfo:
        r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True))
    assert expected in (excinfo.value.provider_message or "")


def test_findsequence_maps_status_when_transport_raises_the_non_2xx():
    """A BYO transport calling raise_for_status() must not collapse a 400 into
    provider_unavailable: the provider answered, so the caller gets the real
    status and the HERE message."""

    class RaisingTransport:
        """Shape of requests/httpx: the exception carries a `.response`, and its
        message embeds the key-bearing URL."""

        def send(self, request):
            error = RuntimeError(f"400 Client Error for url: {request.url}")
            error.response = types.SimpleNamespace(
                status_code=400,
                headers={"Content-Type": "application/json"},
                content=b'{"results":null,"errors":["Bad Format for Date and Time: x."],"responseCode":"400"}',
            )
            raise error

    r = Routing(HereConfig("secret-key"), transport=RaisingTransport())
    with pytest.raises(ConnectorError) as excinfo:
        r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)], optimize=True))
    error = excinfo.value
    assert error.status_code == 400
    assert error.provider_code is ProviderCode.INVALID_REQUEST
    assert "Bad Format for Date and Time" in (error.provider_message or "")
    assert "secret-key" not in repr(error)


def test_transport_failure_without_a_status_stays_provider_unavailable():
    class DeadTransport:
        def send(self, request):
            raise OSError("connection reset by peer")

    r = Routing(HereConfig("k"), transport=DeadTransport())
    with pytest.raises(ConnectorError) as excinfo:
        r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)]))
    assert excinfo.value.provider_code is ProviderCode.PROVIDER_UNAVAILABLE
    assert excinfo.value.status_code is None


_RESULT_URL = "https://aws-eu-west-1.matrix.router.hereapi.com/v8/matrix/mid/result"
_S3_URL = "https://s3.eu-west-1.amazonaws.com/here-matrix-results/mid.json.gz?X-Amz-Signature=deadbeef&X-Amz-Expires=60"


def test_matrix_async_cycle():
    # Real HERE v8 async flow: poll completes with a 303 (Location + body),
    # retrieve resultUrl 303-redirects to a pre-signed S3 URL, and the final
    # body is gzip-compressed.
    submit = resp(200, '{"matrixId":"mid","statusUrl":"https://matrix.router.hereapi.com/v8/matrix/mid/status"}')
    poll = resp(303, '{"matrixId":"mid","status":"completed","resultUrl":"' + _RESULT_URL + '"}', location=_RESULT_URL)
    retrieve_redirect = resp(303, "", location=_S3_URL)
    retrieve_body = gzip_resp(200, '{"matrixId":"mid","matrix":{"numOrigins":2,"numDestinations":2,"travelTimes":[0,60,60,0],"distances":[0,1000,1000,0]}}')
    fake = FakeTransport(submit, poll, retrieve_redirect, retrieve_body)
    m = Matrix(HereConfig("k"), transport=fake, sleep=no_sleep)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert len(res.cells) == 4
    assert res.cells[1].distance_meters == 1000 and res.cells[1].duration_seconds == 60  # meters / seconds, as-is
    assert len(fake.calls) == 4
    # The resultUrl hop (HERE host) carries the apiKey + Accept-Encoding: gzip.
    result_call = fake.calls[2]
    assert qget(result_call, "apiKey") == "k"
    assert result_call.headers.get("Accept-Encoding") == "gzip"
    # The S3 hop must NOT leak the HERE apiKey — neither as a query param nor a header.
    s3_call = fake.calls[3]
    assert s3_call.url.startswith("https://s3.eu-west-1.amazonaws.com/")
    assert "apikey" not in s3_call.url.lower() and "k" not in qget(s3_call, "apiKey")
    assert all("key" not in k.lower() and "auth" not in k.lower() for k in s3_call.headers)
    assert s3_call.headers.get("Accept-Encoding") == "gzip"


def test_matrix_retrieve_direct_plain_body():
    # Some responses return the 200 gzip/plain body directly without the S3 hop,
    # and a 200 poll body with status "completed" is honored too (belt-and-braces).
    submit = resp(200, '{"matrixId":"mid","statusUrl":"https://matrix.router.hereapi.com/v8/matrix/mid/status"}')
    poll = resp(200, '{"matrixId":"mid","status":"completed","resultUrl":"' + _RESULT_URL + '"}')
    retrieve = resp(200, '{"matrixId":"mid","matrix":{"numOrigins":1,"numDestinations":1,"travelTimes":[5427],"distances":[109144]}}')
    fake = FakeTransport(submit, poll, retrieve)
    m = Matrix(HereConfig("k"), transport=fake, sleep=no_sleep)
    res = m.matrix(MatrixOptions(origins=[LatLng(40.7484, -73.9857)], destinations=[LatLng(41.1792, -73.1952)]))
    assert len(res.cells) == 1
    c = res.cells[0]
    assert c.distance_meters == 109144 and c.duration_seconds == 5427
    assert len(fake.calls) == 3  # no S3 hop


# Matrix v8 accepts the same eight transport modes as Routing v8 — verified live
# for bus and privateBus, both HTTP 200 — so MatrixOptions takes the whole set,
# not a routing-only subset.
@pytest.mark.parametrize("mode", [HereTransportMode.TAXI, HereTransportMode.BUS, HereTransportMode.PRIVATE_BUS])
def test_matrix_transport_mode(mode):
    submit = resp(200, '{"matrixId":"mid","statusUrl":"https://matrix.router.hereapi.com/v8/matrix/mid/status"}')
    poll = resp(200, '{"matrixId":"mid","status":"completed","resultUrl":"' + _RESULT_URL + '"}')
    retrieve = resp(200, '{"matrixId":"mid","matrix":{"numOrigins":1,"numDestinations":1,"travelTimes":[1],"distances":[1]}}')
    fake = FakeTransport(submit, poll, retrieve)
    m = Matrix(HereConfig("k"), transport=fake, sleep=no_sleep)
    m.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], transport_mode=mode))
    assert body_json(fake.calls[0])["transportMode"] == mode.value


def test_matrix_rejects_foreign_host():
    submit = resp(200, '{"matrixId":"mid","statusUrl":"https://evil.example.com/steal"}')
    m = Matrix(HereConfig("k"), transport=FakeTransport(submit), sleep=no_sleep)
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_matrix_polling_timeout():
    submit = resp(200, '{"matrixId":"mid","statusUrl":"https://matrix.router.hereapi.com/v8/matrix/mid/status"}')
    pending = resp(200, '{"status":"inProgress"}')
    m = Matrix(HereConfig("k"), transport=FakeTransport(submit, pending))  # real sleep, 1ms deadline
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], passthrough=Passthrough(body={"timeoutMs": 1})))
    assert ei.value.provider_code == ProviderCode.MATRIX_POLLING_TIMEOUT
    assert ei.value.cause["matrixId"] == "mid"


def test_geocode():
    fake = FakeTransport(resp(200, '{"items":[{"title":"New York","position":{"lat":40.7,"lng":-74.0},"id":"here:123","mapView":{"south":40.4,"west":-74.3,"north":40.9,"east":-73.7}}]}'))
    g = Geocoding(HereConfig("k"), transport=fake)
    res = g.geocode(GeocodeOptions(address="NYC", country_filter=["US"]))
    c = res.candidates[0]
    assert c.formatted_address == "New York" and c.place_id == "here:123"
    assert c.viewport.southwest.lat == 40.4
    assert qget(fake.last, "in") == "countryCode:USA"


def test_geocode_unmapped_country():
    g = Geocoding(HereConfig("k"), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        g.geocode(GeocodeOptions(address="x", country_filter=["ZZ"]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_autocomplete():
    fake = FakeTransport(resp(200, '{"items":[{"title":"New York, NY","id":"here:abc"}]}'))
    g = Geocoding(HereConfig("k"), transport=fake)
    # Autosuggest mandates a search context; incidental to what this asserts.
    res = g.autocomplete(AutocompleteOptions(input="New", location=LatLng(40.7128, -74.006)))
    assert res.predictions[0].description == "New York, NY" and res.predictions[0].place_id == "here:abc"


def test_isochrone():
    fake = FakeTransport(resp(200, '{"isolines":[{"range":{"type":"time","value":600},"polygons":[{"outer":"' + FLEX + '"}]}]}'))
    iso = Isochrone(HereConfig("k"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(50.1, 8.6), type=IsochroneType.TIME, values=[600]))
    assert res.contours[0].value == 600
    assert len(res.contours[0].geometry.coordinates[0]) >= 5  # closed ring
    assert qget(fake.last, "range[values]") == "600"


def test_isochrone_rejects_cycling():
    iso = Isochrone(HereConfig("k"), transport=FakeTransport())
    with pytest.raises(ConnectorError) as ei:
        iso.isochrone(IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=[600], travel_mode=TravelMode.CYCLING))
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_TRAVEL_MODE
