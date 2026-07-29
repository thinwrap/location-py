"""Nil/absence-tolerant helpers for navigating a decoded JSON tree on the error
and raw paths, where full typing would be overkill."""

from __future__ import annotations

from typing import Any, List, Optional


def jget(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else None


def jstr(v: Any) -> str:
    return v if isinstance(v, str) else ""


def jnum(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    return float(v) if isinstance(v, (int, float)) else 0.0


def jnum_opt(v: Any) -> Optional[float]:
    """Read a JSON number, returning ``None`` when absent or not a number.

    Use this wherever a zero would be indistinguishable from real data — notably
    coordinates, where ``jnum``'s 0.0 fallback fabricates a (0,0) "Null Island"
    position that a consumer cannot tell apart from a genuine result off the
    coast of Africa.
    """
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def jlist(v: Any) -> Optional[List[Any]]:
    return v if isinstance(v, list) else None
