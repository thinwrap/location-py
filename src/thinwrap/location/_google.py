"""Google connectors: routing (Routes v2), matrix (RouteMatrix NDJSON),
geocoding + Places Autocomplete NEW."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from ._jsonpath import jget, jlist, jnum, jstr
from ._util import decode_json, iso_string, ok_status
from .base import BaseConnector
from .config import GoogleConfig
from .coordinate import assert_finite
from .enums import TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, provider_error, unknown_error
from .geocoding import (
    AutocompleteOptions,
    AutocompletePrediction,
    AutocompleteResult,
    GeocodeCandidate,
    GeocodeOptions,
    GeocodeResult,
    ReverseGeocodeOptions,
    ReverseGeocodeResult,
    Viewport,
)
from .latlng import LatLng
from .matrix import MatrixCell, MatrixOptions, MatrixResult
from .passthrough import merge_passthrough
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_AUTOCOMPLETE_URL = "https://places.googleapis.com/v1/places:autocomplete"
_ISO_ALPHA2 = re.compile(r"^[A-Za-z]{2}$")


class GoogleRoutingConnector(BaseConnector):
    def __init__(self, config: GoogleConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def route(self, opts: RoutingOptions) -> RoutingResult:
        if len(opts.waypoints) < 2:
            raise ConnectorError(ProviderCode.INVALID_REQUEST, message="Google Routing requires at least two waypoints", provider_message="Google Routing requires at least two waypoints")
        wps = list(opts.waypoints)
        first, last = wps[0], wps[-1]
        destination_wp = first if opts.is_round_trip else last
        inter_src = wps[1:] if opts.is_round_trip else wps[1:-1]
        intermediates = [_google_latlng(wp) for wp in inter_src]

        reorder = opts.optimize or opts.optimize_fixed_origin or opts.optimize_fixed_destination or opts.is_round_trip

        travel_mode = _google_travel_mode(opts.travel_mode)
        body: dict[str, Any] = {
            "origin": _google_latlng(first),
            "destination": _google_latlng(destination_wp),
            "travelMode": travel_mode,
            "polylineEncoding": "ENCODED_POLYLINE",
        }
        # Google rejects routingPreference for WALK/BICYCLE ("Routing preference
        # cannot be set for WALK or BICYCLE routing mode.") — only DRIVE and
        # TWO_WHEELER accept it. Overridable via passthrough.
        if travel_mode in ("DRIVE", "TWO_WHEELER"):
            body["routingPreference"] = "TRAFFIC_AWARE" if opts.departure_time else "TRAFFIC_UNAWARE"
        if intermediates:
            body["intermediates"] = intermediates
        if reorder and intermediates:
            body["optimizeWaypointOrder"] = True
        if opts.departure_time:
            body["departureTime"] = iso_string(opts.departure_time)
        if opts.avoid_tolls or opts.avoid_ferries or opts.avoid_highways:
            body["routeModifiers"] = {
                "avoidTolls": opts.avoid_tolls,
                "avoidFerries": opts.avoid_ferries,
                "avoidHighways": opts.avoid_highways,
            }

        field_mask = [
            "routes.legs.distanceMeters", "routes.legs.duration", "routes.legs.staticDuration",
            "routes.distanceMeters", "routes.duration", "routes.staticDuration",
            "routes.polyline.encodedPolyline",
        ]
        if reorder:
            field_mask.append("routes.optimizedIntermediateWaypointIndex")
        headers = {"X-Goog-Api-Key": self.cfg.api_key, "X-Goog-FieldMask": ",".join(field_mask)}

        m_body, m_headers, m_query = merge_passthrough(body, headers, opts.passthrough, None)
        resp = self.send_post_json(_ROUTES_URL, m_body, m_headers, m_query)
        raw = decode_json(resp.body)
        if not ok_status(resp.status):
            raise provider_error(resp.status, resp.headers, raw, _google_map_vendor_error(resp.status, raw), _google_error_message(raw))
        routes = raw.get("routes") if isinstance(raw, dict) else None
        if not routes:
            raise unknown_error(resp.status, raw, "Google Routing returned no routes")
        route = routes[0]

        legs = [RoutingLeg(jnum(l.get("distanceMeters")), _parse_google_duration(jstr(l.get("duration")))) for l in (route.get("legs") or [])]
        waypoint_order = None
        oiwi = route.get("optimizedIntermediateWaypointIndex")
        if isinstance(oiwi, list):
            order = [0] + [int(i) + 1 for i in oiwi]
            if not opts.is_round_trip:
                order.append(len(wps) - 1)
            waypoint_order = order

        return RoutingResult(
            legs=legs,
            total_distance_meters=jnum(route.get("distanceMeters")),
            total_duration_seconds=_parse_google_duration(jstr(route.get("duration"))),
            polyline=jstr(jget(jget(route, "polyline"), "encodedPolyline")),
            waypoint_order=waypoint_order,
            raw=raw,
        )


class GoogleMatrixConnector(BaseConnector):
    def __init__(self, config: GoogleConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        origins = [{"waypoint": _google_latlng(o)} for o in opts.origins]
        destinations = [{"waypoint": _google_latlng(d)} for d in opts.destinations]
        travel_mode = _google_travel_mode(opts.travel_mode)
        body: dict[str, Any] = {
            "origins": origins,
            "destinations": destinations,
            "travelMode": travel_mode,
        }
        # Google rejects routingPreference for WALK/BICYCLE — only DRIVE and
        # TWO_WHEELER accept it. Overridable via passthrough.
        if travel_mode in ("DRIVE", "TWO_WHEELER"):
            body["routingPreference"] = "TRAFFIC_AWARE" if opts.departure_time else "TRAFFIC_UNAWARE"
        if opts.avoid_tolls:
            body["routeModifiers"] = {"avoidTolls": True}
        if opts.departure_time:
            body["departureTime"] = iso_string(opts.departure_time)
        headers = {"X-Goog-Api-Key": self.cfg.api_key, "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,duration,status,condition"}

        m_body, m_headers, m_query = merge_passthrough(body, headers, opts.passthrough, None)
        resp = self.send_post_json(_MATRIX_URL, m_body, m_headers, m_query)
        if not ok_status(resp.status):
            raw = decode_json(resp.body)
            raise provider_error(resp.status, resp.headers, raw, _google_map_vendor_error(resp.status, raw), _google_error_message(raw))

        elements = _parse_google_ndjson(resp.body.decode("utf-8"))
        cells: List[MatrixCell] = []
        for el in elements:
            if not isinstance(el, dict) or not _google_cell_successful(el):
                continue
            cells.append(MatrixCell(int(jnum(el.get("originIndex"))), int(jnum(el.get("destinationIndex"))), jnum(el.get("distanceMeters")), _parse_google_duration(jstr(el.get("duration")))))
        return MatrixResult(cells=cells, raw=elements)


class GoogleGeocodingConnector(BaseConnector):
    def __init__(self, config: GoogleConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def geocode(self, opts: GeocodeOptions) -> GeocodeResult:
        for code in opts.country_filter or []:
            if not _ISO_ALPHA2.match(code):
                raise ConnectorError(ProviderCode.INVALID_REQUEST, message=f"Invalid countryFilter entry: {code} (expected ISO 3166-1 alpha-2)", provider_message=f"Invalid countryFilter entry: {code} (expected ISO 3166-1 alpha-2)")
        query = {"address": opts.address, "key": self.cfg.api_key}
        if opts.country_filter:
            query["components"] = "|".join(f"country:{c}" for c in opts.country_filter)
        if opts.language:
            query["language"] = opts.language

        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, query)
        resp = self.send_get(_GEOCODE_URL, m_headers, m_query)
        raw = decode_json(resp.body)
        if not ok_status(resp.status):
            raise self._geo_http_error(resp.status, resp.headers, raw)
        self._enforce_status(resp.status, raw)
        return GeocodeResult(candidates=_normalize_google_candidates(raw), raw=raw)

    def reverse_geocode(self, opts: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        from .coordinate import assert_finite, to_lat_lng_string

        assert_finite(opts.location, "Google reverseGeocode")
        query = {"latlng": to_lat_lng_string(opts.location), "key": self.cfg.api_key}
        if opts.language:
            query["language"] = opts.language
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, query)
        resp = self.send_get(_GEOCODE_URL, m_headers, m_query)
        raw = decode_json(resp.body)
        if not ok_status(resp.status):
            raise self._geo_http_error(resp.status, resp.headers, raw)
        self._enforce_status(resp.status, raw)
        return ReverseGeocodeResult(candidates=_normalize_google_candidates(raw), raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        from .coordinate import assert_finite

        body: dict[str, Any] = {"input": opts.input}
        if opts.language:
            body["languageCode"] = opts.language
        if opts.location is not None:
            assert_finite(opts.location, "Google autocomplete")
            circle: dict[str, Any] = {"center": {"latitude": opts.location.lat, "longitude": opts.location.lng}}
            if opts.radius is not None:
                circle["radius"] = opts.radius
            body["locationBias"] = {"circle": circle}
        headers = {"X-Goog-Api-Key": self.cfg.api_key}
        m_body, m_headers, m_query = merge_passthrough(body, headers, opts.passthrough, None)
        resp = self.send_post_json(_PLACES_AUTOCOMPLETE_URL, m_body, m_headers, m_query)
        raw = decode_json(resp.body)
        if not ok_status(resp.status):
            err_obj = jget(raw, "error")
            code = _google_geo_map_vendor_error(resp.status, jstr(jget(err_obj, "status")))
            raise provider_error(resp.status, resp.headers, raw, code, jstr(jget(err_obj, "message")))
        preds: List[AutocompletePrediction] = []
        for s in jlist(jget(raw, "suggestions")) or []:
            pred = jget(s, "placePrediction")
            if pred is None:
                continue
            preds.append(AutocompletePrediction(description=jstr(jget(jget(pred, "text"), "text")), place_id=jstr(jget(pred, "placeId")) or None))
        return AutocompleteResult(predictions=preds, raw=raw)

    def _geo_http_error(self, status, headers, raw) -> ConnectorError:
        code = _google_geo_map_vendor_error(status, jstr(jget(raw, "status")))
        return provider_error(status, headers, raw, code, jstr(jget(raw, "error_message")))

    def _enforce_status(self, status, raw) -> None:
        s = jstr(jget(raw, "status"))
        if s in ("OK", "ZERO_RESULTS"):
            return
        pm = jstr(jget(raw, "error_message")) or s
        raise classified_error(_google_geo_map_vendor_error(status, s), status, raw, pm)


# ---- shared google helpers ----

def _google_latlng(c: LatLng) -> dict:
    assert_finite(c, "Google routing/matrix")
    return {"location": {"latLng": {"latitude": c.lat, "longitude": c.lng}}}


def _google_travel_mode(m: TravelMode) -> str:
    if m == TravelMode.WALKING:
        return "WALK"
    if m == TravelMode.CYCLING:
        return "BICYCLE"
    return "DRIVE"


def _parse_google_duration(s: str) -> float:
    try:
        return float(s[:-1]) if s.endswith("s") else float(s)
    except (ValueError, TypeError):
        return 0.0


def _google_error_status(body: Any) -> str:
    return jstr(jget(jget(body, "error"), "status"))


def _google_error_message(body: Any) -> str:
    return jstr(jget(jget(body, "error"), "message"))


def _google_error_reason(body: Any) -> str:
    """Read the machine-readable reason from a google.rpc.ErrorInfo entry in
    error.details[] (domain 'googleapis.com'). Empty when absent."""
    details = jlist(jget(jget(body, "error"), "details"))
    if not details:
        return ""
    for d in details:
        if jstr(jget(d, "domain")) == "googleapis.com" or jstr(jget(d, "@type")).endswith("google.rpc.ErrorInfo"):
            r = jstr(jget(d, "reason"))
            if r:
                return r
    return ""


_GOOGLE_AUTH_REASONS = {
    "API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED",
    "API_KEY_IP_ADDRESS_BLOCKED", "API_KEY_ANDROID_APP_BLOCKED", "API_KEY_IOS_APP_BLOCKED",
    "CREDENTIALS_MISSING", "ACCESS_TOKEN_EXPIRED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    "ACCESS_TOKEN_TYPE_UNSUPPORTED", "ACCOUNT_STATE_INVALID", "CONSUMER_INVALID",
    "CONSUMER_SUSPENDED", "USER_PROJECT_DENIED", "SERVICE_DISABLED", "BILLING_DISABLED",
}
_GOOGLE_RATE_REASONS = {"RATE_LIMIT_EXCEEDED", "RESOURCE_QUOTA_EXCEEDED"}


def _google_reason_code(reason: str):
    """Map a google.rpc.ErrorInfo reason to a ProviderCode, or None to fall back
    to the HTTP-status mapping. Lets a bad/blocked key surface as auth_failed
    even though Google reports it as HTTP 400 INVALID_ARGUMENT."""
    if reason in _GOOGLE_AUTH_REASONS:
        return ProviderCode.AUTH_FAILED
    if reason in _GOOGLE_RATE_REASONS:
        return ProviderCode.RATE_LIMITED
    return None


def _google_map_vendor_error(status: int, body: Any) -> ProviderCode:
    code = _google_reason_code(_google_error_reason(body))
    if code is not None:
        return code
    if status == 401:
        return ProviderCode.AUTH_FAILED
    if status == 403:
        return ProviderCode.RATE_LIMITED if _google_error_status(body) == "QUOTA_EXCEEDED" else ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if status == 400:
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _google_geo_map_vendor_error(status: int, google_status: str) -> ProviderCode:
    if google_status == "REQUEST_DENIED":
        return ProviderCode.AUTH_FAILED
    if google_status == "OVER_QUERY_LIMIT":
        return ProviderCode.RATE_LIMITED
    if google_status == "INVALID_REQUEST":
        return ProviderCode.INVALID_REQUEST
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if status == 400:
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _normalize_google_candidates(raw: Any) -> List[GeocodeCandidate]:
    out: List[GeocodeCandidate] = []
    for r in (jlist(jget(raw, "results")) or []):
        geom = jget(r, "geometry")
        loc = jget(geom, "location")
        cand = GeocodeCandidate(
            formatted_address=jstr(jget(r, "formatted_address")),
            location=LatLng(jnum(jget(loc, "lat")), jnum(jget(loc, "lng"))),
            place_id=jstr(jget(r, "place_id")) or None,
        )
        vp = jget(geom, "viewport")
        if isinstance(vp, dict):
            sw, ne = jget(vp, "southwest"), jget(vp, "northeast")
            cand = GeocodeCandidate(
                formatted_address=cand.formatted_address,
                location=cand.location,
                place_id=cand.place_id,
                viewport=Viewport(LatLng(jnum(jget(sw, "lat")), jnum(jget(sw, "lng"))), LatLng(jnum(jget(ne, "lat")), jnum(jget(ne, "lng")))),
            )
        out.append(cand)
    return out


def _parse_google_ndjson(text: str) -> List[Any]:
    trimmed = text.strip()
    if trimmed == "":
        return []
    if trimmed.startswith("["):
        try:
            arr = json.loads(trimmed)
            if isinstance(arr, list):
                return arr
        except ValueError:
            pass
    out: List[Any] = []
    for raw_line in trimmed.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            raise unknown_error(None, exc, "Google Matrix returned an unparseable NDJSON line") from exc
    return out


def _google_cell_successful(m: dict) -> bool:
    status = m.get("status")
    if isinstance(status, dict):
        code = status.get("code")
        if code is not None and jnum(code) != 0:
            return False
    # `condition` is independent of `status`: an element can be status-OK with
    # "ROUTE_NOT_FOUND" and no distanceMeters/duration. Treat anything other
    # than ROUTE_EXISTS (when present) as a failed cell.
    condition = m.get("condition")
    if condition is not None and condition != "ROUTE_EXISTS":
        return False
    return True
