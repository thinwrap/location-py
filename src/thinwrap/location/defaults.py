"""Connector default lists for list-valued vendor parameters.

``Passthrough`` REPLACES a list-valued parameter wholesale (a field mask header,
HERE's ``return`` query, an ``annotations`` list) rather than appending to it — an append-only merge would
leave no way to remove a default. These tuples are what the connectors send, so a
caller who wants the default plus one field can build it without copying strings::

    ",".join(GOOGLE_ROUTES_FIELD_MASK + ("routes.travelAdvisory",))

The connectors build their requests from these same tuples, so the published list
cannot drift from what goes on the wire. Tuples, so a caller cannot mutate them.
"""

from __future__ import annotations

from typing import Tuple

# Google Compute Routes ``X-Goog-FieldMask``. Each field feeds a normalized output:
# legs.distanceMeters/legs.duration -> ``legs``, distanceMeters/duration -> the
# totals, polyline.encodedPolyline -> ``polyline``.
GOOGLE_ROUTES_FIELD_MASK: Tuple[str, ...] = (
    "routes.legs.distanceMeters",
    "routes.legs.duration",
    "routes.distanceMeters",
    "routes.duration",
    "routes.polyline.encodedPolyline",
)

# Appended only when ``RoutingInclude.DURATION_WITHOUT_TRAFFIC`` is requested;
# feeds the ``*duration_without_traffic_seconds`` outputs.
GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC: Tuple[str, ...] = (
    "routes.legs.staticDuration",
    "routes.staticDuration",
)

# Appended only when the request reorders waypoints (any optimize flag or
# ``is_round_trip``); feeds ``waypoint_order``.
GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER: Tuple[str, ...] = (
    "routes.optimizedIntermediateWaypointIndex",
)

# Google Compute Route Matrix ``X-Goog-FieldMask``. status/condition decide whether
# a cell is reachable; without them an unroutable pair surfaces as a zero cell.
GOOGLE_MATRIX_FIELD_MASK: Tuple[str, ...] = (
    "originIndex",
    "destinationIndex",
    "distanceMeters",
    "duration",
    "status",
    "condition",
)

# Google Place Details ``X-Goog-FieldMask``. These are Essentials-tier fields; the
# mask selects the SKU, so anything added here changes what the call costs.
GOOGLE_PLACE_DETAILS_FIELD_MASK: Tuple[str, ...] = (
    "id",
    "formattedAddress",
    "location",
    "viewport",
)

# Appended only when ``PlaceDetailsInclude.NAME`` is requested: ``displayName`` is
# a Pro-tier field, so it stays opt-in.
GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME: Tuple[str, ...] = ("displayName",)

# HERE Routing v8 ``return``. summary feeds ``legs`` and the totals, polyline feeds
# ``polyline``.
HERE_ROUTING_RETURN: Tuple[str, ...] = ("polyline", "summary")

# HERE Matrix v8 body ``matrixAttributes``. travelTimes and distances fill the
# cells; the decoder needs both, so dropping one raises ``unknown_error``.
HERE_MATRIX_ATTRIBUTES: Tuple[str, ...] = ("travelTimes", "distances")

# Mapbox Matrix ``annotations``. Mapbox documents only these two values; the
# decoder needs both matrices, so dropping one raises ``unknown_error``.
MAPBOX_MATRIX_ANNOTATIONS: Tuple[str, ...] = ("duration", "distance")

# OSRM Table ``annotations``. Same two values and the same dependency as Mapbox;
# OSRM's own default is ``duration`` alone, which leaves no distances to read.
OSRM_TABLE_ANNOTATIONS: Tuple[str, ...] = ("duration", "distance")

# Esri route ``accumulateAttributeNames``, one list per travel mode. The time
# attribute must match the travel mode's impedance (driving TravelTime, walking
# WalkTime) or Esri silently returns no cumulative time. The legs are read from
# ``Cumul_<time>`` and ``Cumul_Kilometers`` by name, so extra attributes a caller
# appends never change which fields feed them.
ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING: Tuple[str, ...] = ("TravelTime", "Kilometers")
ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING: Tuple[str, ...] = ("WalkTime", "Kilometers")

# Esri OD cost matrix ``accumulateAttributeNames``. Kilometers is the distance
# column; without it the decoder raises ``unknown_error`` rather than report 0 m.
ESRI_MATRIX_ACCUMULATE_ATTRIBUTES: Tuple[str, ...] = ("Kilometers",)

__all__ = [
    "GOOGLE_ROUTES_FIELD_MASK",
    "GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC",
    "GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER",
    "GOOGLE_MATRIX_FIELD_MASK",
    "GOOGLE_PLACE_DETAILS_FIELD_MASK",
    "GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME",
    "HERE_ROUTING_RETURN",
    "HERE_MATRIX_ATTRIBUTES",
    "MAPBOX_MATRIX_ANNOTATIONS",
    "OSRM_TABLE_ANNOTATIONS",
    "ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING",
    "ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING",
    "ESRI_MATRIX_ACCUMULATE_ATTRIBUTES",
]
