"""Routing operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional, Sequence

from ._coerce import check_departure_type, coerce_enum, coerce_tokens
from .enums import (
    HereTransportMode,
    PolylineQuality,
    RoutingInclude,
    TrafficMode,
    TravelMode,
)
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

    #: Geometry fidelity of the returned polyline. See :class:`PolylineQuality` —
    #: a best-effort hint that HERE, TomTom and Esri silently ignore.
    polyline_quality: PolylineQuality = PolylineQuality.SIMPLIFIED

    #: Whether to route against traffic (see :class:`TrafficMode`: ``NONE``,
    #: ``TRAFFIC``, and the deprecated alias ``LIVE``). Opt-in because traffic is a
    #: billable upgrade on some providers — most notably Google, where it selects
    #: a Pro-tier SKU, and HERE, which bills routing as Time-Aware unless traffic
    #: is disabled — so it is NOT implied by ``departure_time``: under ``NONE`` a
    #: departure time never adds traffic.
    traffic_mode: TrafficMode = TrafficMode.NONE

    #: Optional normalized output fields to fetch. Empty means nothing extra is
    #: requested, so the response stays as small and as cheap as the provider
    #: allows.
    include: Sequence[RoutingInclude] = ()

    def __post_init__(self) -> None:
        """Normalize the closed-vocabulary fields to their enum members.

        Raw valid strings (``travel_mode="walking"``) are accepted and normalized;
        ``include`` becomes a tuple of :class:`RoutingInclude` members.
        ``transport_mode`` is not checked: the HERE connector forwards it verbatim,
        so HERE itself judges it.

        Raises:
            ValueError: ``travel_mode``, ``traffic_mode``, ``polyline_quality`` or an
                ``include`` token is a string outside its enum.
            TypeError: one of those is not a string (``None`` included), ``include``
                is a bare string or not iterable, or ``departure_time`` is neither a
                ``datetime`` nor a string.
        """
        coerce_enum(self, "travel_mode", TravelMode)
        coerce_enum(self, "traffic_mode", TrafficMode)
        coerce_enum(self, "polyline_quality", PolylineQuality)
        coerce_tokens(self, "include", RoutingInclude)
        check_departure_type(self)

    def includes(self, token: RoutingInclude) -> bool:
        """Whether the caller opted into a given optional output field."""
        return token in self.include


@dataclass(frozen=True)
class RoutingLeg:
    distance_meters: float
    duration_seconds: float

    #: Leg duration ignoring traffic. Present only when
    #: ``RoutingInclude.DURATION_WITHOUT_TRAFFIC`` was requested AND the provider
    #: returned it natively — **never synthesized**, so ``None`` is meaningful:
    #: this provider did not supply the value for this request.
    #:
    #: The point of having it alongside ``duration_seconds`` is the delta: the two
    #: together say how much of a trip's time is congestion, which makes
    #: ETA-vs-baseline comparisons possible without a second request.
    #:
    #: Native on Google (``staticDuration``), HERE (``baseDuration``) and TomTom
    #: (``noTrafficTravelTimeInSeconds``). Mapbox, OSRM and Esri do not return it.
    duration_without_traffic_seconds: Optional[float] = None


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

    #: Whole-route duration ignoring traffic. Same contract as
    #: :attr:`RoutingLeg.duration_without_traffic_seconds`: opt-in via
    #: ``RoutingInclude.DURATION_WITHOUT_TRAFFIC``, vendor-native only, never
    #: synthesized.
    total_duration_without_traffic_seconds: Optional[float] = None
