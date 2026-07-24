"""The per-operation input escape hatch and its merge semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class Passthrough:
    """Keys are forwarded verbatim: ``body`` is deep-merged into the connector's
    request body; ``headers`` and ``query`` are shallow-merged. Consumer values
    intentionally OVERRIDE connector-set values (including auth) — there is
    deliberately no reserved-key protection."""

    body: Optional[Mapping[str, Any]] = None
    headers: Optional[Mapping[str, str]] = None
    query: Optional[Mapping[str, str]] = None


def merge_passthrough(
    connector_body: Mapping[str, Any],
    connector_headers: Mapping[str, str],
    pt: Optional[Passthrough],
    connector_query: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    pt_body = pt.body if pt and pt.body else {}
    pt_headers = pt.headers if pt and pt.headers else {}
    pt_query = pt.query if pt and pt.query else {}

    body = _deep_merge(dict(connector_body), pt_body)
    headers = {**dict(connector_headers), **dict(pt_headers)}
    query = {**dict(connector_query or {}), **dict(pt_query)}
    return body, headers, query


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for k, v in source.items():
        tv = result.get(k)
        if isinstance(v, dict) and isinstance(tv, dict):
            result[k] = _deep_merge(tv, v)
        else:
            result[k] = v
    return result
