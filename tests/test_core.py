from __future__ import annotations

import json
import math
import os

import pytest

from thinwrap.location.coordinate import assert_finite, join_coords, to_lat_lng_string, to_lng_lat_string
from thinwrap.location.errors import ConnectorError, ProviderCode, provider_error
from thinwrap.location.isochrone_validate import validate_isochrone_cap
from thinwrap.location.latlng import LatLng
from thinwrap.location.passthrough import Passthrough, merge_passthrough
from thinwrap.location.polyline import (
    decode_flex_polyline,
    decode_polyline,
    encode_esri_paths,
    encode_polyline,
)

_FIXTURE = os.path.join(os.path.dirname(__file__), "data", "parity-vectors.json")


def _load():
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _ll(d):
    return LatLng(lat=d["lat"], lng=d["lng"])


def _close(a, b, eps=1e-9):
    return len(a) == len(b) and all(abs(x.lat - y["lat"]) <= eps and abs(x.lng - y["lng"]) <= eps for x, y in zip(a, b))


def test_polyline_parity_vectors():
    f = _load()
    for v in f["encodePolyline"]:
        assert encode_polyline([_ll(p) for p in v["input"]]) == v["expected"], v["name"]
    for v in f["decodePolyline"]:
        assert _close(decode_polyline(v["input"]), v["expected"]), v["name"]
    for v in f["decodeFlexPolyline"]:
        assert _close(decode_flex_polyline(v["input"]), v["expected"]), v["name"]
    for v in f["encodeEsriPaths"]:
        got = encode_esri_paths([[_ll(p) for p in path] for path in v["input"]])
        assert got == v["expected"], v["name"]


def test_polyline_roundtrip():
    coords = [LatLng(38.5, -120.2), LatLng(40.7, -120.95), LatLng(43.252, -126.453)]
    assert _close(decode_polyline(encode_polyline(coords)), [{"lat": c.lat, "lng": c.lng} for c in coords], eps=1e-5)


def test_encode_rejects_nonfinite():
    with pytest.raises(ConnectorError) as ei:
        encode_polyline([LatLng(math.nan, 0)])
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_decode_malformed():
    with pytest.raises(ConnectorError) as ei:
        decode_polyline("_p~iF~ps|U_ulL_")
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_merge_passthrough_deep_and_wins():
    body, headers, query = merge_passthrough(
        {"a": 1, "nested": {"x": 1}},
        {"Authorization": "Bearer k"},
        Passthrough(body={"a": 9, "nested": {"y": 2}}, headers={"X-Trace": "abc"}, query={"pretty": "true"}),
        {"key": "orig"},
    )
    assert body == {"a": 9, "nested": {"x": 1, "y": 2}}
    assert headers["Authorization"] == "Bearer k" and headers["X-Trace"] == "abc"
    assert query == {"key": "orig", "pretty": "true"}


def test_merge_passthrough_none_safe():
    body, headers, query = merge_passthrough({"a": 1}, {"H": "v"}, None, {"q": "v"})
    assert body == {"a": 1} and headers == {"H": "v"} and query == {"q": "v"}


def test_provider_error_retry_after():
    err = provider_error(429, {"retry-after": "30"}, {"message": "slow down"}, ProviderCode.RATE_LIMITED, "slow down")
    assert err.status_code == 429
    assert err.provider_code == ProviderCode.RATE_LIMITED
    assert err.provider_message == "slow down; retry after 30 seconds"
    assert isinstance(err.cause, dict) and err.cause["retryAfter"] == "30" and err.cause["message"] == "slow down"


def test_provider_error_no_retry():
    body = {"error": "bad"}
    err = provider_error(400, {}, body, ProviderCode.INVALID_REQUEST, "bad")
    assert err.cause == body and err.provider_message == "bad"


def test_coordinate_formatting():
    c = LatLng(40.7128, -74.006)
    assert to_lat_lng_string(c) == "40.7128,-74.006"
    assert to_lng_lat_string(c) == "-74.006,40.7128"
    assert join_coords([LatLng(1, 2), LatLng(3, 4)], "lnglat", ";") == "2,1;4,3"


def test_assert_finite():
    with pytest.raises(ConnectorError) as ei:
        assert_finite(LatLng(math.inf, 0), "t")
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST


def test_validate_isochrone_cap():
    validate_isochrone_cap([300, 600])  # ok
    for bad in ([], [0], [-1], [math.nan], [1, 2, 3, 4, 5]):
        with pytest.raises(ConnectorError):
            validate_isochrone_cap(bad)
