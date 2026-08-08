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
    override the base TravelMode mapping; read only by the HERE connector.

    Routing v8 and Matrix v8 publish the same eight values, so both options
    classes take the whole set. HERE lists ``BICYCLE``, ``BUS`` and
    ``PRIVATE_BUS`` as Beta with limited functionality.

    HERE's routing enum has one further value, ``networkRestrictedTruck``, which
    is deliberately absent: ``findsequence2`` and Matrix v8 both reject it, and
    ``/v8/routes`` returns 400 ``Missing 'networkRestrictedTruck[permittedNetworks]'
    parameter`` unless that companion parameter is supplied, which this connector
    does not model. Reach it through ``passthrough.query`` if you need it.
    """

    CAR = "car"
    TRUCK = "truck"
    PEDESTRIAN = "pedestrian"
    BICYCLE = "bicycle"
    SCOOTER = "scooter"
    TAXI = "taxi"

    #: A public-service bus: may drive through bus-restricted and bus-exclusive
    #: streets.
    BUS = "bus"

    #: A bus without those permissions: bus-exclusive streets are used only
    #: where a waypoint sits on one, the pick-up / drop-off case. NOT a synonym
    #: for ``BUS``.
    #:
    #: Incompatible with ``optimize=True``. Optimization runs through the legacy
    #: ``findsequence2`` endpoint, whose ``mode`` grammar accepts only car,
    #: truck, pedestrian, bus, bicycle, scooter and taxi; ``privateBus`` comes
    #: back as HTTP 400 ``Unknown transport mode 'privateBus'``. Use ``BUS``
    #: when you need optimization.
    PRIVATE_BUS = "privateBus"


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
