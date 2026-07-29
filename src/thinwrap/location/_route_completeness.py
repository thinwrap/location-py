"""Route-response completeness check, shared by the Mapbox and OSRM connectors."""

from __future__ import annotations

from typing import Any

from .errors import ConnectorError, ProviderCode


def assert_route_has_legs(actual_legs: int, waypoint_count: int, provider: str, raw: Any) -> None:
    """Verify a routing response carries any route detail at all.

    This is the connector-side answer to *"expose waypoints[] so I can check the
    response is complete"*. Exporting a count would make every consumer re-derive
    the same invariant, so the wrapper — which knows what it asked for — checks
    instead. An empty ``legs`` list for a multi-waypoint request means the response
    arrived structurally intact but describes no journey: the totals and polyline
    may still look plausible while there is nothing to iterate.

    **Why this is not an exact leg-count check.** ``len(legs) == N - 1`` looks like
    the stronger invariant, but it has a false positive that would break valid code:
    Mapbox and OSRM both support *silent waypoints* (``waypoints=0;2``), where a
    coordinate is used for routing without producing its own leg, so a consumer
    setting that through ``passthrough`` has a legitimately lower count. There is
    also no live evidence of any provider returning a short-but-non-empty ``legs``
    list — so enforcing the exact count would trade a real false positive for a
    speculative catch. Consumers that know their own waypoint semantics can still
    assert it.
    """
    if actual_legs > 0 or waypoint_count < 2:
        return

    msg = f"{provider} returned a route with no legs for {waypoint_count} waypoints"
    raise ConnectorError(ProviderCode.NO_ROUTE, message=msg, provider_message=msg, cause=raw)
