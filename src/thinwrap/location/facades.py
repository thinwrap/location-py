"""The four operation facades. The config type selects the provider.

Construction-time misuse is a bug in the calling code, so it raises the native
exception rather than a ``ConnectorError``: ``TypeError`` for something that is
not a provider config (``None`` included) or a transport without ``send()``, and
``ValueError`` for a provider that does not support the operation (the
dynamic-language analog of the siblings' compile-time provider unions).
``ConnectorError`` is reserved for operation failures — see ``errors.py``."""

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
    PlaceDetailsOptions,
    PlaceDetailsResult,
    ReverseGeocodeOptions,
    ReverseGeocodeResult,
)
from .isochrone import IsochroneOptions, IsochroneResult
from .matrix import MatrixOptions, MatrixResult
from .routing import RoutingOptions, RoutingResult
from .transport import Transport


_CONFIG_TYPES = (GoogleConfig, MapboxConfig, HereConfig, EsriConfig, OsrmConfig, TomTomConfig)


def _require_config(config, facade: str) -> ProviderID:
    """The config's provider id, or a ``TypeError`` naming what was expected.

    Checked before the provider dispatch so ``None`` or a stray dict reads as the
    type error it is, rather than an ``AttributeError`` on ``provider_id``."""
    if not isinstance(config, _CONFIG_TYPES):
        expected = ", ".join(t.__name__ for t in _CONFIG_TYPES)
        raise TypeError(f"{facade} requires a provider config ({expected}), got {type(config).__name__}")
    return config.provider_id


def _unsupported(config, op: str) -> ValueError:
    return ValueError(f"{config.provider_id.value} does not support {op}")


class Routing:
    """Routing facade. All six providers."""

    def __init__(self, config, *, transport: Optional[Transport] = None) -> None:
        """Raises:
            TypeError: ``config`` is not a provider config, or ``transport`` has no
                callable ``send()``.
        """
        self.provider_id: ProviderID = _require_config(config, "Routing")
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
        """Compute a route through ``options.waypoints``.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.route(options)


class Matrix:
    """Distance-matrix facade. ``sleep`` overrides the async-matrix poll wait
    (HERE always; TomTom for large grids) — primarily a test seam. All six
    providers."""

    def __init__(self, config, *, transport: Optional[Transport] = None, sleep: Optional[Callable[[float], None]] = None) -> None:
        """Raises:
            TypeError: ``config`` is not a provider config, ``transport`` has no
                callable ``send()``, or ``sleep`` is not callable.
        """
        self.provider_id: ProviderID = _require_config(config, "Matrix")
        if sleep is not None and not callable(sleep):
            # Otherwise it would only fail inside the poll loop, after the job
            # has already been submitted (and billed).
            raise TypeError(f"sleep must be callable, got {type(sleep).__name__}")
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
        """Compute the origins x destinations distance/duration matrix.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.matrix(options)


class Geocoding:
    """Geocoding facade (forward, reverse, autocomplete). All providers except OSRM."""

    def __init__(self, config, *, transport: Optional[Transport] = None) -> None:
        """Raises:
            TypeError: ``config`` is not a provider config, or ``transport`` has no
                callable ``send()``.
            ValueError: the provider (OSRM) has no geocoding.
        """
        self.provider_id: ProviderID = _require_config(config, "Geocoding")
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
        """Forward-geocode ``options.address``.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.geocode(options)

    def place_details(self, options: PlaceDetailsOptions) -> PlaceDetailsResult:
        """Resolve a ``place_id`` from ``autocomplete()`` into a full candidate.

        All five geocoding providers implement it. There is no capability check
        because every connector this facade can build is one of the five above —
        unlike the TS sibling, whose facade can have a custom connector swapped in.

        For Mapbox, set ``options.session_token`` to the same value used for the
        preceding ``autocomplete()`` call: Search Box bills per session, so a matching
        token makes suggest+retrieve one billable session instead of two.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.place_details(options)

    def reverse_geocode(self, options: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        """Reverse-geocode ``options.location``.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.reverse_geocode(options)

    def autocomplete(self, options: AutocompleteOptions) -> AutocompleteResult:
        """Suggest places for the partial ``options.input``.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.autocomplete(options)


class Isochrone:
    """Isochrone facade. Mapbox, HERE, ESRI, TomTom."""

    def __init__(self, config, *, transport: Optional[Transport] = None) -> None:
        """Raises:
            TypeError: ``config`` is not a provider config, or ``transport`` has no
                callable ``send()``.
            ValueError: the provider (Google, OSRM) has no isochrone.
        """
        self.provider_id: ProviderID = _require_config(config, "Isochrone")
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
        """Compute reachable-area contours around ``options.center``.

        Raises:
            ConnectorError: any operation failure — a vendor error, a transport
                failure or timeout, request data rejected before sending, or an
                ``unsupported_*`` capability rejection.
        """
        return self._c.isochrone(options)
