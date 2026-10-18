"""Coordinate finiteness checks and wire formatting."""

from __future__ import annotations

import math
import numbers
from typing import Any, Iterable

from .errors import ConnectorError, ProviderCode
from .latlng import LatLng


def is_finite(f: float) -> bool:
    return not (math.isnan(f) or math.isinf(f))


def assert_finite(c: LatLng, context: str = "") -> None:
    """Reject NaN / non-finite coordinates before they reach the wire.
    Out-of-range (but finite) lat/lng pass through verbatim (thin-wrapper)."""
    if not is_finite(c.lat) or not is_finite(c.lng):
        where = f" ({context})" if context else ""
        msg = f"Coordinate lat/lng must be finite numbers{where}"
        raise ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)


def assert_finite_number(value: Any, label: str) -> None:
    """Reject a non-finite scalar (a search radius) before it reaches the wire.

    NaN/inf is runtime data a vendor would reject, so it is ``invalid_request``;
    without this it reaches :func:`fmt_coord`, whose ``int()`` raises a bare
    ``ValueError``/``OverflowError``.

    Raises:
        ConnectorError: ``invalid_request`` for NaN or ±inf.
        TypeError: ``value`` is not a real number (a string, ``None``, a bool) —
            a wrong argument type, not request data.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{label} must be a number, got {type(value).__name__}")
    if not is_finite(value):
        msg = f"{label} must be a finite number"
        raise ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)


def fmt_coord(f: float) -> str:
    """Shortest fixed-point decimal (never exponential); integral values without
    a trailing .0. ``repr(f)`` switches to scientific notation for |f| < 1e-4
    (e.g. "5e-05"), which geo APIs reject in a URL — so format fixed instead.
    Ten fractional digits is well beyond sub-millimetre geographic precision."""
    if f == int(f):
        return str(int(f))
    return f"{f:.10f}".rstrip("0").rstrip(".")


def to_lng_lat_string(c: LatLng) -> str:
    """'lng,lat' (OSRM / Mapbox convention)."""
    return f"{fmt_coord(c.lng)},{fmt_coord(c.lat)}"


def to_lat_lng_string(c: LatLng) -> str:
    """'lat,lng' (HERE / Google convention)."""
    return f"{fmt_coord(c.lat)},{fmt_coord(c.lng)}"


def join_coords(coords: Iterable[LatLng], fmt: str, separator: str) -> str:
    parts = []
    for c in coords:
        assert_finite(c, "join_coords")
        parts.append(to_lng_lat_string(c) if fmt == "lnglat" else to_lat_lng_string(c))
    return separator.join(parts)
