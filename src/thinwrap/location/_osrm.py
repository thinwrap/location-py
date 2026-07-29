"""OSRM connectors: routing (route / trip), matrix (table). Self-hosted;
baseUrl required; pre-flight validation; in-body status codes."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from ._jsonpath import jget, jlist, jnum, jstr
from ._route_completeness import assert_route_has_legs
from ._util import decode_json, ok_status
from ._waypoint_order import invert_waypoint_positions
from .base import BaseConnector
from .config import OsrmConfig
from .coordinate import join_coords
from .enums import PolylineQuality, TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, unknown_error
from .latlng import LatLng
from .matrix import MatrixCell, MatrixOptions, MatrixResult
from .passthrough import merge_passthrough
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_PROFILE_NOT_FOUND = re.compile(r"profile\s+not\s+found", re.IGNORECASE)


def _validate_base_url(base_url: str) -> str:
    """Validate and normalize an OSRM base URL, returning the value to build on.

    OSRM is the only provider requiring an explicit base URL and shipping zero
    auth, so there is no default to fall back to (the public demo server is
    deliberately not one).

    Two checks: non-empty, and an ``http://`` or ``https://`` scheme. Without a
    scheme the default transport raises ``URLError("unsupported URL scheme")``,
    which ``BaseConnector`` reports as ``provider_unavailable`` behind a redacted
    message — making a bare host in config look exactly like the server being
    down. Checking here makes it an ``INVALID_REQUEST`` that names the config.

    A path prefix is explicitly ALLOWED (hosting OSRM at ``https://host/osrm``
    behind a reverse proxy is a normal deployment); trailing slashes are stripped
    so the caller's ``f"{base_url}/route/v1/..."`` cannot produce a double slash.
    """
    if not base_url:
        raise ConnectorError(
            ProviderCode.INVALID_REQUEST,
            message="OSRM connector requires explicit baseUrl. The public demo server is not used as a default.",
            provider_message="baseUrl is required for OSRM",
        )
    if not base_url.lower().startswith(("http://", "https://")):
        raise ConnectorError(
            ProviderCode.INVALID_REQUEST,
            message=f"OSRM baseUrl must start with http:// or https:// (got: {base_url})",
            provider_message="OSRM baseUrl must start with http:// or https://",
        )
    return base_url.rstrip("/")


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
        base_url = _validate_base_url(self.cfg.base_url)
        _validate_routing_compat(opts, self.cfg.supported_exclude_classes)
        wps = list(opts.waypoints)
        if len(wps) < 2:
            raise invalid_request("OSRM Routing requires at least two waypoints")

        use_trip = opts.optimize or opts.optimize_fixed_origin or opts.optimize_fixed_destination or opts.is_round_trip
        profile = _osrm_profile(opts.travel_mode)
        coords = join_coords(wps, "lnglat", ";")
        endpoint = "trip" if use_trip else "route"
        url = f"{base_url}/{endpoint}/v1/{profile}/{coords}"

        # steps and annotations are deliberately NOT sent on /route: nothing in
        # RoutingResult reads them, and leg distance/duration are present regardless
        # of annotations. (The Table service is different — it forces
        # annotations=duration,distance because that IS what populates the cells.)
        base_query = {
            "overview": "full" if opts.polyline_quality == PolylineQuality.DETAILED else "simplified",
            "geometries": "polyline",
        }

        # Only classes the operator declared AND the caller asked for; validation
        # has already rejected any undeclared request.
        excludes = [cls for _, cls, requested in _osrm_avoid_flags(opts) if requested]
        if excludes:
            base_query["exclude"] = ",".join(excludes)
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
            raise _osrm_route_in_body_error(raw, use_trip, resp.status)
        route = routes[0]

        legs = [RoutingLeg(jnum(l.get("distance")), jnum(l.get("duration"))) for l in (route.get("legs") or [])]

        assert_route_has_legs(len(legs), len(wps), 'OSRM routing', raw)

        waypoint_order = None
        wps_out = raw.get("waypoints")
        if use_trip and isinstance(wps_out, list):
            # Canonical waypoint_order = full visiting sequence of INPUT indices
            # (origin/destination inclusive). OSRM /trip returns waypoints[] in INPUT
            # order, where each waypoint_index is the position that input
            # waypoint occupies in the optimized trip — i.e. the INVERSE of the
            # canonical. Invert it, validated against the INPUT waypoint count so
            # a truncated or duplicate-index waypoints[] omits the ordering
            # instead of yielding a permutation that silently drops or repeats a
            # waypoint.
            waypoint_order = invert_waypoint_positions(
                [jget(wp, "waypoint_index") for wp in wps_out], len(opts.waypoints)
            )

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
        base_url = _validate_base_url(self.cfg.base_url)
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
        url = f"{base_url}/table/v1/{profile}/{coords}"
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

def _osrm_avoid_flags(opts: RoutingOptions) -> List[Tuple[str, str, bool]]:
    """The normalized avoid-flag -> OSRM ``exclude`` class mapping, paired with
    whether the caller requested it. Ordered, so the first unsupported flag is the
    one reported."""
    return [
        ("avoid_tolls", "toll", opts.avoid_tolls),
        ("avoid_ferries", "ferry", opts.avoid_ferries),
        ("avoid_highways", "motorway", opts.avoid_highways),
    ]


def _validate_routing_compat(opts: RoutingOptions, supported: Sequence[str] = ()) -> None:
    if opts.departure_time is not None:
        raise ConnectorError(ProviderCode.UNSUPPORTED_FIELD, message="OSRM does not support departureTime", provider_message="OSRM does not support departureTime")

    # Whether an avoid-flag works depends on the OPERATOR'S build, not on OSRM:
    # exclude=toll is rejected as InvalidValue by the public demo build and honoured
    # by a self-hosted instance with the class compiled in (verified live — it
    # genuinely rerouted). So the capability is declared in config, and anything not
    # declared is still rejected up front rather than sent and bounced with an
    # opaque vendor error.
    for flag, exclude_class, requested in _osrm_avoid_flags(opts):
        if requested and exclude_class not in supported:
            msg = (
                f"{flag} requires an OSRM build with the '{exclude_class}' exclude "
                "class compiled in; declare it via OsrmConfig.supported_exclude_classes"
            )
            raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message=msg, provider_message=msg)


def _osrm_classify_envelope_code(code: str, message: str, use_trip: bool) -> Optional[ProviderCode]:
    """Classify an OSRM envelope ``code``, or None when it is not one this
    connector recognizes (so the caller falls back to HTTP-status mapping).

    Shared by both error paths because OSRM does not distinguish them: these codes
    arrive with a **4xx** in practice (live-verified on both the public demo build
    and a self-hosted instance — NoSegment, InvalidOptions and InvalidValue all came
    back as 400), and the same code on a 200 means the same thing.

    ``NoRoute`` / ``NoSegment`` / ``NoTrips`` are ``NO_ROUTE``: the request was
    well-formed and the server answered, there simply is no connecting route (or no
    road near a coordinate to snap to). A ``NoRoute`` whose message states a missing
    profile is ``PROFILE_NOT_CONFIGURED`` instead — never inferred from a bare one.
    """
    if code in ("NoRoute", "NoSegment"):
        if message and _PROFILE_NOT_FOUND.search(message):
            return ProviderCode.PROFILE_NOT_CONFIGURED
        return ProviderCode.NO_ROUTE
    if code == "NoTrips":
        # A /trip-endpoint outcome. On a /route dispatch it should never occur, so
        # an unexpected one stays unclassified.
        return ProviderCode.NO_ROUTE if use_trip else None
    if code in ("InvalidQuery", "InvalidOptions", "InvalidValue", "TooBig"):
        return ProviderCode.INVALID_REQUEST
    return None


def _osrm_map_vendor_error(status: int, body: Any = None, use_trip: bool = False) -> ProviderCode:
    # Proxy-layer statuses win: a 401/429 from a reverse proxy has no OSRM envelope.
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE

    # OSRM serves EVERY non-Ok envelope code with a 4xx, so the envelope code — not
    # the status — distinguishes "no route exists" from "your request was wrong".
    classified = _osrm_classify_envelope_code(
        jstr(jget(body, "code")), jstr(jget(body, "message")), use_trip
    )
    if classified is not None:
        return classified

    if status in (400, 404):
        return ProviderCode.INVALID_REQUEST
    return ProviderCode.UNKNOWN


def _osrm_error_message(body: Any) -> str:
    return jstr(jget(body, "message")) or jstr(jget(body, "error"))


def _osrm_http_error(status: int, headers, data: bytes) -> ConnectorError:
    raw = decode_json(data)
    # use_trip is not threaded here: the /trip-only NoTrips code stays unclassified
    # on this shared path, which is the conservative choice for a helper also used
    # by the Table service.
    return provider_error(status, headers, raw, _osrm_map_vendor_error(status, raw), _osrm_error_message(raw))


def _osrm_route_in_body_error(body: Any, use_trip: bool, status_code: int) -> ConnectorError:
    """Map a 2xx OSRM envelope to a typed error.

    Reached when the envelope code is not ``Ok``, or when ``routes``/``trips`` came
    back empty.

    An ``Ok`` envelope with an empty ``routes[]`` is ``NO_ROUTE``, not ``UNKNOWN``:
    the envelope says the request was fine and the server answered, there is simply
    nothing to return. Reported as unknown it produced the message "OSRM returned
    code: Ok", which reads like a success and gave a consumer nothing to branch on —
    while Google's empty ``routes[]`` has always mapped to ``no_route``.

    ``status_code`` is the real HTTP status rather than ``None``: this path is only
    reachable on a 2xx, and nulling it made an answered request look like a
    transport failure.
    """
    code = jstr(jget(body, "code"))
    message = jstr(jget(body, "message"))
    if code == "Ok":
        noun = "trips" if use_trip else "routes"
        return classified_error(
            ProviderCode.NO_ROUTE, status_code, body, f"OSRM returned no {noun} with envelope code Ok"
        )
    pc = _osrm_classify_envelope_code(code, message, use_trip) or ProviderCode.UNKNOWN
    pm = message or f"OSRM returned code: {code or 'unknown'}"
    return classified_error(pc, status_code, body, pm)


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
