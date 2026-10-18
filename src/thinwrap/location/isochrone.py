"""Isochrone operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Union

from ._coerce import check_departure_type, coerce_enum
from .enums import IsochroneType, TravelMode
from .latlng import LatLng
from .passthrough import Passthrough


@dataclass(frozen=True)
class IsochroneOptions:
    center: LatLng
    type: IsochroneType
    #: 1..4 break values (seconds for time, meters for distance).
    values: Sequence[float]
    #: Mapbox accepts driving/walking/cycling; HERE and ESRI accept driving/walking;
    #: TomTom accepts driving only (calculateReachableRange forbids pedestrian and
    #: bicycle). An unsupported mode raises ``unsupported_travel_mode``.
    travel_mode: TravelMode = TravelMode.DRIVING
    #: ISO 8601 string (note: a string, matching the sibling isochrone surface).
    departure_time: Optional[str] = None
    passthrough: Optional[Passthrough] = None

    def __post_init__(self) -> None:
        """Normalize ``type`` / ``travel_mode`` to their enum members (raw valid
        strings are accepted).

        Raises:
            ValueError: ``type`` or ``travel_mode`` is a string outside its enum.
            TypeError: one of them is not a string (``None`` included), or
                ``departure_time`` is neither a string nor a ``datetime``.
        """
        coerce_enum(self, "type", IsochroneType)
        coerce_enum(self, "travel_mode", TravelMode)
        check_departure_type(self)


@dataclass(frozen=True)
class Polygon:
    """GeoJSON Polygon: ``[exterior, *holes]`` rings of [lng, lat] positions,
    each ring closed, the exterior counter-clockwise and holes clockwise
    (RFC 7946 §3.1.6)."""

    type: str
    coordinates: List[List[List[float]]]


@dataclass(frozen=True)
class MultiPolygon:
    """GeoJSON MultiPolygon: a list of Polygon coordinate arrays, same ring rules.
    Used only when a contour genuinely has several disconnected parts (islands,
    ferry-joined areas)."""

    type: str
    coordinates: List[List[List[List[float]]]]


@dataclass(frozen=True)
class IsochroneContour:
    value: float
    #: A ``Polygon`` for a single-part contour; a ``MultiPolygon`` only when the
    #: provider returned several disconnected parts. Check ``geometry.type``
    #: before reading ``coordinates`` — their nesting depth differs.
    geometry: Union[Polygon, MultiPolygon]


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
