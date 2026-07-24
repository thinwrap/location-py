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
