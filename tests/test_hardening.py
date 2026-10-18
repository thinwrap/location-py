"""Regression tests for the targeted hardening fixes:

- transport-error redaction never leaks credentials via cause/__cause__;
- non-finite coordinates raise a clean Connector(invalid_request), never a raw
  ValueError/OverflowError, on every guarded coordinate-bearing method;
- iso_string treats a naive datetime as UTC (host-timezone independent);
- compact_json rejects non-finite floats instead of emitting invalid JSON.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from helpers import FakeTransport, no_sleep, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    EsriConfig,
    GeocodeOptions,
    Geocoding,
    GoogleConfig,
    HereConfig,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    Matrix,
    MatrixOptions,
    MapboxConfig,
    ProviderCode,
    ReverseGeocodeOptions,
    Routing,
    RoutingOptions,
    TomTomConfig,
)
from thinwrap.location._util import compact_json, iso_string

_SECRET = "SUPERSECRET123"


class RaisingTransport:
    """A BYO transport (mimicking requests/httpx) whose exception message embeds
    the full request URL, including a credential query param."""

    def send(self, request):
        raise RuntimeError(
            "HTTPSConnectionPool: failed to reach "
            f"https://maps.googleapis.com/maps/api/geocode/json?address=x&key={_SECRET}"
        )


def test_transport_error_does_not_leak_secret():
    geo = Geocoding(GoogleConfig("k"), transport=RaisingTransport())
    with pytest.raises(ConnectorError) as ei:
        geo.geocode(GeocodeOptions(address="x"))
    err = ei.value
    assert err.provider_code == ProviderCode.PROVIDER_UNAVAILABLE
    # The secret must not survive on any user-reachable surface.
    assert _SECRET not in str(err)
    assert _SECRET not in (err.provider_message or "")
    assert _SECRET not in str(err.cause)
    assert _SECRET not in str(err.__cause__)
    # Error trackers (Sentry/Rollbar) walk __context__ regardless of
    # __suppress_context__, so the raw URL-bearing transport exception must not
    # survive there either — the error is raised OUTSIDE the except block.
    assert _SECRET not in str(err.__context__)
    # cause is a redacted string, not the raw exception object; chain broken.
    assert isinstance(err.cause, str) and "[REDACTED]" in err.cause
    assert err.__cause__ is None
    assert err.__context__ is None


# --- (b) non-finite coordinate guards -----------------------------------------

_NAN = LatLng(math.nan, 0.0)
_INF = LatLng(0.0, math.inf)
_OK = LatLng(1.0, 1.0)


def _fake():
    return FakeTransport(resp(200, "{}"))


@pytest.mark.parametrize("cfg", [
    GoogleConfig("k"),
    MapboxConfig("k"),
    HereConfig("k"),
    EsriConfig(api_key="k"),
    TomTomConfig("k"),
])
def test_reverse_geocode_rejects_nonfinite(cfg):
    geo = Geocoding(cfg, transport=_fake())
    with pytest.raises(ConnectorError) as ei:
        geo.reverse_geocode(ReverseGeocodeOptions(location=_NAN))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_esri_autocomplete_rejects_nonfinite():
    geo = Geocoding(EsriConfig(api_key="k"), transport=_fake())
    with pytest.raises(ConnectorError) as ei:
        geo.autocomplete(AutocompleteOptions(input="x", location=_INF))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


@pytest.mark.parametrize("cfg", [
    GoogleConfig("k"),
    MapboxConfig("k"),
    HereConfig("k"),
    EsriConfig(api_key="k"),
    TomTomConfig("k"),
])
def test_routing_rejects_nonfinite(cfg):
    r = Routing(cfg, transport=_fake())
    with pytest.raises(ConnectorError) as ei:
        r.route(RoutingOptions(waypoints=[_NAN, _OK]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


@pytest.mark.parametrize("cfg,uses_sleep", [
    (GoogleConfig("k"), False),
    (HereConfig("k"), True),
    (EsriConfig(api_key="k"), False),
    (TomTomConfig("k"), True),
])
def test_matrix_rejects_nonfinite(cfg, uses_sleep):
    kwargs = {"transport": _fake()}
    if uses_sleep:
        kwargs["sleep"] = no_sleep
    m = Matrix(cfg, **kwargs)
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[_NAN], destinations=[_OK]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


@pytest.mark.parametrize("cfg", [
    MapboxConfig("k"),
    HereConfig("k"),
    EsriConfig(api_key="k"),
    TomTomConfig("k"),
])
def test_isochrone_rejects_nonfinite(cfg):
    iso = Isochrone(cfg, transport=_fake())
    with pytest.raises(ConnectorError) as ei:
        iso.isochrone(IsochroneOptions(center=_NAN, type=IsochroneType.TIME, values=[300]))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


# --- (c) iso_string: naive datetime is interpreted as UTC ---------------------

def test_iso_string_naive_interpreted_as_utc():
    naive = datetime(2026, 7, 19, 12, 0, 0)
    aware = naive.replace(tzinfo=timezone.utc)
    assert iso_string(naive) == "2026-07-19T12:00:00.000Z"
    assert iso_string(naive) == iso_string(aware)


def test_iso_string_naive_host_tz_independent():
    naive = datetime(2026, 7, 19, 12, 0, 0)
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset unavailable on this platform")
    prev = os.environ.get("TZ")
    try:
        for tz in ("America/New_York", "Asia/Tokyo", "UTC"):
            os.environ["TZ"] = tz
            time.tzset()
            # Naive input must not be shifted by the host's local timezone.
            assert iso_string(naive) == "2026-07-19T12:00:00.000Z"
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


def test_iso_string_aware_still_converts_offset():
    aware = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert iso_string(aware) == "2026-07-19T07:00:00.000Z"


# --- (d) compact_json rejects non-finite floats -------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_compact_json_rejects_nonfinite(bad):
    with pytest.raises(ValueError):
        compact_json({"coord": bad})


def test_compact_json_finite_still_works():
    assert compact_json({"a": 1, "b": "x"}) == b'{"a":1,"b":"x"}'


# --- decode_json rejects the non-JSON NaN/Infinity tokens ---------------------

@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_decode_json_rejects_nonfinite_tokens(token):
    from thinwrap.location._util import decode_json

    assert decode_json(b'{"distance": ' + token + b"}") is None


def test_nan_in_a_vendor_body_is_an_error_not_a_nan_distance():
    body = '{"code":"Ok","routes":[{"distance":NaN,"duration":1,"geometry":"","legs":[{"distance":1,"duration":1}]}]}'
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("pk"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# --- passthrough headers replace connector headers case-insensitively ----------

def test_passthrough_header_replaces_connector_header_regardless_of_case():
    from thinwrap.location import Passthrough

    fake = FakeTransport(resp(200, '{"routes":[{"distanceMeters":1,"duration":"1s","legs":[{"distanceMeters":1,"duration":"1s"}]}]}'))
    Routing(GoogleConfig("k"), transport=fake).route(
        RoutingOptions(
            waypoints=[LatLng(1, 1), LatLng(2, 2)],
            passthrough=Passthrough(headers={"x-goog-fieldmask": "routes.distanceMeters"}),
        )
    )
    sent = {k: v for k, v in fake.last.headers.items() if k.lower() == "x-goog-fieldmask"}
    # One header, the caller's spelling and value.
    assert sent == {"x-goog-fieldmask": "routes.distanceMeters"}


def test_passthrough_content_type_replaces_the_connectors():
    from thinwrap.location import Passthrough

    fake = FakeTransport(resp(200, '{"suggestions":[]}'))
    Geocoding(GoogleConfig("k"), transport=fake).autocomplete(
        AutocompleteOptions(input="x", passthrough=Passthrough(headers={"content-type": "application/json; charset=utf-8"}))
    )
    sent = {k: v for k, v in fake.last.headers.items() if k.lower() == "content-type"}
    assert sent == {"content-type": "application/json; charset=utf-8"}


# --- timeoutMs: a finite number of milliseconds > 0, else invalid_request ------

_MATRIX_TWO_BY_TWO = dict(origins=[LatLng(1, 1), LatLng(1, 2)], destinations=[LatLng(2, 1), LatLng(2, 2)])


@pytest.mark.parametrize("bad", ["5000", float("nan"), float("inf"), 0, -1, True, None, [1]])
@pytest.mark.parametrize("cfg", [HereConfig("k"), TomTomConfig("k")])
def test_timeout_ms_rejects_anything_but_a_positive_finite_number(cfg, bad):
    from thinwrap.location import Passthrough

    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        Matrix(cfg, transport=fake, sleep=no_sleep).matrix(
            MatrixOptions(**_MATRIX_TWO_BY_TWO, passthrough=Passthrough(body={"timeoutMs": bad}))
        )
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert fake.calls == []


def test_timeout_ms_accepts_a_fractional_number():
    from thinwrap.location.passthrough import Passthrough
    from thinwrap.location.poll import extract_timeout_ms

    deadline, clean = extract_timeout_ms(Passthrough(body={"timeoutMs": 1500.5, "keep": 1}))
    assert deadline == 1.5005
    assert clean.body == {"keep": 1}


# --- a 2xx body the connector cannot interpret is `unknown`, not zero results --

@pytest.mark.parametrize("cfg", [GoogleConfig("k"), HereConfig("k"), MapboxConfig("pk"), TomTomConfig("k"), EsriConfig(api_key="k")])
@pytest.mark.parametrize("body", ["", "<html>busy</html>", "[]", '{"unexpected": true}'])
def test_geocode_uninterpretable_2xx_body_is_unknown(cfg, body):
    with pytest.raises(ConnectorError) as ei:
        Geocoding(cfg, transport=FakeTransport(resp(200, body))).geocode(GeocodeOptions(address="x"))
    assert ei.value.provider_code == ProviderCode.UNKNOWN
    assert ei.value.status_code == 200


@pytest.mark.parametrize("cfg,body", [
    (GoogleConfig("k"), '{"status":"ZERO_RESULTS","results":[]}'),
    (HereConfig("k"), '{"items":[]}'),
    (MapboxConfig("pk"), '{"type":"FeatureCollection","features":[]}'),
    (TomTomConfig("k"), '{"results":[]}'),
    (EsriConfig(api_key="k"), '{"candidates":[]}'),
])
def test_geocode_well_formed_zero_results_stays_empty(cfg, body):
    result = Geocoding(cfg, transport=FakeTransport(resp(200, body))).geocode(GeocodeOptions(address="x"))
    assert result.candidates == []


@pytest.mark.parametrize("cfg", [GoogleConfig("k"), HereConfig("k"), MapboxConfig("pk"), TomTomConfig("k"), EsriConfig(api_key="k")])
def test_autocomplete_uninterpretable_2xx_body_is_unknown(cfg):
    with pytest.raises(ConnectorError) as ei:
        Geocoding(cfg, transport=FakeTransport(resp(200, "<html/>"))).autocomplete(
            AutocompleteOptions(input="x", location=LatLng(1, 1))
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_google_autocomplete_empty_object_is_zero_results():
    # proto3 JSON drops an empty repeated field: no predictions arrive as `{}`.
    result = Geocoding(GoogleConfig("k"), transport=FakeTransport(resp(200, "{}"))).autocomplete(
        AutocompleteOptions(input="x")
    )
    assert result.predictions == []


# --- a partial viewport is omitted, never 0-filled -----------------------------

@pytest.mark.parametrize("cfg,body", [
    (HereConfig("k"), '{"items":[{"address":{"label":"a"},"position":{"lat":1,"lng":2},"mapView":{"south":1,"west":2,"north":3}}]}'),
    (TomTomConfig("k"), '{"results":[{"address":{"freeformAddress":"a"},"position":{"lat":1,"lon":2},"viewport":{"topLeftPoint":{"lat":3},"btmRightPoint":{"lat":1,"lon":4}}}]}'),
    (EsriConfig(api_key="k"), '{"candidates":[{"address":"a","location":{"x":2,"y":1},"extent":{"xmin":1,"ymin":1,"xmax":3}}]}'),
    (MapboxConfig("pk"), '{"features":[{"geometry":{"coordinates":[2,1]},"properties":{"full_address":"a","bbox":[1,1,null,3]}}]}'),
])
def test_partial_viewport_is_omitted(cfg, body):
    result = Geocoding(cfg, transport=FakeTransport(resp(200, body))).geocode(GeocodeOptions(address="x"))
    assert len(result.candidates) == 1
    assert result.candidates[0].viewport is None


def test_mapbox_feature_with_non_numeric_coordinates_is_skipped():
    body = '{"features":[{"geometry":{"coordinates":["2",1]},"properties":{"full_address":"a"}}]}'
    result = Geocoding(MapboxConfig("pk"), transport=FakeTransport(resp(200, body))).geocode(GeocodeOptions(address="x"))
    assert result.candidates == []


# --- Esri reverseGeocode "no address here" is the zero-results answer ----------

def test_esri_reverse_geocode_unable_to_find_address_returns_empty():
    body = (
        '{"error":{"code":400,"message":"Cannot perform query. Invalid query parameters.",'
        '"details":["Unable to find address for the specified location."]}}'
    )
    result = Geocoding(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).reverse_geocode(
        ReverseGeocodeOptions(location=LatLng(0, -30))
    )
    assert result.candidates == []
    assert result.raw["error"]["code"] == 400


def test_esri_reverse_geocode_other_400_is_still_invalid_request():
    body = '{"error":{"code":400,"message":"Invalid location","details":[]}}'
    with pytest.raises(ConnectorError) as ei:
        Geocoding(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).reverse_geocode(
            ReverseGeocodeOptions(location=LatLng(0, -30))
        )
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_a_route_with_no_legs_carries_the_http_status():
    body = '{"code":"Ok","routes":[{"distance":1,"duration":1,"geometry":"","legs":[]}]}'
    with pytest.raises(ConnectorError) as ei:
        Routing(MapboxConfig("pk"), transport=FakeTransport(resp(200, body))).route(
            RoutingOptions(waypoints=[LatLng(1, 1), LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE
    assert ei.value.status_code == 200
