"""Small shared helpers: JSON, URL building, credential redaction, ISO time."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Mapping
from urllib.parse import urlencode


def compact_json(value: Any) -> bytes:
    """Encode as compact UTF-8 JSON (no spaces, non-ASCII preserved), matching
    JSON.stringify / Go's escape-HTML-off encoder. Non-finite floats (NaN/inf)
    raise ValueError rather than emitting the invalid JSON tokens
    NaN/Infinity."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def decode_json(data: bytes | None) -> Any:
    """Decode arbitrary JSON, returning None on empty/malformed input."""
    if not data:
        return None
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return None


def ok_status(status: int) -> bool:
    return 200 <= status < 300


def build_url(url: str, query: Mapping[str, str] | None) -> str:
    if not query:
        return url
    qs = urlencode(dict(query))
    if not qs:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{qs}"


_CRED_RE = re.compile(r"(?i)((?:access_token|apikey|api_key|key|token|sig|signature)=)[^&\s\"']+")


def redact_credentials(s: str) -> str:
    """Mask the values of common credential query params in a free-form string
    (e.g. a transport error message) to avoid leaking secrets."""
    return _CRED_RE.sub(r"\1[REDACTED]", s)


def iso_string(when: _dt.datetime) -> str:
    """Format a datetime as an ISO 8601 / RFC 3339 UTC string with millisecond
    precision and a 'Z' suffix, matching JS Date.prototype.toISOString().

    A naive datetime (``tzinfo is None``) is interpreted as UTC — not the host's
    local time — so output is host-timezone independent; tz-aware datetimes are
    converted from their own offset as before."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    utc = when.astimezone(_dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
