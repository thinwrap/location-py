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
            # A BYO transport (requests/httpx/…) may embed the full request URL —
            # including credential query params — in its exception message. Keep
            # only a redacted STRING.
            msg = redact_credentials(str(exc))
        # Raised OUTSIDE the except block on purpose: implicit exception chaining
        # only happens while an exception is being handled, so neither .__cause__
        # nor .__context__ ends up referencing the (URL-bearing) transport
        # exception. `from None` alone would still leave it on .__context__,
        # which Sentry/Rollbar walk regardless of __suppress_context__.
        raise ConnectorError(
            ProviderCode.PROVIDER_UNAVAILABLE,
            message=msg,
            provider_message=msg,
            cause=msg,
        )
