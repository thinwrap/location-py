"""The cross-provider isochrone break-count validator."""

from __future__ import annotations

from typing import Sequence

from .coordinate import is_finite
from .errors import ConnectorError, ProviderCode

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
