"""Geocoding (forward, reverse, autocomplete) operation DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from .latlng import LatLng
from .passthrough import Passthrough


@dataclass(frozen=True)
class GeocodeOptions:
    address: str
    language: Optional[str] = None
    #: Hard filter of ISO 3166-1 alpha-2 codes.
    country_filter: Optional[Sequence[str]] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class ReverseGeocodeOptions:
    location: LatLng
    language: Optional[str] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class AutocompleteOptions:
    input: str
    location: Optional[LatLng] = None
    radius: Optional[float] = None
    language: Optional[str] = None
    passthrough: Optional[Passthrough] = None


@dataclass(frozen=True)
class Viewport:
    southwest: LatLng
    northeast: LatLng


@dataclass(frozen=True)
class GeocodeCandidate:
    formatted_address: str
    location: LatLng
    place_id: Optional[str] = None
    viewport: Optional[Viewport] = None


@dataclass(frozen=True)
class GeocodeResult:
    candidates: List[GeocodeCandidate]
    raw: Any = None


@dataclass(frozen=True)
class ReverseGeocodeResult:
    candidates: List[GeocodeCandidate]
    raw: Any = None


@dataclass(frozen=True)
class AutocompletePrediction:
    description: str
    place_id: Optional[str] = None


@dataclass(frozen=True)
class AutocompleteResult:
    predictions: List[AutocompletePrediction]
    raw: Any = None
