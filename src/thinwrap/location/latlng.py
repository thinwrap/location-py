"""The single coordinate representation, latitude first."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float
