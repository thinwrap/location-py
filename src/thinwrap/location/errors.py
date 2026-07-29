"""The typed error surface: ProviderCode + ConnectorError."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from ._util import redact_credentials


class ProviderCode(str, Enum):
    """Normalized, cross-provider error classification. Values are byte-identical
    across the thinwrap location siblings (TypeScript, PHP, Go, Python)."""

    # Canonical (shared across thinwrap scopes).
    INVALID_RECIPIENT = "invalid_recipient"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"

    # Location-extended.
    UNSUPPORTED_FIELD = "unsupported_field"
    UNSUPPORTED_OPTION = "unsupported_option"
    UNSUPPORTED_TRAVEL_MODE = "unsupported_travel_mode"
    PROFILE_NOT_CONFIGURED = "profile_not_configured"
    MATRIX_POLLING_TIMEOUT = "matrix_polling_timeout"

    #: The provider answered successfully but no route exists between the given
    #: waypoints. Distinct from ``INVALID_REQUEST``: the request was well-formed,
    #: so this is a business outcome to branch on rather than a bug to fix.
    #:
    #: The vendors agree on nothing here — Google answers HTTP 200 with the
    #: ``routes`` key absent, HERE 200 with ``routes: []`` plus a ``notices[]``
    #: code, Mapbox ``code: "NoRoute"`` on either 200 or 422, OSRM the same codes
    #: on a 400, TomTom a 400 with ``detailedError.code``, Esri a 200 with an
    #: in-body ``error.code: 400`` whose ``details[]`` name an *unlocated* stop.
    #:
    #: In practice it means a waypoint could not be matched to the road network:
    #: every provider tested routes Reykjavik->Oslo via ferry, so a genuinely
    #: disconnected network is close to unreachable.
    NO_ROUTE = "no_route"

    #: The request exceeded the transport's timeout before any response arrived.
    TIMEOUT = "timeout"


class ConnectorError(Exception):
    """The single typed error every operation can raise.

    There is deliberately no ``retry_after_seconds`` attribute: the raw
    ``Retry-After`` header rides in ``cause['retryAfter']`` and its parsed
    seconds are woven into ``provider_message``.
    """

    def __init__(
        self,
        provider_code: ProviderCode,
        *,
        status_code: int | None = None,
        provider_message: str | None = None,
        message: str | None = None,
        cause: Any = None,
    ) -> None:
        self.provider_code = provider_code
        self.status_code = status_code
        self.provider_message = provider_message
        self.cause = cause
        super().__init__(message or provider_message or "Connector error")


def invalid_request(msg: str) -> ConnectorError:
    """Build a pre-flight invalid_request error (no HTTP round-trip)."""
    return ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)


def _redact_cause(value: Any) -> Any:
    """Defense-in-depth: mask credential-looking substrings inside a decoded
    vendor error body (should a provider ever echo the request URL/key) while
    preserving its shape, so structured access like ``cause['retryAfter']`` keeps
    working. A credential-free body is returned value-equal to the input."""
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, dict):
        return {k: _redact_cause(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_cause(v) for v in value]
    return value


def classified_error(code: ProviderCode, status: int | None, cause: Any, msg: str) -> ConnectorError:
    """Build a ConnectorError with an EXPLICIT code, redacting the message and
    (shape-preserving) the cause — for in-body vendor errors (HTTP 2xx with an
    error payload) that carry a known classification but must not leak
    credential-looking substrings from the body."""
    msg = redact_credentials(msg)
    return ConnectorError(code, status_code=status, message=msg, provider_message=msg, cause=_redact_cause(cause))


def unknown_error(status: int | None, cause: Any, msg: str) -> ConnectorError:
    """Build an unknown-classification error for a malformed vendor body."""
    return classified_error(ProviderCode.UNKNOWN, status, cause, msg)


def provider_error(
    status: int,
    headers: Mapping[str, str],
    body: Any,
    code: ProviderCode,
    provider_message: str | None,
) -> ConnectorError:
    """Build a ConnectorError for a non-2xx response. The vendor->code
    classification and message are computed per-connector; the shared
    Retry-After surfacing lives here (raw header merged into the decoded body
    under ``retryAfter``; parsed seconds appended to the message)."""
    retry_after = headers.get("retry-after", "")
    cause: Any = body
    msg = provider_message

    if retry_after:
        merged: dict[str, Any] = {}
        if isinstance(body, dict):
            merged.update(body)
        merged["retryAfter"] = retry_after
        cause = merged
        try:
            secs = int(retry_after.strip())
        except (ValueError, AttributeError):
            secs = None
        if secs is not None:
            base = (msg or "").strip()
            msg = f"{base}; retry after {secs} seconds" if base else f"retry after {secs} seconds"

    return ConnectorError(
        code,
        status_code=status,
        provider_message=redact_credentials(msg) if msg else msg,
        cause=_redact_cause(cause),
    )
