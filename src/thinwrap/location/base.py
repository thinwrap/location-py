"""BaseConnector — the shared HTTP seam."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ._util import build_url, compact_json, redact_credentials
from .errors import ConnectorError, ProviderCode
from .transport import HttpRequest, HttpResponse, Transport, default_transport


class BaseConnector:
    """Centralizes the injectable transport and transport-error normalization.
    Vendor specifics (request shape, result normalization, error mapping) stay
    in the per-provider connectors that subclass this."""

    def __init__(self, transport: Optional[Transport] = None) -> None:
        self._transport: Transport = transport or default_transport()

    def send_get(self, url: str, headers: Mapping[str, str] | None = None, query: Mapping[str, str] | None = None) -> HttpResponse:
        return self._invoke("GET", build_url(url, query), None, None, headers)

    def send_post_json(self, url: str, body: Any, headers: Mapping[str, str] | None = None, query: Mapping[str, str] | None = None) -> HttpResponse:
        try:
            data = compact_json(body)
        except (TypeError, ValueError) as exc:
            raise ConnectorError(
                ProviderCode.INVALID_REQUEST,
                message="Failed to encode request body",
                provider_message="Failed to encode request body",
                cause=exc,
            ) from exc
        return self._invoke("POST", build_url(url, query), data, "application/json", headers)

    def send_post_form(self, url: str, form: Mapping[str, str], headers: Mapping[str, str] | None = None, query: Mapping[str, str] | None = None) -> HttpResponse:
        from urllib.parse import urlencode

        data = urlencode(dict(form)).encode("utf-8")
        return self._invoke("POST", build_url(url, query), data, "application/x-www-form-urlencoded", headers)

    def _invoke(self, method: str, url: str, body: bytes | None, content_type: str | None, headers: Mapping[str, str] | None) -> HttpResponse:
        h: dict[str, str] = {}
        if content_type:
            h["Content-Type"] = content_type
        if headers:
            h.update(headers)
        try:
            return self._transport.send(HttpRequest(method=method, url=url, headers=h, body=body))
        except ConnectorError:
            raise
        except Exception as exc:  # transport failure (connection/DNS/timeout)
            # …unless the provider actually answered and the transport chose to
            # raise the non-2xx instead of returning it (`requests`' raise_for_status,
            # `httpx`'s raise_for_status, a bare urllib.error.HTTPError). Rebuilding
            # the response — rather than classifying here — keeps the per-connector
            # error mapping the single owner of status→code translation, so the
            # caller still gets the real status and the vendor message.
            rebuilt = _response_from_raised_http_error(exc)
            if rebuilt is not None:
                return rebuilt
            # A BYO transport (requests/httpx/…) may embed the full request URL —
            # including credential query params — in its exception message. Keep
            # only a redacted STRING.
            msg = redact_credentials(str(exc))
            code = _classify_transport_failure(exc)
        # Raised OUTSIDE the except block on purpose: implicit exception chaining
        # only happens while an exception is being handled, so neither .__cause__
        # nor .__context__ ends up referencing the (URL-bearing) transport
        # exception. `from None` alone would still leave it on .__context__,
        # which Sentry/Rollbar walk regardless of __suppress_context__.
        raise ConnectorError(
            code,
            message=msg,
            provider_message=msg,
            cause=msg,
        )


def _response_from_raised_http_error(exc: BaseException) -> Optional[HttpResponse]:
    """Rebuild an :class:`HttpResponse` from an exception that carries one, or
    return ``None`` when the failure is a genuine transport failure.

    :class:`Transport` requires a non-2xx to be RETURNED, and the bundled
    ``UrllibTransport`` obeys that. A BYO transport may not: ``requests`` and
    ``httpx`` both raise from ``raise_for_status()``, and a thin urllib wrapper
    can let :class:`urllib.error.HTTPError` escape. The provider answered, so the
    answer is what the caller must see — without this, every 400/429/503
    collapses into ``provider_unavailable`` with no status.

    Duck-typed on the shape (``.response.status_code``, ``.code`` + ``.read()``),
    never on an import: the library has zero runtime dependencies."""
    for candidate in (getattr(exc, "response", None), exc):
        if candidate is None:
            continue
        status = _read_http_status(candidate)
        if status is None:
            continue
        return HttpResponse(
            status=status,
            headers=_read_http_headers(candidate),
            body=_read_http_body(candidate),
        )
    return None


def _read_http_status(candidate: Any) -> Optional[int]:
    """The HTTP status a response-like object carries, if it plausibly is one."""
    status = getattr(candidate, "status_code", None)
    if status is None:
        status = getattr(candidate, "status", None)
    if status is None and (hasattr(candidate, "read") or hasattr(candidate, "headers")):
        # `code` alone is too generic to trust — urllib.error.HTTPError pairs it
        # with a readable body, an unrelated exception with a `.code` does not.
        status = getattr(candidate, "code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status if 200 <= status <= 599 else None


def _read_http_headers(candidate: Any) -> dict[str, str]:
    headers = getattr(candidate, "headers", None)
    if headers is None:
        return {}
    try:
        items = headers.items()
    except Exception:
        return {}
    return {str(k).lower(): str(v) for k, v in items}


def _read_http_body(candidate: Any) -> bytes:
    for attribute in ("content", "text", "body"):
        try:
            value = getattr(candidate, attribute, None)
        except Exception:
            # httpx raises on `.content` for an unread streaming response.
            continue
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8", "replace")
    read = getattr(candidate, "read", None)
    if callable(read):
        try:
            value = read()
        except Exception:
            return b""
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8", "replace")
    return b""


def _classify_transport_failure(exc: BaseException) -> ProviderCode:
    """Separate a timeout from a generic transport outage.

    A timeout is the one transport failure a caller acts on differently (back off
    and retry vs. treat the provider as down), and the redacted message
    deliberately hides the detail needed to tell them apart.

    ``socket.timeout`` is an alias of :class:`TimeoutError` on Python 3.10+, and
    ``urllib`` surfaces a read timeout as ``URLError(TimeoutError(...))`` — hence
    the ``__cause__``/``reason`` walk. A BYO transport that raises something else
    entirely still reports ``provider_unavailable``, exactly as before.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return ProviderCode.TIMEOUT
        reason = getattr(current, "reason", None)
        current = current.__cause__ or (reason if isinstance(reason, BaseException) else None)
    return ProviderCode.PROVIDER_UNAVAILABLE
