"""OSRM connectors: routing (route / trip), matrix (table). Self-hosted;
baseUrl required; pre-flight validation; in-body status codes."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from ._jsonpath import jget, jlist, jnum_opt, jstr
from ._matrix_grid import dimensions_ok, grid_cells
from ._route_completeness import assert_route_has_legs
from ._util import decode_json, ok_status, redact_url_userinfo
from ._waypoint_order import invert_waypoint_positions
from .base import BaseConnector
from .config import OsrmConfig
from .coordinate import join_coords
from .defaults import OSRM_TABLE_ANNOTATIONS
from .enums import PolylineQuality, TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, unknown_error
from .latlng import LatLng
from .matrix import MatrixOptions, MatrixResult
from .passthrough import merge_passthrough
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_PROFILE_NOT_FOUND = re.compile(r"profile\s+not\s+found", re.IGNORECASE)


def _validate_base_url(base_url: str) -> str:
    """Validate and normalize an OSRM base URL, returning the value to build on.

    OSRM is the only provider requiring an explicit base URL and shipping zero
    auth, so there is no default to fall back to (the public demo server is
    deliberately not one).

    Two checks: non-empty, and an ``http://`` or ``https://`` scheme, so a bare
    host in config fails as an ``INVALID_REQUEST`` that names the config. ``http://``
    passes here because an injected Transport may serve it; the default transport
    is HTTPS-only and refuses it with its own ``INVALID_REQUEST``.

    The rejected value is echoed with its userinfo masked: a base URL for a server
    behind basic auth carries the credentials inline.

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
            message=f"OSRM baseUrl must start with http:// or https:// (got: {redact_url_userinfo(base_url)})",
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
        # of annotations. (The Table service is different — it defaults
        # annotations to duration,distance because that IS what populates the cells.)
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
            # With roundtrip=false OSRM accepts first/last, first/any and any/last
            # (trip.cpp IsSupportedParameterCombination: roundtrip || fixed_start ||
            # fixed_end, since v5.27) and rejects only any/any with HTTP 400
            # NotImplemented. A plain optimize (neither endpoint fixed, open route)
            # therefore keeps the input's first & last fixed and reorders the middle.
            if not opts.is_round_trip and source == "any" and destination == "any":
                source = "first"
                destination = "last"
            base_query["source"] = source
            base_query["destination"] = destination
            base_query["roundtrip"] = "true" if opts.is_round_trip else "false"

        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _osrm_http_error(resp.status, resp.headers, resp.body, use_trip)
        raw = decode_json(resp.body)
        if raw is None:
            raise unknown_error(resp.status, None, "OSRM routing returned a malformed response body")
        routes = raw.get("routes") if isinstance(raw, dict) else None
        # The `/trip/v1` service returns its route objects under `trips`, not `routes`.
        if not routes and isinstance(raw, dict):
            routes = raw.get("trips")
        if jstr(raw.get("code") if isinstance(raw, dict) else "") != "Ok" or not routes:
            raise _osrm_route_in_body_error(raw, use_trip, resp.status)
        route = routes[0] if isinstance(routes, list) else None
        if not isinstance(route, dict):
            raise unknown_error(resp.status, raw, "OSRM returned a malformed route")
        legs = []
        for leg in jlist(route.get("legs")) or []:
            distance, duration = _osrm_distance_duration(leg, resp.status, raw)
            legs.append(RoutingLeg(distance, duration))
        total_distance, total_duration = _osrm_distance_duration(route, resp.status, raw)

        assert_route_has_legs(len(legs), len(wps), "OSRM routing", raw, resp.status)

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
            total_distance_meters=total_distance,
            total_duration_seconds=total_duration,
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
        # traffic_mode and departure_time are ignored, as on routing (see
        # _validate_routing_compat).
        # Same rule as routing: honoured only when the operator declared the class.
        _require_exclude_class("avoid_tolls", "toll", opts.avoid_tolls, self.cfg.supported_exclude_classes)
        if not opts.origins or not opts.destinations:
            raise invalid_request("OSRM Matrix requires at least one origin and one destination")

        profile = _osrm_profile(opts.travel_mode)
        coords = join_coords(list(opts.origins) + list(opts.destinations), "lnglat", ";")
        sources = ";".join(str(i) for i in range(len(opts.origins)))
        dests = ";".join(str(i + len(opts.origins)) for i in range(len(opts.destinations)))
        url = f"{base_url}/table/v1/{profile}/{coords}"
        # `annotations` is a default, not an invariant: it sits in the base query so a
        # consumer's passthrough.query replaces it like any other list default.
        base_query = {"sources": sources, "destinations": dests, "annotations": ",".join(OSRM_TABLE_ANNOTATIONS)}
        if opts.avoid_tolls:
            base_query["exclude"] = "toll"
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _osrm_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if not isinstance(raw, dict) or raw.get("code") != "Ok":
            raise _osrm_matrix_in_body_error(raw, resp.status)

        durations = raw.get("durations")
        distances = raw.get("distances")
        no, nd = len(opts.origins), len(opts.destinations)
        if not dimensions_ok(durations, distances, no, nd):
            raise unknown_error(resp.status, raw, "OSRM matrix returned a table that does not match the requested dimensions")
        return MatrixResult(cells=grid_cells(durations, distances, no, nd), raw=raw)


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
    # OSRM has no time parameter and no request-time traffic: traffic needs a
    # graph rebuilt with "edge weight updates" (osrm-backend wiki, Traffic). So
    # traffic_mode is ignored, and departure_time with it — under NONE a time has
    # no effect by definition, so refusing it would reject a valid request.

    # Whether an avoid-flag works depends on the OPERATOR'S build, not on OSRM:
    # exclude=toll is rejected as InvalidValue by the public demo build and honoured
    # by a self-hosted instance with the class compiled in (verified live — it
    # genuinely rerouted). So the capability is declared in config, and anything not
    # declared is still rejected up front rather than sent and bounced with an
    # opaque vendor error.
    for flag, exclude_class, requested in _osrm_avoid_flags(opts):
        _require_exclude_class(flag, exclude_class, requested, supported)


def _require_exclude_class(flag: str, exclude_class: str, requested: bool, supported: Sequence[str]) -> None:
    """Reject a requested avoid-flag whose exclude class the operator has not declared."""
    if requested and exclude_class not in supported:
        msg = (
            f"{flag} requires an OSRM build with the '{exclude_class}' exclude "
            "class compiled in; declare it via OsrmConfig.supported_exclude_classes"
        )
        raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message=msg, provider_message=msg)


def _osrm_distance_duration(obj: Any, status: int, raw: Any) -> Tuple[float, float]:
    """A route's or leg's ``distance``/``duration``. OSRM always sends both, so a
    missing one is a malformed response rather than a free, instant trip."""
    distance, duration = jnum_opt(jget(obj, "distance")), jnum_opt(jget(obj, "duration"))
    if distance is None or duration is None:
        raise unknown_error(status, raw, "OSRM returned a route or leg without distance/duration")
    return distance, duration


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
    if code == "NotImplemented":
        # A trip endpoint combination this server does not implement: any/any on an
        # open trip, or first/any / any/last on a build older than v5.27.
        return ProviderCode.UNSUPPORTED_OPTION
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


def _osrm_http_error(status: int, headers, data: bytes, use_trip: bool = False) -> ConnectorError:
    raw = decode_json(data)
    # OSRM serves NoTrips with a 4xx as readily as in a 200 envelope, so /trip
    # classifies it the same on both paths. The Table service never produces it.
    return provider_error(status, headers, raw, _osrm_map_vendor_error(status, raw, use_trip), _osrm_error_message(raw))


def _osrm_route_in_body_error(body: Any, use_trip: bool, status_code: int) -> ConnectorError:
    """Map a 2xx OSRM envelope to a typed error.

    Reached when the envelope code is not ``Ok``, or when ``routes``/``trips`` came
    back empty.

    An ``Ok`` envelope with an empty ``routes[]`` is ``NO_ROUTE``, not ``UNKNOWN``:
    the envelope says the request was fine and the server answered, there is simply
    nothing to return — the same outcome as Google's empty ``routes[]``, so a
    consumer gets one "provider answered, no route" case to branch on.

    ``status_code`` is the real HTTP status: the server answered, and a ``None``
    status is what a transport failure looks like.
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


def _osrm_matrix_in_body_error(body: Any, status_code: int) -> ConnectorError:
    """Map a 2xx Table envelope that is not ``Ok``, with the same envelope codes as
    routing (a ``NoSegment`` is a coordinate with no road to snap to on either
    service). ``NoTable`` is the Table service's own invalid-request code."""
    code = jstr(jget(body, "code"))
    message = jstr(jget(body, "message"))
    if code == "NoTable":
        pc = ProviderCode.INVALID_REQUEST
    else:
        pc = _osrm_classify_envelope_code(code, message, False) or ProviderCode.UNKNOWN
    pm = message or f"OSRM returned code: {code or 'unknown'}"
    return classified_error(pc, status_code, body, pm)
