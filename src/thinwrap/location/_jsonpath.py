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


def jlist(v: Any) -> Optional[List[Any]]:
    return v if isinstance(v, list) else None
