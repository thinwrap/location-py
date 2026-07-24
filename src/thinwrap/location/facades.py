"""The four operation facades. The config type selects the provider; an
operation a provider does not support raises ValueError at construction (the
dynamic-language analog of the siblings' compile-time provider unions)."""

from __future__ import annotations

from typing import Callable, Optional

from ._esri import EsriGeocodingConnector, EsriIsochroneConnector, EsriMatrixConnector, EsriRoutingConnector
from ._google import GoogleGeocodingConnector, GoogleMatrixConnector, GoogleRoutingConnector
from ._here import HereGeocodingConnector, HereIsochroneConnector, HereMatrixConnector, HereRoutingConnector
from ._mapbox import MapboxGeocodingConnector, MapboxIsochroneConnector, MapboxMatrixConnector, MapboxRoutingConnector
from ._osrm import OsrmMatrixConnector, OsrmRoutingConnector
from ._tomtom import TomTomGeocodingConnector, TomTomIsochroneConnector, TomTomMatrixConnector, TomTomRoutingConnector
from .config import EsriConfig, GoogleConfig, HereConfig, MapboxConfig, OsrmConfig, TomTomConfig
from .enums import ProviderID
from .geocoding import (
    AutocompleteOptions,
    AutocompleteResult,
    GeocodeOptions,
    GeocodeResult,
    ReverseGeocodeOptions,
    ReverseGeocodeResult,
)
from .isochrone import IsochroneOptions, IsochroneResult
from .matrix import MatrixOptions, MatrixResult
from .routing import RoutingOptions, RoutingResult
from .transport import Transport


def _unsupported(config, op: str) -> ValueError:
    return ValueError(f"{config.provider_id.value} does not support {op}")


class Routing:
    """Routing facade."""

    def __init__(self, config, *, transport: Optional[Transport] = None) -> None:
        self.provider_id: ProviderID = config.provider_id
        if isinstance(config, GoogleConfig):
            self._c = GoogleRoutingConnector(config, transport)
        elif isinstance(config, MapboxConfig):
            self._c = MapboxRoutingConnector(config, transport)
        elif isinstance(config, HereConfig):
            self._c = HereRoutingConnector(config, transport)
        elif isinstance(config, EsriConfig):
            self._c = EsriRoutingConnector(config, transport)
        elif isinstance(config, OsrmConfig):
            self._c = OsrmRoutingConnector(config, transport)
        elif isinstance(config, TomTomConfig):
            self._c = TomTomRoutingConnector(config, transport)
        else:
            raise _unsupported(config, "routing")

    def route(self, options: RoutingOptions) -> RoutingResult:
        return self._c.route(options)


class Matrix:
    """Distance-matrix facade. ``sleep`` overrides the async-matrix poll wait
    (HERE always; TomTom for large grids) — primarily a test seam."""

    def __init__(self, config, *, transport: Optional[Transport] = None, sleep: Optional[Callable[[float], None]] = None) -> None:
        self.provider_id: ProviderID = config.provider_id
        if isinstance(config, GoogleConfig):
            self._c = GoogleMatrixConnector(config, transport)
        elif isinstance(config, MapboxConfig):
            self._c = MapboxMatrixConnector(config, transport)
        elif isinstance(config, HereConfig):
            self._c = HereMatrixConnector(config, transport, sleep)
        elif isinstance(config, EsriConfig):
            self._c = EsriMatrixConnector(config, transport)
        elif isinstance(config, OsrmConfig):
            self._c = OsrmMatrixConnector(config, transport)
        elif isinstance(config, TomTomConfig):
            self._c = TomTomMatrixConnector(config, transport, sleep)
        else:
            raise _unsupported(config, "matrix")

    def matrix(self, options: MatrixOptions) -> MatrixResult:
        return self._c.matrix(options)


class Geocoding:
    """Geocoding facade (forward, reverse, autocomplete). All providers except OSRM."""

    def __init__(self, config, *, transport: Optional[Transport] = None) -> None:
        self.provider_id: ProviderID = config.provider_id
        if isinstance(config, GoogleConfig):
            self._c = GoogleGeocodingConnector(config, transport)
        elif isinstance(config, MapboxConfig):
            self._c = MapboxGeocodingConnector(config, transport)
        elif isinstance(config, HereConfig):
            self._c = HereGeocodingConnector(config, transport)
        elif isinstance(config, EsriConfig):
            self._c = EsriGeocodingConnector(config, transport)
        elif isinstance(config, TomTomConfig):
            self._c = TomTomGeocodingConnector(config, transport)
        else:
            raise _unsupported(config, "geocoding")

    def geocode(self, options: GeocodeOptions) -> GeocodeResult:
        return self._c.geocode(options)

    def reverse_geocode(self, options: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        return self._c.reverse_geocode(options)

    def autocomplete(self, options: AutocompleteOptions) -> AutocompleteResult:
        return self._c.autocomplete(options)


class Isochrone:
    """Isochrone facade. Mapbox, HERE, ESRI, TomTom."""

    def __init__(self, config, *, transport: Optional[Transport] = None) -> None:
        self.provider_id: ProviderID = config.provider_id
        if isinstance(config, MapboxConfig):
            self._c = MapboxIsochroneConnector(config, transport)
        elif isinstance(config, HereConfig):
            self._c = HereIsochroneConnector(config, transport)
        elif isinstance(config, EsriConfig):
            self._c = EsriIsochroneConnector(config, transport)
        elif isinstance(config, TomTomConfig):
            self._c = TomTomIsochroneConnector(config, transport)
        else:
            raise _unsupported(config, "isochrone")

    def isochrone(self, options: IsochroneOptions) -> IsochroneResult:
        return self._c.isochrone(options)
