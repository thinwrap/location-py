"""thinwrap.location — unified, SDK-free, zero-dependency Python wrapper for
routing, distance matrix, geocoding, and isochrone across Google, Mapbox, HERE,
ESRI, TomTom, and OSRM.

The config type selects the provider; switching vendor is a one-line change::

    from thinwrap.location import Routing, RoutingOptions, GoogleConfig, LatLng

    r = Routing(GoogleConfig(api_key="..."))
    res = r.route(RoutingOptions(waypoints=[LatLng(40.71, -74.0), LatLng(41.42, -73.0)]))
"""

from __future__ import annotations

from .config import EsriConfig, GoogleConfig, HereConfig, MapboxConfig, OsrmConfig, TomTomConfig
from .enums import (
    HereTransportMode,
    IsochroneType,
    PlaceDetailsInclude,
    PolylineQuality,
    ProviderID,
    RoutingInclude,
    TrafficMode,
    TravelMode,
)
from .errors import ConnectorError, ProviderCode
from .facades import Geocoding, Isochrone, Matrix, Routing
from .geocoding import (
    AutocompleteOptions,
    AutocompletePrediction,
    AutocompleteResult,
    AutocompleteStructuredFormat,
    GeocodeCandidate,
    GeocodeOptions,
    GeocodeResult,
    PlaceDetailsOptions,
    PlaceDetailsResult,
    ReverseGeocodeOptions,
    ReverseGeocodeResult,
    Viewport,
)
from .isochrone import IsochroneContour, IsochroneMeta, IsochroneOptions, IsochroneResult, Polygon
from .latlng import LatLng
from .matrix import MatrixCell, MatrixOptions, MatrixResult
from .passthrough import Passthrough
from .polyline import decode_flex_polyline, decode_polyline, encode_esri_paths, encode_polyline
from .routing import RoutingLeg, RoutingOptions, RoutingResult
from .transport import HttpRequest, HttpResponse, Transport, UrllibTransport

__all__ = [
    "PlaceDetailsOptions", "PlaceDetailsResult", "AutocompleteStructuredFormat",
    # facades
    "Routing", "Matrix", "Geocoding", "Isochrone",
    # configs
    "GoogleConfig", "MapboxConfig", "HereConfig", "EsriConfig", "OsrmConfig", "TomTomConfig",
    # enums
    "ProviderID", "TravelMode", "IsochroneType", "HereTransportMode",
    "PolylineQuality", "TrafficMode", "RoutingInclude", "PlaceDetailsInclude",
    # errors
    "ConnectorError", "ProviderCode",
    # core types
    "LatLng", "Passthrough",
    # routing
    "RoutingOptions", "RoutingLeg", "RoutingResult",
    # matrix
    "MatrixOptions", "MatrixCell", "MatrixResult",
    # geocoding
    "GeocodeOptions", "ReverseGeocodeOptions", "AutocompleteOptions",
    "GeocodeCandidate", "GeocodeResult", "ReverseGeocodeResult",
    "AutocompletePrediction", "AutocompleteResult", "Viewport",
    # isochrone
    "IsochroneOptions", "IsochroneContour", "IsochroneResult", "IsochroneMeta", "Polygon",
    # polyline
    "encode_polyline", "decode_polyline", "decode_flex_polyline", "encode_esri_paths",
    # transport
    "Transport", "UrllibTransport", "HttpRequest", "HttpResponse",
]
