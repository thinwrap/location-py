"""Canonical ``waypoint_order`` validation, shared by every routing connector
that derives an optimized visiting sequence.

The canonical contract (see :class:`~thinwrap.location.routing.RoutingResult`) is
a complete permutation of ``[0..N-1]`` listing the INPUT waypoint indices in
visit order. A vendor can break that in ways a bounds check alone will not catch
— a sentinel value (Google returns ``[-1]`` when it declines to optimize), a
duplicated position, a short list — and the resulting list is then either wrong
or holds filler values that read as a real index. Consumers use
``waypoint_order`` to reorder their own collections, so a silently wrong
permutation corrupts their data. Both helpers therefore reject rather than
repair: an ordering that is not a complete permutation is **omitted** (``None``),
which the contract already documents as "the vendor returned no ordering".

Vendors express the sequence in one of two ways, hence two helpers:

* **Visit order** (HERE ``findsequence2``, Google/TomTom after projection) —
  already the canonical direction; validate with :func:`is_complete_waypoint_order`.
* **Visit position per input** (OSRM/Mapbox ``waypoint_index``, Esri
  ``Sequence``) — the INVERSE; invert with :func:`invert_waypoint_positions`.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence


def _as_index(value: Any) -> Optional[int]:
    """Coerce a vendor value to an index, or ``None`` when it is not one.

    ``bool`` is rejected explicitly (it is an ``int`` subclass in Python, so
    ``True`` would otherwise pass as index 1), and a float is accepted only when
    it is integral.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def is_complete_waypoint_order(order: Sequence[Any], expected_length: int) -> bool:
    """Whether ``order`` is a complete permutation of ``[0..expected_length-1]``.

    Correct length, integers only, all in range, no duplicates. Accepts
    unvalidated vendor data — non-integer entries are rejected, not coerced.
    """
    if len(order) != expected_length:
        return False
    seen = [False] * expected_length
    for value in order:
        index = _as_index(value)
        if index is None or index < 0 or index >= expected_length or seen[index]:
            return False
        seen[index] = True
    return True


def invert_waypoint_positions(
    positions: Sequence[Any], expected_length: int
) -> Optional[List[int]]:
    """Invert vendor visit-position data into the canonical ``waypoint_order``.

    ``positions[i]`` is the 0-based position input waypoint ``i`` occupies in the
    optimized route, so the result places each input index at its visit position
    (``order[positions[i]] = i``). Returns ``None`` when the data is absent,
    incomplete, or malformed — never a partially-filled list.
    """
    if len(positions) != expected_length:
        return None
    order: List[int] = [0] * expected_length
    filled = [False] * expected_length
    for input_index, position in enumerate(positions):
        index = _as_index(position)
        if index is None or index < 0 or index >= expected_length or filled[index]:
            return None
        filled[index] = True
        order[index] = input_index
    return order
