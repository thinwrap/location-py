"""Distance-matrix operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence

from .enums import HereTransportMode, TrafficMode, TravelMode
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

    #: Whether to compute cells against live traffic. Opt-in for the same reason as
    #: on routing — and the stakes are higher here: Google's Route Matrix bills
    #: **per element**, so a traffic-aware 10x10 request moves 100 billed elements
    #: onto the Pro-tier SKU, not one. Passing ``departure_time`` alone does NOT
    #: enable traffic.
    traffic_mode: TrafficMode = TrafficMode.NONE


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
