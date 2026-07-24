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
