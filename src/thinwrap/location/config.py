"""Provider configuration objects. The config type selects the provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .enums import ProviderID


@dataclass(frozen=True)
class GoogleConfig:
    """Google provider (routing, matrix, geocoding)."""

    api_key: str

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.GOOGLE


@dataclass(frozen=True)
class MapboxConfig:
    """Mapbox provider (all four operations)."""

    access_token: str

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.MAPBOX


@dataclass(frozen=True)
class HereConfig:
    """HERE provider (all four operations)."""

    api_key: str

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.HERE


@dataclass(frozen=True)
class EsriConfig:
    """ESRI/ArcGIS provider (all four operations). Provide exactly one of
    api_key or arcgis_token."""

    api_key: str = ""
    arcgis_token: str = ""

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.ESRI


@dataclass(frozen=True)
class OsrmConfig:
    """Self-hosted OSRM (routing, matrix only). base_url is required."""

    base_url: str

    #: Exclude classes THIS OSRM build accepts — ``"toll"``, ``"ferry"``,
    #: ``"motorway"``.
    #:
    #: Whether a class is accepted is a property of the OPERATOR'S SERVER, not of
    #: OSRM, which is why it has to be declared rather than assumed. Verified live
    #: on two builds that answer the same request differently: ``exclude=toll`` is
    #: rejected with ``InvalidValue`` by the public demo build, and honoured by a
    #: self-hosted instance where it genuinely changed the route (138075 m / 5890 s
    #: via the toll road -> 130421 m / 6513 s without it).
    #:
    #: Stock OSRM compiles no exclude classes, so by default the connector rejects
    #: ``avoid_tolls`` / ``avoid_ferries`` / ``avoid_highways`` up front with
    #: ``unsupported_option`` — better than sending a request the server will bounce
    #: with an opaque ``InvalidValue``. List the classes your profile was built with
    #: and the matching avoid-flags start working.
    #:
    #: Declared rather than probed because there is no way to ask an OSRM server
    #: what it supports without issuing a request that fails, and the wrapper holds
    #: no state to cache such a probe in.
    supported_exclude_classes: Sequence[str] = ()

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.OSRM


@dataclass(frozen=True)
class TomTomConfig:
    """TomTom provider (all four operations)."""

    api_key: str

    @property
    def provider_id(self) -> ProviderID:
        return ProviderID.TOMTOM
