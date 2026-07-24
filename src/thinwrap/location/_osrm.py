"""OSRM connectors: routing (route / trip), matrix (table). Self-hosted;
baseUrl required; pre-flight validation; in-body status codes."""

from __future__ import annotations

import re
from typing import Any, List, Optional

from ._jsonpath import jget, jlist, jnum, jstr
from ._util import decode_json, ok_status
from .base import BaseConnector
from .config import OsrmConfig
from .coordinate import join_coords
from .enums import TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, unknown_error
from .latlng import LatLng
from .matrix import MatrixCell, MatrixOptions, MatrixResult
from .passthrough import merge_passthrough
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_PROFILE_NOT_FOUND = re.compile(r"profile\s+not\s+found", re.IGNORECASE)


def _validate_base_url(base_url: str) -> None:
    if not base_url:
        raise ConnectorError(
            ProviderCode.INVALID_REQUEST,
            message="OSRM connector requires explicit baseUrl. The public demo server is not used as a default.",
            provider_message="baseUrl is required for OSRM",
        )


def _osrm_profile(m: TravelMode) -> str:
    if m == TravelMode.WALKING:
        return "walking"
    if m == TravelMode.CYCLING:
        return "cycling"
    return "driving"


class OsrmRoutingConnector(BaseConnector):
    def __init__(self, config: OsrmConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def route(self, opts: RoutingOptions) -> RoutingResult:
        _validate_base_url(self.cfg.base_url)
        _validate_routing_compat(opts)
        wps = list(opts.waypoints)
        if len(wps) < 2:
            raise invalid_request("OSRM Routing requires at least two waypoints")

        use_trip = opts.optimize or opts.optimize_fixed_origin or opts.optimize_fixed_destination or opts.is_round_trip
        profile = _osrm_profile(opts.travel_mode)
        coords = join_coords(wps, "lnglat", ";")
        endpoint = "trip" if use_trip else "route"
        url = f"{self.cfg.base_url}/{endpoint}/v1/{profile}/{coords}"

        base_query = {"overview": "full", "geometries": "polyline", "steps": "true", "annotations": "duration,distance"}
        if use_trip:
            source = "first" if opts.optimize_fixed_origin else "any"
            destination = "last" if opts.optimize_fixed_destination else "any"
            # OSRM rejects source=any + destination=any with roundtrip=false (HTTP
            # 400 NotImplemented). A plain optimize (neither endpoint fixed, open
            # route) therefore keeps the input's first & last fixed and reorders the
            # middle — matching the Mapbox Optimization v1 sibling.
            if not opts.is_round_trip and source == "any" and destination == "any":
                source = "first"
                destination = "last"
            base_query["source"] = source
            base_query["destination"] = destination
            base_query["roundtrip"] = "true" if opts.is_round_trip else "false"

        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _osrm_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if raw is None:
            raise unknown_error(resp.status, None, "OSRM routing returned a malformed response body")
        routes = raw.get("routes") if isinstance(raw, dict) else None
        # The `/trip/v1` service returns its route objects under `trips`, not `routes`.
        if not routes and isinstance(raw, dict):
            routes = raw.get("trips")
        if jstr(raw.get("code") if isinstance(raw, dict) else "") != "Ok" or not routes:
            raise _osrm_route_in_body_error(raw, use_trip)
        route = routes[0]

        legs = [RoutingLeg(jnum(l.get("distance")), jnum(l.get("duration"))) for l in (route.get("legs") or [])]

        waypoint_order = None
        wps_out = raw.get("waypoints")
        if use_trip and isinstance(wps_out, list):
            n = len(wps_out)
            order = [0] * n
            valid = True
            for input_idx, wp in enumerate(wps_out):
                pos = jget(wp, "waypoint_index")
                if isinstance(pos, bool) or not isinstance(pos, (int, float)) or pos < 0 or pos >= n:
                    valid = False
                    break
                order[int(pos)] = input_idx
            if valid:
                waypoint_order = order

        return RoutingResult(
            legs=legs,
            total_distance_meters=jnum(route.get("distance")),
            total_duration_seconds=jnum(route.get("duration")),
            polyline=jstr(route.get("geometry")),  # OSRM polyline is already precision-5
            waypoint_order=waypoint_order,
            raw=raw,
        )


class OsrmMatrixConnector(BaseConnector):
    def __init__(self, config: OsrmConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        _validate_base_url(self.cfg.base_url)
        if opts.departure_time is not None:
            raise ConnectorError(ProviderCode.UNSUPPORTED_FIELD, message="OSRM does not support departureTime", provider_message="OSRM does not support departureTime")
        if opts.avoid_tolls:
            raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message="OSRM does not support avoidTolls", provider_message="avoidTolls is not supported by OSRM")
        if not opts.origins or not opts.destinations:
            raise invalid_request("OSRM Matrix requires at least one origin and one destination")

        profile = _osrm_profile(opts.travel_mode)
        coords = join_coords(list(opts.origins) + list(opts.destinations), "lnglat", ";")
        sources = ";".join(str(i) for i in range(len(opts.origins)))
        dests = ";".join(str(i + len(opts.origins)) for i in range(len(opts.destinations)))
        url = f"{self.cfg.base_url}/table/v1/{profile}/{coords}"
        # `annotations` is a connector default set BEFORE the merge so a consumer's
        # passthrough.query can override it (setting it after the merge silently
        # ignored the override).
        base_query = {"sources": sources, "destinations": dests, "annotations": "duration,distance"}
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _osrm_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if not isinstance(raw, dict) or raw.get("code") != "Ok":
            raise _osrm_matrix_in_body_error(raw)

        durations = raw.get("durations") or []
        distances = raw.get("distances") or []
        no, nd = len(opts.origins), len(opts.destinations)
        if not _osrm_dimensions_ok(durations, distances, no, nd):
            raise unknown_error(resp.status, raw, "OSRM matrix returned a table that does not match the requested dimensions")
        # OSRM `/table` returns null for an unroutable pair — omit the cell rather
        # than coercing to 0 (which reads as "same location"). Contract:
        # missing/failed entries are omitted from cells.
        cells = [
            MatrixCell(oi, di, jnum(distances[oi][di]), jnum(durations[oi][di]))
            for oi in range(no)
            for di in range(nd)
            if distances[oi][di] is not None and durations[oi][di] is not None
        ]
        return MatrixResult(cells=cells, raw=raw)


# ---- shared osrm helpers ----

def _validate_routing_compat(opts: RoutingOptions) -> None:
    if opts.departure_time is not None:
        raise ConnectorError(ProviderCode.UNSUPPORTED_FIELD, message="OSRM does not support departureTime", provider_message="OSRM does not support departureTime")
    if opts.avoid_tolls:
        raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message="OSRM does not support avoidTolls", provider_message="avoidTolls is not supported by OSRM")
    if opts.avoid_ferries:
        raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message="OSRM does not support avoidFerries", provider_message="avoidFerries is not supported by OSRM")
    if opts.avoid_highways:
        raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message="OSRM does not support avoidHighways", provider_message="avoidHighways is not supported by OSRM")
    # NOTE: the previous "invalid /trip combo" preflight is gone. It rejected
    # source=any/destination=any/roundtrip=false, but the query builder no longer
    # emits that combo — a plain optimize maps to source=first/destination=last
    # (open route, endpoints kept, middle reordered), which OSRM accepts.


def _osrm_map_vendor_error(status: int) -> ProviderCode:
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if status in (400, 404):
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _osrm_error_message(body: Any) -> str:
    return jstr(jget(body, "message")) or jstr(jget(body, "error"))


def _osrm_http_error(status: int, headers, data: bytes) -> ConnectorError:
    raw = decode_json(data)
    return provider_error(status, headers, raw, _osrm_map_vendor_error(status), _osrm_error_message(raw))


def _osrm_route_in_body_error(body: Any, use_trip: bool) -> ConnectorError:
    code = jstr(jget(body, "code"))
    message = jstr(jget(body, "message"))
    if code in ("NoRoute", "NoSegment"):
        pc = ProviderCode.PROFILE_NOT_CONFIGURED if (message and _PROFILE_NOT_FOUND.search(message)) else ProviderCode.INVALID_REQUEST
    elif code in ("InvalidQuery", "InvalidOptions"):
        pc = ProviderCode.INVALID_REQUEST
    elif code == "NoTrips":
        pc = ProviderCode.INVALID_REQUEST if use_trip else ProviderCode.UNKNOWN
    else:
        pc = ProviderCode.UNKNOWN
    pm = message or f"OSRM returned code: {code or 'unknown'}"
    return classified_error(pc, None, body, pm)


def _osrm_matrix_in_body_error(body: Any) -> ConnectorError:
    code = jstr(jget(body, "code"))
    message = jstr(jget(body, "message"))
    pc = ProviderCode.INVALID_REQUEST if code in ("NoTable", "InvalidQuery", "InvalidOptions") else ProviderCode.UNKNOWN
    pm = message or f"OSRM returned code: {code or 'unknown'}"
    return classified_error(pc, None, body, pm)


def _osrm_dimensions_ok(durations, distances, no, nd) -> bool:
    if len(durations) < no or len(distances) < no:
        return False
    for i in range(no):
        if len(durations[i]) < nd or len(distances[i]) < nd:
            return False
    return True
