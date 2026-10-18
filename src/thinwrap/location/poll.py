"""Async-matrix polling parameters and the timeout-override extractor.

The submit/poll/retrieve loops live inline in the HERE and TomTom matrix
connectors (each has a slightly different completion check), sharing the backoff
constants here. All durations are in seconds (time.sleep units).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .coordinate import is_finite
from .errors import invalid_request
from .passthrough import Passthrough

POLL_INITIAL_DELAY = 1.0
POLL_MAX_DELAY = 5.0
POLL_BACKOFF = 1.5
POLL_DEFAULT_DEADLINE = 60.0


def extract_timeout_ms(pt: Optional[Passthrough]) -> Tuple[float, Optional[Passthrough]]:
    """Pull a consumer-supplied ``timeoutMs`` override out of the passthrough
    body and return (deadline_seconds, passthrough-copy-without-timeoutMs). The
    caller's Passthrough is not mutated.

    ``timeoutMs`` must be a finite number of milliseconds greater than 0; anything
    else raises ``invalid_request`` before a request is sent. A value that cannot be
    a deadline — a string, NaN, zero, a negative — is a caller mistake, and falling
    back to the default would poll for a budget the caller never asked for."""
    deadline = POLL_DEFAULT_DEADLINE
    if pt is None or not pt.body:
        return deadline, pt
    if "timeoutMs" not in pt.body:
        return deadline, pt
    deadline = _to_ms(pt.body["timeoutMs"]) / 1000.0
    clean_body = {k: v for k, v in pt.body.items() if k != "timeoutMs"}
    return deadline, Passthrough(body=clean_body, headers=pt.headers, query=pt.query)


def _to_ms(raw: Any) -> float:
    # bool is an int subclass, and a numeric string is not a number: both rejected.
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            ms = float(raw)
        except OverflowError:  # an int too large for a float
            ms = float("inf")
        if is_finite(ms) and ms > 0:
            return ms
    raise invalid_request(
        f"passthrough timeoutMs must be a finite number of milliseconds greater than 0 (got {raw!r})"
    )
