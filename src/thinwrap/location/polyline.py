"""The four locked, cross-language-parity polyline utilities (stdlib only)."""

from __future__ import annotations

import math
from typing import Any, List, Sequence

from .coordinate import is_finite
from .errors import ConnectorError, ProviderCode
from .latlng import LatLng

_FLEX_DECODING_TABLE = [
    62, -1, -1, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, -1, -1, -1, -1, -1, -1, -1,
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21,
    22, 23, 24, 25, -1, -1, -1, -1, 63, -1, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
    36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51,
]


def _malformed() -> ConnectorError:
    return ConnectorError(ProviderCode.UNKNOWN, message="Malformed polyline", provider_message="Malformed polyline")


def _js_round(x: float) -> int:
    """JavaScript Math.round: round half toward +Infinity == floor(x + 0.5)."""
    return math.floor(x + 0.5)


def encode_polyline(coords: Sequence[LatLng]) -> str:
    """Encode coordinates into a Google-format precision-5 polyline string."""
    out: List[str] = []
    prev_lat = prev_lng = 0
    for c in coords:
        if not is_finite(c.lat) or not is_finite(c.lng):
            msg = "Cannot encode polyline: coordinate lat/lng must be finite numbers"
            raise ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)
        lat = _js_round(c.lat * 1e5)
        lng = _js_round(c.lng * 1e5)
        out.append(_encode_signed(lat - prev_lat))
        out.append(_encode_signed(lng - prev_lng))
        prev_lat, prev_lng = lat, lng
    return "".join(out)


def decode_polyline(encoded: str) -> List[LatLng]:
    """Decode a Google-format precision-5 polyline string."""
    coords: List[LatLng] = []
    index = 0
    lat = lng = 0
    n = len(encoded)
    while index < n:
        d_lat, index = _decode_signed(encoded, index)
        lat += d_lat
        d_lng, index = _decode_signed(encoded, index)
        lng += d_lng
        coords.append(LatLng(lat / 1e5, lng / 1e5))
    return coords


def decode_flex_polyline(encoded: str) -> List[LatLng]:
    """Decode a HERE flex-polyline string. Altitude is decoded and skipped.
    Reference: https://github.com/heremaps/flexible-polyline"""

    def decode_unsigned(idx: int) -> tuple[int, int]:
        result = 0
        shift = 0
        while True:
            if idx >= len(encoded):
                raise _malformed()
            di = ord(encoded[idx]) - 45
            if di < 0 or di >= len(_FLEX_DECODING_TABLE) or _FLEX_DECODING_TABLE[di] == -1:
                raise _malformed()
            b = _FLEX_DECODING_TABLE[di]
            idx += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        return result, idx

    def decode_zigzag(idx: int) -> tuple[int, int]:
        r, ni = decode_unsigned(idx)
        val = ~(r >> 1) if (r & 1) else (r >> 1)
        return val, ni

    idx = 0
    _, idx = decode_unsigned(idx)  # header value 1: format version
    header2, idx = decode_unsigned(idx)
    precision = header2 & 0x0F
    has_third_dim = (header2 >> 4) & 0x07

    factor = 10 ** precision
    coords: List[LatLng] = []
    lat = lng = 0
    while idx < len(encoded):
        d_lat, idx = decode_zigzag(idx)
        lat += d_lat
        d_lng, idx = decode_zigzag(idx)
        lng += d_lng
        if has_third_dim:
            _, idx = decode_zigzag(idx)
        coords.append(LatLng(lat / factor, lng / factor))
    return coords


def encode_esri_paths(paths: Sequence[Sequence[LatLng]]) -> dict[str, Any]:
    """Convert LatLng paths into an ESRI-JSON ``paths`` geometry object. Each
    point becomes an ``[lng, lat]`` pair; the CRS is fixed at WGS-84."""
    return {
        "paths": [[[p.lng, p.lat] for p in path] for path in paths],
        "spatialReference": {"wkid": 4326},
    }


def _encode_signed(value: int) -> str:
    v = ~(value << 1) if value < 0 else (value << 1)
    out: List[str] = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def _decode_signed(encoded: str, index: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if index >= len(encoded):
            raise _malformed()
        b = ord(encoded[index]) - 63
        if b < 0:
            raise _malformed()
        index += 1
        result |= (b & 0x1F) << shift
        shift += 5
        if b < 0x20:
            break
    val = ~(result >> 1) if (result & 1) else (result >> 1)
    return val, index
