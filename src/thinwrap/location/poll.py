"""Async-matrix polling parameters and the timeout-override extractor.

The submit/poll/retrieve loops live inline in the HERE and TomTom matrix
connectors (each has a slightly different completion check), sharing the backoff
constants here. All durations are in seconds (time.sleep units).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .passthrough import Passthrough

POLL_INITIAL_DELAY = 1.0
POLL_MAX_DELAY = 5.0
POLL_BACKOFF = 1.5
POLL_DEFAULT_DEADLINE = 60.0


def extract_timeout_ms(pt: Optional[Passthrough]) -> Tuple[float, Optional[Passthrough]]:
    """Pull a consumer-supplied ``timeoutMs`` override out of the passthrough
    body and return (deadline_seconds, passthrough-copy-without-timeoutMs). The
    caller's Passthrough is not mutated."""
    deadline = POLL_DEFAULT_DEADLINE
    if pt is None or not pt.body:
        return deadline, pt
    if "timeoutMs" not in pt.body:
        return deadline, pt
    ms = _to_ms(pt.body["timeoutMs"])
    if ms > 0:
        deadline = ms / 1000.0
    clean_body = {k: v for k, v in pt.body.items() if k != "timeoutMs"}
    return deadline, Passthrough(body=clean_body, headers=pt.headers, query=pt.query)


def _to_ms(raw: Any) -> float:
    if isinstance(raw, bool):  # bool is an int subclass — exclude it
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(int(raw.strip()))
        except ValueError:
            return 0.0
    return 0.0
