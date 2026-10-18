"""Cross-provider isochrone input validation: break values and departure time."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from ._util import iso_seconds_string
from .coordinate import is_finite
from .errors import ConnectorError, ProviderCode, invalid_request

# Maximum contour breaks across all four isochrone providers (Mapbox's ceiling).
MAX_ISOCHRONE_VALUES = 4


def validate_isochrone_cap(values: Sequence[float]) -> None:
    if len(values) < 1:
        msg = "isochrone requires at least one break value"
        raise ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)
    for v in values:
        if not is_finite(v) or v <= 0:
            msg = "isochrone break values must be finite numbers greater than 0"
            raise ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)
    if len(values) > MAX_ISOCHRONE_VALUES:
        msg = f"Maximum {MAX_ISOCHRONE_VALUES} values supported (Mapbox native ceiling)"
        raise ConnectorError(ProviderCode.INVALID_REQUEST, message=msg, provider_message=msg)


def isochrone_departure(value: Any, label: str) -> str:
    """Validate an isochrone ``departure_time`` and return the string to send.

    An ISO-8601 date-time string is sent verbatim, so a caller's offset — or its
    absence, which a vendor may read as local time — is preserved. A
    ``datetime`` is sent as UTC with seconds precision (a naive one is read as
    UTC, as everywhere else in the library).

    Raises:
        ConnectorError: ``invalid_request`` for a string that is not an ISO-8601
            date-time. Forwarded unchecked, the vendor either rejects it with an
            opaque 4xx or, worse, ignores it and computes a time-independent
            isochrone the caller did not ask for.
        TypeError: ``value`` is neither a string nor a ``datetime`` — a wrong
            argument type, not request data."""
    if not isinstance(value, (datetime, str)):
        raise TypeError(f"{label} departure_time must be an ISO-8601 string or a datetime, got {type(value).__name__}")
    if isinstance(value, datetime):
        return iso_seconds_string(value)
    if isinstance(value, str) and "T" in value.upper():
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
            return value
        except ValueError:
            pass
    raise invalid_request(
        f"{label} departure_time must be an ISO-8601 date-time string (or a datetime), got {value!r}"
    )
