"""User-facing typed vocabulary. Values are byte-identical across siblings."""

from __future__ import annotations

from enum import Enum


class ProviderID(str, Enum):
    GOOGLE = "google"
    MAPBOX = "mapbox"
    HERE = "here"
    ESRI = "esri"
    OSRM = "osrm"
    TOMTOM = "tomtom"


class TravelMode(str, Enum):
    DRIVING = "driving"
    WALKING = "walking"
    CYCLING = "cycling"


class IsochroneType(str, Enum):
    TIME = "time"
    DISTANCE = "distance"


class HereTransportMode(str, Enum):
    """HERE-narrowed transport mode. Set on RoutingOptions / MatrixOptions to
    override the base TravelMode mapping; read only by the HERE connector."""

    CAR = "car"
    TRUCK = "truck"
    PEDESTRIAN = "pedestrian"
    BICYCLE = "bicycle"
    SCOOTER = "scooter"
    TAXI = "taxi"  # routing only
    PRIVATE_BUS = "privateBus"  # routing only


class PolylineQuality(str, Enum):
    """Geometry fidelity for the returned ``polyline``.

    ``SIMPLIFIED`` is the default because the difference is large and one-sided:
    measured on a single ~140km route, Mapbox returned 203 characters simplified
    versus 6146 full (30x), OSRM 155 versus 4873 (31x), and Google 883 versus 2565
    (2.9x) — with every leg distance and duration byte-identical.

    **A best-effort hint, not a guarantee.** Google, Mapbox and OSRM honour both
    values. HERE and TomTom expose no fidelity control, and Esri offers only a
    generalization *tolerance* in map units (mapping onto some chosen tolerance
    would mean inventing a magic number), so on those three the value is silently
    ignored. That is deliberate: fidelity is cosmetic, so extra vertices cannot
    make a caller's result wrong — unlike ``avoid_tolls``, whose silent omission
    WOULD change routing semantics and therefore raises.
    """

    SIMPLIFIED = "simplified"
    DETAILED = "detailed"


class TrafficMode(str, Enum):
    """How much traffic data a route is computed against.

    ``NONE`` is the default, and that default is a **billing** decision as much as
    a correctness one: on Google, traffic-aware routing is a Pro-tier SKU feature
    while the base tier is Essentials, so a wrapper that quietly turns traffic on
    moves the consumer to a more expensive SKU. Traffic is therefore always opt-in
    — notably NOT implied by passing a ``departure_time``.
    """

    NONE = "none"
    LIVE = "live"


class RoutingInclude(str, Enum):
    """Opt-in tokens for optional normalized routing output fields.

    Nothing extra is fetched unless it is named. Each token maps **1:1 onto one
    optional field** of :class:`~thinwrap.location.routing.RoutingResult` /
    :class:`~thinwrap.location.routing.RoutingLeg` — that 1:1 rule is what keeps
    this from degenerating into a second ``Passthrough``. Vendor data with no
    normalized field gets no token; read it from ``raw``.
    """

    #: Populates ``RoutingLeg.duration_without_traffic_seconds`` and
    #: ``RoutingResult.total_duration_without_traffic_seconds``.
    #:
    #: Free on Google (a field-mask entry), HERE (already inside the requested
    #: summary) and Mapbox/OSRM (not returned at all); TomTom needs the extra
    #: ``computeTravelTimeFor=all`` request parameter.
    DURATION_WITHOUT_TRAFFIC = "durationWithoutTraffic"


class PlaceDetailsInclude(str, Enum):
    """Opt-in tokens for optional place-details output fields, mirroring
    :class:`RoutingInclude`."""

    #: Populates ``PlaceDetailsResult.name``.
    #:
    #: On Google this adds ``displayName`` to the MANDATORY field mask, which selects
    #: the **Pro** SKU tier — the reason it is opt-in rather than always requested.
    #: Free on HERE/Mapbox/TomTom; unavailable on Esri.
    NAME = "name"
