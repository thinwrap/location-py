"""Distance-matrix operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence

from .enums import HereTransportMode, TravelMode
from .latlng import LatLng
from .passthrough import Passthrough


@dataclass(frozen=True)
class MatrixOptions:
    origins: Sequence[LatLng]
    destinations: Sequence[LatLng]
    travel_mode: TravelMode = TravelMode.DRIVING
    avoid_tolls: bool = False
    departure_time: Optional[datetime] = None
    transport_mode: Optional[HereTransportMode] = None  # HERE only
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class MatrixCell:
    origin_index: int
    destination_index: int
    distance_meters: float
    duration_seconds: float


@dataclass(frozen=True)
class MatrixResult:
    #: Flat list of cells; re-pivot via origin_index / destination_index.
    cells: List[MatrixCell]
    raw: Any = None
