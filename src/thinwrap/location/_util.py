"""Small shared helpers: JSON, URL building, credential redaction, ISO time."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Mapping, Sequence, Union
from urllib.parse import urlencode

#: A query-string value: one string, or a sequence of strings emitted as the SAME
#: key repeated (``avoid=a&avoid=b``) — the only list form some vendors accept.
QueryValue = Union[str, Sequence[str]]


def compact_json(value: Any) -> bytes:
    """Encode as compact UTF-8 JSON (no spaces, non-ASCII preserved), matching
    JSON.stringify / Go's escape-HTML-off encoder. Non-finite floats (NaN/inf)
    raise ValueError rather than emitting the invalid JSON tokens
    NaN/Infinity."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _reject_non_finite(token: str) -> Any:
    raise ValueError(f"non-finite JSON token: {token}")


def decode_json(data: bytes | None) -> Any:
    """Decode arbitrary JSON, returning None on empty/malformed input.

    ``NaN``/``Infinity``/``-Infinity`` count as malformed: they are not JSON (RFC
    8259), and the stdlib parser accepts them by default, so a vendor body carrying
    one would otherwise surface as a NaN distance or duration the caller cannot tell
    from a real value."""
    if not data:
        return None
    try:
        return json.loads(data, parse_constant=_reject_non_finite)
    except (ValueError, TypeError):
        return None


def ok_status(status: int) -> bool:
    return 200 <= status < 300


def build_url(url: str, query: Mapping[str, QueryValue] | None) -> str:
    if not query:
        return url
    # doseq: a list value becomes repeated keys rather than its Python repr. TomTom
    # calculateRoute rejects a comma-joined `avoid` outright ("Invalid avoid value")
    # and only accepts the parameter repeated. A plain str is still one value.
    qs = urlencode(dict(query), doseq=True)
    if not qs:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{qs}"


_CRED_RE = re.compile(r"(?i)((?:access_token|apikey|api_key|key|token|sig|signature)=)[^&\s\"']+")


# `scheme://user:pass@host` — the userinfo part of a URL embedded in free text.
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@?#\"']+@")


def redact_credentials(s: str) -> str:
    """Mask the values of common credential query params, and the userinfo of any
    ``scheme://user:pass@host`` URL, in a free-form string (e.g. a transport error
    message) to avoid leaking secrets."""
    return _URL_USERINFO_RE.sub(r"\1[REDACTED]@", _CRED_RE.sub(r"\1[REDACTED]", s))


def redact_url_userinfo(url: str) -> str:
    """Replace the userinfo of a single URL (``https://user:pass@host`` or a
    scheme-less ``user:pass@host``) with ``[REDACTED]``.

    A self-hosted endpoint behind basic auth is commonly configured with the
    credentials inline, so a config URL is a secret-bearing string: any message or
    repr that shows it must show only the host part."""
    sep = url.find("://")
    start = sep + 3 if sep >= 0 else 0
    end = len(url)
    for ch in "/?#":
        i = url.find(ch, start)
        if i != -1:
            end = min(end, i)
    at = url.rfind("@", start, end)
    if at == -1:
        return url
    return url[:start] + "[REDACTED]@" + url[at + 1 :]


def traffic_requested(mode: Any) -> bool:
    """Whether a ``TrafficMode`` asks for traffic.

    ``LIVE`` is a deprecated alias of ``TRAFFIC``; deciding it in one place is
    what keeps the alias identical everywhere, so connectors call this instead of
    comparing to either member. ``TrafficMode`` is a ``str`` enum, so a raw
    ``"traffic"``/``"live"`` string is recognised too."""
    return mode in ("traffic", "live")


def _require_datetime(when: Any) -> None:
    if not isinstance(when, _dt.datetime):
        raise TypeError(f"departure_time must be a datetime, got {type(when).__name__}")


def iso_string(when: _dt.datetime) -> str:
    """Format a datetime as an ISO 8601 / RFC 3339 UTC string with millisecond
    precision and a 'Z' suffix, matching JS Date.prototype.toISOString().

    A naive datetime (``tzinfo is None``) is interpreted as UTC — not the host's
    local time — so output is host-timezone independent; tz-aware datetimes are
    converted from their own offset as before.

    Raises:
        TypeError: ``when`` is not a ``datetime``. Only Esri parses an ISO string
            for ``departure_time``; everywhere else a string is a wrong argument
            type, and would otherwise surface as an ``AttributeError`` from deep
            inside the formatter."""
    _require_datetime(when)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    utc = when.astimezone(_dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def iso_seconds_string(when: _dt.datetime) -> str:
    """Format a datetime as ISO 8601 at *seconds* precision, zone designator
    retained ('2026-08-07T03:06:00Z').

    HERE's legacy WPS endpoint (findsequence2) rejects fractional seconds
    outright — 'Bad Format for Date and Time', HTTP 400 — so the millisecond form
    :func:`iso_string` produces breaks every optimized route carrying a departure
    time. Precision is a property of the endpoint, not of the value: /v8/routes
    accepts the fractional form and keeps using :func:`iso_string`.

    Naive datetimes are interpreted as UTC, exactly as :func:`iso_string` does.

    Raises:
        TypeError: ``when`` is not a ``datetime``, as for :func:`iso_string`."""
    _require_datetime(when)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
