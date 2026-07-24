"""Provider configuration objects. The config type selects the provider."""

from __future__ import annotations

from dataclasses import dataclass

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
