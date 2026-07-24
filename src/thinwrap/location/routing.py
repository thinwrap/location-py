"""Routing operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional, Sequence

from .enums import HereTransportMode, TravelMode
from .latlng import LatLng
from .passthrough import Passthrough


@dataclass(frozen=True)
class RoutingOptions:
    waypoints: Sequence[LatLng]
    optimize: bool = False
    optimize_fixed_origin: bool = False
    optimize_fixed_destination: bool = False
    is_round_trip: bool = False
    departure_time: Optional[datetime] = None
    avoid_tolls: bool = False
    avoid_ferries: bool = False
    avoid_highways: bool = False
    travel_mode: TravelMode = TravelMode.DRIVING
    #: HERE-only augmentation; overrides the TravelMode mapping when set.
    transport_mode: Optional[HereTransportMode] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class RoutingLeg:
    distance_meters: float
    duration_seconds: float


@dataclass(frozen=True)
class RoutingResult:
    legs: List[RoutingLeg]
    total_distance_meters: float
    total_duration_seconds: float
    polyline: str
    #: Input waypoint indices in visiting order (0-based, incl. origin/dest);
    #: None when no optimization was requested or the vendor gave no ordering.
    waypoint_order: Optional[List[int]] = None
    raw: Any = None
