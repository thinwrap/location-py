"""Duration + distance grid decoding shared by the Mapbox Matrix and OSRM Table
connectors, which answer in the same ``durations[i][j]`` / ``distances[i][j]``
shape."""

from __future__ import annotations

from typing import List

from ._jsonpath import jnum_opt
from .matrix import MatrixCell


def dimensions_ok(durations, distances, no, nd) -> bool:
    """Whether both tables are lists of at least ``no`` rows of at least ``nd``
    entries — anything else cannot be indexed as the requested grid."""
    if not isinstance(durations, list) or not isinstance(distances, list):
        return False
    if len(durations) < no or len(distances) < no:
        return False
    for i in range(no):
        if not isinstance(durations[i], list) or not isinstance(distances[i], list):
            return False
        if len(durations[i]) < nd or len(distances[i]) < nd:
            return False
    return True


def grid_cells(durations, distances, no: int, nd: int) -> List[MatrixCell]:
    """Cells from a Mapbox/OSRM-shaped duration + distance grid.

    Both return ``null`` for an unroutable pair; that cell — like any entry that is
    not a number — is omitted rather than coerced to 0, which would read as "same
    location"."""
    cells = []
    for i in range(no):
        for j in range(nd):
            distance, duration = jnum_opt(distances[i][j]), jnum_opt(durations[i][j])
            if distance is not None and duration is not None:
                cells.append(MatrixCell(i, j, distance, duration))
    return cells
