from __future__ import annotations

import datetime as dt

import pytest
from helpers import FakeTransport, body_json, resp

from thinwrap.location import (
    ConnectorError,
    EsriConfig,
    Geocoding,
    GeocodeOptions,
    GoogleConfig,
    HereConfig,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    MapboxConfig,
    Matrix,
    OsrmConfig,
    ProviderCode,
    Routing,
    RoutingOptions,
    TomTomConfig,
    TravelMode,
)
from thinwrap.location.transport import HttpRequest


def test_provider_ids():
    assert Routing(GoogleConfig("k")).provider_id.value == "google"
    assert Matrix(OsrmConfig("http://x")).provider_id.value == "osrm"
    assert Geocoding(TomTomConfig("k")).provider_id.value == "tomtom"
    assert Isochrone(HereConfig("k")).provider_id.value == "here"


def test_unsupported_operations():
    # OSRM: no geocoding, no isochrone.
    with pytest.raises(ValueError):
        Geocoding(OsrmConfig("http://x"))
    with pytest.raises(ValueError):
        Isochrone(OsrmConfig("http://x"))
    # Google: no isochrone.
    with pytest.raises(ValueError):
        Isochrone(GoogleConfig("k"))


class _ErrTransport:
    def send(self, request: HttpRequest):
        raise RuntimeError('Get "https://api.example.com/x?key=supersecret": connection refused')


def test_transport_error_redacts_and_normalizes():
    r = Routing(GoogleConfig("supersecret"), transport=_ErrTransport())
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)]))
    ce = ei.value
    assert ce.provider_code == ProviderCode.PROVIDER_UNAVAILABLE and ce.status_code is None
    assert "supersecret" not in str(ce) and "[REDACTED]" in str(ce)
    assert ce.cause is not None  # raw error preserved


def test_google_departure_time_and_walking():
    fake = FakeTransport(resp(200, '{"routes":[{"legs":[],"distanceMeters":1,"duration":"1s","polyline":{"encodedPolyline":"x"}}]}'))
    r = Routing(GoogleConfig("k"), transport=fake)
    when = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    r.route(RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)], departure_time=when, travel_mode=TravelMode.WALKING))
    b = body_json(fake.last)
    assert b["travelMode"] == "WALK"
    # Google rejects routingPreference for WALK/BICYCLE, so it is omitted even
    # when a departure_time is supplied.
    assert "routingPreference" not in b
    assert b["departureTime"] == "2026-01-02T03:04:05.000Z"


def test_mapbox_isochrone_cycling_profile():
    fake = FakeTransport(resp(200, '{"features":[{"properties":{"contour":500},"geometry":{"coordinates":[[[0,0],[1,1],[0,0]]]}}]}'))
    iso = Isochrone(MapboxConfig("t"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.DISTANCE, values=[500], travel_mode=TravelMode.CYCLING))
    from helpers import path_of, qget

    assert path_of(fake.last).startswith("/isochrone/v1/mapbox/cycling/")
    assert qget(fake.last, "contours_meters") == "500"
    assert res.contours[0].value == 500
