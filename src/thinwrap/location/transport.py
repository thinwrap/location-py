"""The bring-your-own HTTP seam.

The library never imports a third-party HTTP client. It defines a tiny Transport
contract and ships a stdlib ``urllib`` implementation as the default. A consumer
who prefers ``httpx`` / ``requests`` implements ``Transport`` and injects it via
the facade's ``transport=`` argument — no runtime dependency is forced on anyone.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, runtime_checkable

# The default transport is HTTPS-only. This closes the stdlib opener's file://,
# ftp:// and data: handlers (so a config-/consumer-supplied base_url cannot turn
# into a local-file read) AND refuses cleartext http. A consumer with a
# self-hosted http endpoint (e.g. OSRM on a private network) injects their own
# Transport rather than relaxing TLS by default.
_ALLOWED_SCHEMES = ("https",)

# Default socket timeout (seconds) so a blackholed connection cannot hang the
# calling thread indefinitely. Consumers needing a different budget inject their
# own Transport.
_DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]  # keys lower-cased
    body: bytes


@runtime_checkable
class Transport(Protocol):
    """Send one request, return one response. Raise on transport failure
    (connection/DNS/timeout) — the connector normalizes that to
    provider_unavailable. Non-2xx HTTP responses must be RETURNED, not raised."""

    def send(self, request: HttpRequest) -> HttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        # Never follow redirects: a 3xx surfaces as a response (and then an
        # error) rather than silently re-sending auth headers to the target.
        return None


class UrllibTransport:
    """Default synchronous transport built on the standard library."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._timeout = timeout

    def send(self, request: HttpRequest) -> HttpResponse:
        scheme = urllib.parse.urlsplit(request.url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise urllib.error.URLError(f"unsupported URL scheme: {scheme or '(none)'}")
        req = urllib.request.Request(
            request.url,
            data=request.body,
            method=request.method,
            headers=dict(request.headers),
        )
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                return HttpResponse(
                    status=resp.status,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=resp.read(),
                )
        except urllib.error.HTTPError as exc:
            # urllib raises for 4xx/5xx and (with _NoRedirect) 3xx — capture the
            # response so the connector maps it, rather than treating it as a
            # transport failure.
            hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
            return HttpResponse(status=exc.code, headers=hdrs, body=exc.read())


_default_transport: UrllibTransport | None = None


def default_transport() -> Transport:
    global _default_transport
    if _default_transport is None:
        _default_transport = UrllibTransport()
    return _default_transport
