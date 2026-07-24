"""Isochrone operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from .enums import IsochroneType, TravelMode
from .latlng import LatLng
from .passthrough import Passthrough


@dataclass(frozen=True)
class IsochroneOptions:
    center: LatLng
    type: IsochroneType
    #: 1..4 break values (seconds for time, meters for distance).
    values: Sequence[float]
    #: driving/walking base; Mapbox and TomTom additionally accept cycling.
    travel_mode: TravelMode = TravelMode.DRIVING
    #: ISO 8601 string (note: a string, matching the sibling isochrone surface).
    departure_time: Optional[str] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class Polygon:
    """GeoJSON Polygon (closed outer ring); coordinates are [lng, lat] rings."""

    type: str
    coordinates: List[List[List[float]]]


@dataclass(frozen=True)
class IsochroneContour:
    value: float
    geometry: Polygon


@dataclass(frozen=True)
class IsochroneMeta:
    request_count: int


@dataclass(frozen=True)
class IsochroneResult:
    #: Sorted by value ascending.
    contours: List[IsochroneContour]
    raw: Any = None
    #: Present only when more than one underlying HTTP call was made (TomTom).
    meta: Optional[IsochroneMeta] = None
