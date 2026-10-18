"""Distance-matrix operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence

from ._coerce import check_departure_type, coerce_enum
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
    #: HERE-only augmentation; overrides the TravelMode mapping when set. Matrix
    #: v8 accepts the same eight values as Routing v8 — including taxi, bus and
    #: privateBus — and has no findsequence2 leg, so none of the optimization
    #: caveats on :attr:`HereTransportMode.PRIVATE_BUS` apply here.
    transport_mode: Optional[HereTransportMode] = None
    passthrough: Optional[Passthrough] = None

    #: Whether to compute cells against traffic (see :class:`TrafficMode`). Opt-in
    #: for the same reason as on routing — and the stakes are higher here:
    #: Google's Route Matrix bills **per element**, so a traffic-aware 10x10
    #: request moves 100 billed elements onto the Pro-tier SKU, not one. HERE
    #: Matrix has no traffic surcharge, but it applies live traffic by default, so
    #: ``NONE`` makes the connector disable it explicitly. On TomTom ``NONE`` sends
    #: ``options.traffic: "historical"`` with ``departAt: "any"`` (its
    #: time-independent average) and ``TRAFFIC`` sends ``"live"`` (with
    #: ``departAt: "now"`` when no ``departure_time`` is given).
    #: Passing ``departure_time`` alone does NOT enable traffic.
    traffic_mode: TrafficMode = TrafficMode.NONE

    def __post_init__(self) -> None:
        """Normalize ``travel_mode`` / ``traffic_mode`` to their enum members (raw
        valid strings are accepted). ``transport_mode`` is forwarded verbatim to
        HERE and not checked here.

        Raises:
            ValueError: ``travel_mode`` or ``traffic_mode`` is a string outside its
                enum.
            TypeError: one of them is not a string (``None`` included), or
                ``departure_time`` is neither a ``datetime`` nor a string.
        """
        coerce_enum(self, "travel_mode", TravelMode)
        coerce_enum(self, "traffic_mode", TrafficMode)
        check_departure_type(self)


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
