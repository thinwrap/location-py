"""Shared isochrone contour geometry: ring closure, RFC 7946 winding, and the
Polygon-vs-MultiPolygon choice every isochrone connector makes the same way."""

from __future__ import annotations

from typing import List, Sequence, Union

from .isochrone import MultiPolygon, Polygon

Ring = List[List[float]]


def signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace area of a ``[x, y]`` ring: positive when counter-clockwise,
    negative when clockwise, 0 when degenerate. Closure does not matter — the
    closing edge contributes the same either way."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        total += x1 * y2 - x2 * y1
    return total / 2


def _closed(ring: Sequence[Sequence[float]]) -> Ring:
    out = [[p[0], p[1]] for p in ring]
    if out[0][0] != out[-1][0] or out[0][1] != out[-1][1]:
        out.append([out[0][0], out[0][1]])
    return out


def _wound(ring: Sequence[Sequence[float]], ccw: bool) -> Ring:
    """The ring closed and wound as asked. A degenerate (zero-area) ring has no
    winding to fix, so it is only closed."""
    out = _closed(ring)
    area = signed_area(out)
    if (ccw and area < 0) or (not ccw and area > 0):
        out.reverse()
    return out


def normalize_polygon(rings: Sequence[Sequence[Sequence[float]]]) -> List[Ring]:
    """``[exterior, *holes]`` closed and wound per RFC 7946 §3.1.6 ("exterior
    rings are counterclockwise, and holes are clockwise"), whatever winding the
    vendor used."""
    return [_wound(r, ccw=(i == 0)) for i, r in enumerate(rings)]


def contour_geometry(parts: Sequence[Sequence[Sequence[Sequence[float]]]]) -> Union[Polygon, MultiPolygon]:
    """One part stays a plain Polygon, so the common single-part result keeps
    its shape; only a genuinely multi-part contour becomes a MultiPolygon."""
    polygons = [normalize_polygon(p) for p in parts]
    if len(polygons) == 1:
        return Polygon(type="Polygon", coordinates=polygons[0])
    return MultiPolygon(type="MultiPolygon", coordinates=polygons)


def _contains(ring: Sequence[Sequence[float]], x: float, y: float) -> bool:
    """Even-odd ray cast; closure does not matter."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def esri_parts(rings: Sequence[Sequence[Sequence[float]]]) -> List[List[Ring]]:
    """Group an Esri polygon's flat ``rings`` into ``[exterior, *holes]`` parts.

    Esri lists outer and hole rings in no guaranteed order and marks them only by
    winding: clockwise is an exterior, counter-clockwise a hole. A zero-area ring
    has no winding and cannot bound a hole, so it is kept as its own exterior
    rather than dropped. If no ring is clockwise the winding carries no signal,
    and every ring is read as an exterior so the area is not lost.

    Each hole goes to the smallest exterior containing its first vertex (the
    innermost one, for an island inside another part's hole), or to the largest
    exterior when none contains it."""
    areas = [signed_area(r) for r in rings]
    exteriors = [i for i, a in enumerate(areas) if a <= 0]
    if not exteriors:
        exteriors = list(range(len(rings)))
    exterior_set = set(exteriors)
    holes = [i for i in range(len(rings)) if i not in exterior_set]
    parts: dict = {i: [rings[i]] for i in exteriors}
    largest = max(exteriors, key=lambda i: abs(areas[i]))
    for h in holes:
        x, y = rings[h][0][0], rings[h][0][1]
        containing = [i for i in exteriors if _contains(rings[i], x, y)]
        owner = min(containing, key=lambda i: abs(areas[i])) if containing else largest
        parts[owner].append(rings[h])
    return [parts[i] for i in exteriors]
