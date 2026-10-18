"""Mapbox connectors: routing (Directions / Optimized-Trips), matrix, geocoding
(Geocoding v6 + Searchbox), isochrone."""

from __future__ import annotations

from urllib.parse import quote

import uuid
from typing import Any, List, Optional

from ._contour_geometry import contour_geometry
from ._jsonpath import jget, jlist, jnum_opt, jstr
from ._matrix_grid import dimensions_ok, grid_cells
from ._route_completeness import assert_route_has_legs
from ._util import decode_json, iso_seconds_string, ok_status, traffic_requested
from ._waypoint_order import invert_waypoint_positions
from .base import BaseConnector
from .config import MapboxConfig
from .coordinate import assert_finite, fmt_coord, join_coords
from .defaults import MAPBOX_MATRIX_ANNOTATIONS
from .enums import IsochroneType, PlaceDetailsInclude, PolylineQuality, TrafficMode, TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, require_list, unknown_error
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
from .isochrone import IsochroneContour, IsochroneOptions, IsochroneResult
from .latlng import LatLng
from .matrix import MatrixOptions, MatrixResult
from .passthrough import merge_passthrough
from .polyline import encode_polyline
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_DIRECTIONS_URL = "https://api.mapbox.com/directions/v5/mapbox"
_OPTIMIZED_TRIPS_URL = "https://api.mapbox.com/optimized-trips/v1/mapbox"
_MATRIX_URL = "https://api.mapbox.com/directions-matrix/v1/mapbox"
_GEOCODE_FORWARD_URL = "https://api.mapbox.com/search/geocode/v6/forward"
_GEOCODE_REVERSE_URL = "https://api.mapbox.com/search/geocode/v6/reverse"
_SEARCHBOX_URL = "https://api.mapbox.com/search/searchbox/v1/suggest"
_RETRIEVE_URL = "https://api.mapbox.com/search/searchbox/v1/retrieve"
_ISOCHRONE_URL = "https://api.mapbox.com/isochrone/v1/mapbox"


class MapboxRoutingConnector(BaseConnector):
    def __init__(self, config: MapboxConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def route(self, opts: RoutingOptions) -> RoutingResult:
        if len(opts.waypoints) < 2:
            raise invalid_request("Mapbox Routing requires at least two waypoints")
        use_optimized = opts.optimize or opts.optimize_fixed_origin or opts.optimize_fixed_destination or opts.is_round_trip
        if use_optimized:
            _validate_optimized_compat(opts)
            # Optimization v1 has no driving-traffic profile, so traffic cannot be
            # applied on this path and is ignored (documented).
            profile = _mapbox_profile(opts.travel_mode)
        else:
            profile = _mapbox_profile(opts.travel_mode, opts.traffic_mode)
        resp, geometries = (
            self._dispatch_optimized(opts, profile) if use_optimized else self._dispatch_directions(opts, profile)
        )

        if not ok_status(resp.status):
            raise _mapbox_routing_vendor_error(resp.status, resp.headers, decode_json(resp.body))
        raw = decode_json(resp.body)
        if not isinstance(raw, dict):
            raise unknown_error(resp.status, raw, "Mapbox returned a malformed response body")
        code = jstr(raw.get("code"))
        if code and code != "Ok":
            msg = "Mapbox returned code: " + code
            raise classified_error(_mapbox_body_code(code), resp.status, raw, msg)

        routes = raw.get("routes") or raw.get("trips") or []
        if not routes:
            # A 2xx with an empty routes/trips array is Mapbox saying "nothing
            # found", not a malformed response.
            raise classified_error(
                ProviderCode.NO_ROUTE, resp.status, raw, "Mapbox returned no routes"
            )
        route = routes[0] if isinstance(routes, list) else None
        if not isinstance(route, dict):
            raise unknown_error(resp.status, raw, "Mapbox returned a malformed route")
        legs = _mapbox_legs(route, resp.status, raw)
        total_distance, total_duration = _mapbox_distance_duration(route, resp.status, raw)
        # Normalize the geometry to precision-5, decoding according to the
        # `geometries` value actually sent — NOT the connector's polyline6
        # default, which _passthrough.query may have overridden.
        polyline = _normalize_mapbox_geometry(route.get("geometry"), geometries, resp.status)

        assert_route_has_legs(len(legs), len(opts.waypoints), "Mapbox Routing", raw, resp.status)

        waypoint_order = None
        wps = raw.get("waypoints")
        if use_optimized and isinstance(wps, list):
            # Canonical waypoint_order = full visiting sequence of INPUT indices
            # (origin/destination inclusive). Mapbox returns waypoints[] in INPUT
            # order, where each waypoint_index is the position that input
            # waypoint occupies in the optimized trip — i.e. the INVERSE of the
            # canonical. Invert it, validated against the INPUT waypoint count so
            # a truncated or duplicate-index waypoints[] omits the ordering
            # instead of yielding a permutation that silently drops or repeats a
            # waypoint.
            waypoint_order = invert_waypoint_positions(
                [jget(wp, "waypoint_index") for wp in wps], len(opts.waypoints)
            )

        return RoutingResult(
            legs=legs,
            total_distance_meters=total_distance,
            total_duration_seconds=total_duration,
            polyline=polyline,
            waypoint_order=waypoint_order,
            raw=raw,
        )

    def _dispatch_directions(self, opts: RoutingOptions, profile: str):
        coords = join_coords(opts.waypoints, "lnglat", ";")
        url = f"{_DIRECTIONS_URL}/{profile}/{coords}"
        base_query = {
            "access_token": self.cfg.access_token,
            "geometries": "polyline6",
            "overview": _mapbox_overview(opts),
        }
        ex = _mapbox_excludes(opts, profile)
        if ex:
            base_query["exclude"] = ex
        # Mapbox documents `depart_at` for the driving profiles only. Walking and
        # cycling durations do not depend on traffic, so leaving it off there
        # returns the same route the caller would get with it. On plain `driving`
        # it is also left off: there `depart_at` makes the duration "a prediction
        # for travel time based on historical travel data" — typical traffic,
        # which TrafficMode.NONE promises not to add.
        if opts.departure_time and profile == "driving-traffic":
            # Mapbox documents `depart_at` as one of exactly three ISO 8601
            # forms, none carrying milliseconds — so seconds precision.
            base_query["depart_at"] = iso_seconds_string(opts.departure_time)
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        return self.send_get(url, m_headers, m_query), _mapbox_effective_geometries(m_query)

    def _dispatch_optimized(self, opts: RoutingOptions, profile: str):
        # GET /optimized-trips/v1 — the single-vehicle waypoint-order optimizer
        # that matches every sibling provider (v2 is a fleet/VRP product for a
        # future multi-vehicle surface). v1 (OSRM-trip-based) supports an open
        # route only as source=first + destination=last, so plain optimize and the
        # both-fixed case keep BOTH endpoints and reorder the middle, matching
        # Google/TomTom/HERE/Esri (a single fixed endpoint is rejected up front);
        # is_round_trip returns to the first waypoint, and there Mapbox accepts
        # destination=last as well (live: roundtrip=true&destination=last → Ok).
        coords = join_coords(opts.waypoints, "lnglat", ";")
        url = f"{_OPTIMIZED_TRIPS_URL}/{profile}/{coords}"
        base_query = {
            "access_token": self.cfg.access_token,
            "geometries": "polyline6",
            "overview": _mapbox_overview(opts),
            "roundtrip": "true" if opts.is_round_trip else "false",
        }
        base_query["source"] = "first"
        if not opts.is_round_trip or opts.optimize_fixed_destination:
            base_query["destination"] = "last"
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        return self.send_get(url, m_headers, m_query), _mapbox_effective_geometries(m_query)


class MapboxMatrixConnector(BaseConnector):
    def __init__(self, config: MapboxConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        if not opts.origins or not opts.destinations:
            raise invalid_request("Mapbox Matrix requires non-empty origins and destinations")
        # The Matrix API documents no `exclude`, and `depart_at` only as a sign-up
        # beta; a standard account gets HTTP 422 "Request too large for custom
        # parameters" for either. Refused up front rather than silently dropped, so
        # the caller never receives cells computed without the constraint they set.
        # A beta account can still send depart_at via passthrough.query. Under
        # TrafficMode.NONE, and on walking/cycling (no traffic variant), the time
        # has no effect by definition, so it is dropped rather than refused; only
        # driving traffic at a given time is unanswerable.
        if opts.avoid_tolls:
            msg = "Mapbox Matrix does not support avoid_tolls (the Matrix API has no exclude parameter)"
            raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message=msg, provider_message=msg)
        profile = _mapbox_profile(opts.travel_mode, opts.traffic_mode)
        if opts.departure_time is not None and profile == "driving-traffic":
            msg = "Mapbox Matrix does not support departure_time with traffic (depart_at is a sign-up beta; send it via passthrough.query if your account has it)"
            raise ConnectorError(ProviderCode.UNSUPPORTED_FIELD, message=msg, provider_message=msg)
        coords = join_coords(list(opts.origins) + list(opts.destinations), "lnglat", ";")
        sources = ";".join(str(i) for i in range(len(opts.origins)))
        dests = ";".join(str(i + len(opts.origins)) for i in range(len(opts.destinations)))
        url = f"{_MATRIX_URL}/{profile}/{coords}"
        # `annotations` is a default, not an invariant: it sits in the base query so a
        # consumer's passthrough.query replaces it like any other list default.
        base_query = {"access_token": self.cfg.access_token, "sources": sources, "destinations": dests, "annotations": ",".join(MAPBOX_MATRIX_ANNOTATIONS)}
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            # Matrix shares Directions' error envelope, including NoRoute /
            # NoSegment on a 422, so the body code decides as it does for routing.
            raise _mapbox_routing_vendor_error(resp.status, resp.headers, decode_json(resp.body))
        raw = decode_json(resp.body)
        if not isinstance(raw, dict):
            raise unknown_error(resp.status, raw, "Mapbox Matrix returned a malformed response body")
        if raw.get("code") != "Ok":
            msg = "Mapbox returned code: " + jstr(raw.get("code"))
            raise classified_error(_mapbox_body_code(jstr(raw.get("code"))), resp.status, raw, msg)

        durations = raw.get("durations")
        distances = raw.get("distances")
        no, nd = len(opts.origins), len(opts.destinations)
        if not dimensions_ok(durations, distances, no, nd):
            raise unknown_error(resp.status, raw, f"Mapbox Matrix returned a matrix that does not match the requested {no}x{nd} dimensions")
        return MatrixResult(cells=grid_cells(durations, distances, no, nd), raw=raw)


class MapboxGeocodingConnector(BaseConnector):
    def __init__(self, config: MapboxConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def geocode(self, opts: GeocodeOptions) -> GeocodeResult:
        base_query = {"q": opts.address, "access_token": self.cfg.access_token}
        if opts.language:
            base_query["language"] = opts.language
        if opts.country_filter:
            base_query["country"] = ",".join(c.lower() for c in opts.country_filter)
        raw = self._get(_GEOCODE_FORWARD_URL, base_query, opts.passthrough, "features")
        return GeocodeResult(candidates=_normalize_mapbox_features(raw), raw=raw)

    def reverse_geocode(self, opts: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        assert_finite(opts.location, "Mapbox reverseGeocode")
        base_query = {"longitude": fmt_coord(opts.location.lng), "latitude": fmt_coord(opts.location.lat), "access_token": self.cfg.access_token}
        if opts.language:
            base_query["language"] = opts.language
        raw = self._get(_GEOCODE_REVERSE_URL, base_query, opts.passthrough, "features")
        return ReverseGeocodeResult(candidates=_normalize_mapbox_features(raw), raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        # Search Box requires `session_token` on /suggest and groups every call that
        # shares one into a single billable session, closed by the /retrieve that
        # carries the same value. The caller's token is what makes that grouping
        # work; a fresh UUIDv4 (Mapbox's recommended format) keeps a one-off call
        # valid when none is supplied.
        base_query = {"q": opts.input, "access_token": self.cfg.access_token, "session_token": _mapbox_session_token(opts.session_token)}
        if opts.language:
            base_query["language"] = opts.language
        # `country_filter` (ISO 3166-1 alpha-2) → Searchbox `country=` (lowercase,
        # comma-separated), same translation as forward geocode.
        if opts.country_filter:
            base_query["country"] = ",".join(c.lower() for c in opts.country_filter)
        raw = self._get(_SEARCHBOX_URL, base_query, opts.passthrough, "suggestions")
        preds: List[AutocompletePrediction] = []
        for s in jlist(jget(raw, "suggestions")) or []:
            desc = jstr(jget(s, "full_address")) or jstr(jget(s, "name"))
            # Search Box returns `name` (the POI/street) and `place_formatted` (the
            # rest of the address) as separate fields.
            name = jstr(jget(s, "name"))
            structured = None
            if name:
                structured = AutocompleteStructuredFormat(
                    main_text=name,
                    secondary_text=jstr(jget(s, "place_formatted")) or None,
                )
            preds.append(
                AutocompletePrediction(
                    description=desc,
                    place_id=jstr(jget(s, "mapbox_id")) or None,
                    structured_format=structured,
                )
            )
        return AutocompleteResult(predictions=preds, raw=raw)

    def _get(self, url, base_query, pt, list_key: str):
        return self._get_with_status(url, base_query, pt, list_key)[1]

    def _get_with_status(self, url, base_query, pt, list_key: str, not_found_is_empty: bool = False):
        _, m_headers, m_query = merge_passthrough({}, {}, pt, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not_found_is_empty and resp.status == 404:
            return resp.status, decode_json(resp.body)
        if not ok_status(resp.status):
            raw = decode_json(resp.body)
            raise provider_error(resp.status, resp.headers, raw, _mapbox_status_code(resp.status), _mapbox_message(raw))
        raw = decode_json(resp.body)
        require_list(resp.status, raw, list_key, "Mapbox Search")
        return resp.status, raw

    def place_details(self, opts: PlaceDetailsOptions) -> PlaceDetailsResult:
        """Resolve a Mapbox ``mapbox_id`` to a full candidate.

        ``GET https://api.mapbox.com/search/searchbox/v1/retrieve/{mapbox_id}``

        Pass the SAME ``session_token`` used for the preceding ``autocomplete()``
        call: Search Box bills per *session*, so a matching token makes
        suggest+retrieve one billable session while a missing or fresh one makes it
        two. ``session_token`` is required on /retrieve, so a UUIDv4 is generated
        when none is supplied.
        """
        base_query = {"access_token": self.cfg.access_token, "session_token": _mapbox_session_token(opts.session_token)}
        if opts.language:
            base_query["language"] = opts.language

        url = f"{_RETRIEVE_URL}/{quote(opts.place_id, safe='')}"
        # /retrieve answers an id it has no data for with 404 ("No data found for
        # requested ID"): no such place, not a failed request.
        status, raw = self._get_with_status(url, base_query, opts.passthrough, "features", not_found_is_empty=True)
        if status == 404:
            return PlaceDetailsResult(candidate=None, raw=raw)

        # Retrieve returns a GeoJSON FeatureCollection — the same shape as v6
        # geocode, so the geocode normalizer applies unchanged.
        candidates = _normalize_mapbox_features(raw)
        if not candidates:
            return PlaceDetailsResult(candidate=None, raw=raw)

        name = None
        if opts.includes(PlaceDetailsInclude.NAME):
            features = jlist(jget(raw, "features")) or []
            if features:
                name = jstr(jget(jget(features[0], "properties"), "name")) or None

        return PlaceDetailsResult(candidate=candidates[0], name=name, raw=raw)


class MapboxIsochroneConnector(BaseConnector):
    def __init__(self, config: MapboxConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def isochrone(self, opts: IsochroneOptions) -> IsochroneResult:
        from .isochrone_validate import isochrone_departure, validate_isochrone_cap

        validate_isochrone_cap(opts.values)
        assert_finite(opts.center, "Mapbox isochrone center")
        is_time = opts.type == IsochroneType.TIME
        # Mapbox takes whole minutes / whole meters, in increasing order ("Times
        # must be in increasing order"). `sent` maps each wire value back to the
        # caller's own value, so a 90 s break reads back as 90 s, not 2 min × 60.
        sent = _mapbox_contour_values(opts.values, is_time)
        profile = _mapbox_profile(opts.travel_mode)
        url = f"{_ISOCHRONE_URL}/{profile}/{fmt_coord(opts.center.lng)},{fmt_coord(opts.center.lat)}"
        # `polygons` is a connector default set BEFORE the merge so a consumer's
        # passthrough.query can override it.
        base_query = {"access_token": self.cfg.access_token, "polygons": "true"}
        base_query["contours_minutes" if is_time else "contours_meters"] = ",".join(str(k) for k in sent)
        if opts.departure_time is not None:
            base_query["depart_at"] = isochrone_departure(opts.departure_time, "Mapbox Isochrone")
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raw = decode_json(resp.body)
            raise provider_error(resp.status, resp.headers, raw, _mapbox_status_code(resp.status), _mapbox_message(raw))
        raw = decode_json(resp.body)
        if not isinstance(raw, dict):
            raise unknown_error(resp.status, raw, "Mapbox returned a non-JSON/unparseable body")
        contours: List[IsochroneContour] = []
        for f in jlist(jget(raw, "features")) or []:
            contour = jnum_opt(jget(jget(f, "properties"), "contour"))
            parts = _mapbox_polygon_parts(jget(f, "geometry"))
            if contour is None or not parts:
                raise unknown_error(resp.status, raw, "Mapbox Isochrone returned a contour without a value or polygon")
            # A contour Mapbox echoes that the connector did not send (a
            # passthrough.query override) falls back to a plain unit conversion.
            fallback = contour * 60 if is_time else contour
            value = sent.get(int(contour), fallback) if contour.is_integer() else fallback
            contours.append(IsochroneContour(value=value, geometry=contour_geometry(parts)))
        if not contours:
            raise unknown_error(resp.status, raw, "Mapbox Isochrone returned no contours")
        contours.sort(key=lambda c: c.value)
        return IsochroneResult(contours=contours, raw=raw)


# ---- shared mapbox helpers ----

def _mapbox_contour_values(values, is_time: bool) -> dict:
    """``{wire value: caller value}`` in ascending wire order.

    Mapbox accepts integer minutes (``contours_minutes``) and integer meters
    (``contours_meters``) only, in increasing order. A time break under 60 s rounds
    to 0 minutes, which Mapbox rejects, and two breaks that round to the same
    integer would come back as one contour — so both are refused up front rather
    than sent and silently merged."""
    from .polyline import _js_round

    sent: dict = {}
    for v in sorted(values):
        if is_time and v < 60:
            raise invalid_request(
                f"Mapbox isochrone time breaks are whole minutes; {fmt_coord(v)} s is under the 60 s minimum"
            )
        key = _js_round(v / 60) if is_time else _js_round(v)
        if key <= 0:
            raise invalid_request(f"Mapbox isochrone distance breaks are whole meters; {fmt_coord(v)} m rounds to 0")
        if key in sent:
            unit = "minute" if is_time else "meter"
            raise invalid_request(
                f"Mapbox isochrone breaks {fmt_coord(sent[key])} and {fmt_coord(v)} round to the same {unit} ({key})"
            )
        sent[key] = v
    return sent


def _mapbox_polygon_parts(geometry: Any) -> list:
    """A feature geometry as a list of ``[exterior, *holes]`` parts; empty when
    nothing polygonal is usable. GeoJSON allows a MultiPolygon feature, so one is
    accepted alongside the Polygon that ``polygons=true`` normally returns; a
    missing ``type`` is read as Polygon, as before.

    Unusable pieces are skipped one at a time, as the sibling libraries do: a
    part without a usable exterior is dropped, an unusable hole is dropped from
    its part, and the remaining parts are kept — one bad ring should not throw
    away the rest of the contour."""
    coords = jget(geometry, "coordinates")
    polygons = coords if jget(geometry, "type") == "MultiPolygon" else [coords]
    parts = []
    for polygon in polygons if isinstance(polygons, list) else []:
        if not isinstance(polygon, list) or not polygon or not _is_ring(polygon[0]):
            continue
        parts.append([polygon[0]] + [hole for hole in polygon[1:] if _is_ring(hole)])
    return parts


def _is_ring(ring: Any) -> bool:
    """Whether ``ring`` is a non-empty list of ``[lng, lat]`` number pairs."""
    if not isinstance(ring, list) or not ring:
        return False
    return all(
        isinstance(point, list) and len(point) >= 2 and jnum_opt(point[0]) is not None and jnum_opt(point[1]) is not None
        for point in ring
    )


def _mapbox_profile(m: TravelMode, traffic: TrafficMode = TrafficMode.NONE) -> str:
    if m == TravelMode.WALKING:
        return "walking"
    if m == TravelMode.CYCLING:
        return "cycling"
    # Mapbox selects traffic by PROFILE, not by a flag: driving-traffic on
    # Directions and Matrix. Walking and cycling have no traffic variant, so
    # traffic is ignored there.
    return "driving-traffic" if traffic_requested(traffic) else "driving"


def _mapbox_session_token(supplied: Optional[str]) -> str:
    """The caller's Search Box session token, else a fresh UUIDv4."""
    return supplied if supplied else str(uuid.uuid4())


def _validate_optimized_compat(opts: RoutingOptions) -> None:
    """Pre-flight checks for the Optimization v1 path.

    Its documented query parameters are annotations, approaches, bearings,
    destination, distributions, geometries, language, overview, radiuses, source,
    steps and roundtrip, and "unrecognized options in the query string result in an
    InvalidInput error" — so there is no ``exclude`` and no ``depart_at`` to send.
    Refusing the options up front gives the caller a typed error naming the option
    rather than a vendor InvalidInput. A departure time under TrafficMode.NONE, or
    on walking/cycling (which have no traffic variant), has no effect by
    definition, so it is dropped rather than refused.
    """
    _validate_trip_endpoints(opts)
    if opts.avoid_tolls or opts.avoid_ferries or opts.avoid_highways:
        msg = "Mapbox Optimization v1 does not support avoid_tolls / avoid_ferries / avoid_highways"
        raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message=msg, provider_message=msg)
    if opts.departure_time is not None and _mapbox_profile(opts.travel_mode, opts.traffic_mode) == "driving-traffic":
        msg = "Mapbox Optimization v1 does not support departure_time with traffic"
        raise ConnectorError(ProviderCode.UNSUPPORTED_FIELD, message=msg, provider_message=msg)


def _validate_trip_endpoints(opts: RoutingOptions) -> None:
    """Reject an open trip that pins exactly one endpoint.

    Optimization v1 answers ``first/any`` and ``any/last`` with
    ``200 {"code":"NotImplemented"}`` when ``roundtrip=false`` (live-verified), even
    though the OSRM Trip service it descends from accepts both since v5.27.
    Quietly pinning the other endpoint as well would return a route whose last (or
    first) stop the caller never fixed, so the option is refused before any request.
    """
    if opts.is_round_trip or opts.optimize_fixed_origin == opts.optimize_fixed_destination:
        return
    pinned = "optimize_fixed_origin" if opts.optimize_fixed_origin else "optimize_fixed_destination"
    msg = (
        f"Mapbox Optimization v1 cannot optimize an open route with only {pinned} set: with roundtrip=false "
        "only a fixed origin AND a fixed destination is supported. Set both flags, or use is_round_trip."
    )
    raise ConnectorError(ProviderCode.UNSUPPORTED_OPTION, message=msg, provider_message=msg)


def _mapbox_excludes(opts: RoutingOptions, profile: str) -> str:
    ex = []
    # Walking and cycling accept only a subset of exclude classes (live: `toll` and
    # `motorway` → 422 "exclude value must be one of: ferry, cash_only_tolls, ...").
    # Nothing is lost by leaving those two off: tolls do not apply to either mode,
    # the cycling profile already avoids highways, and walking follows sidewalks
    # and trails. `ferry` is accepted by every profile.
    motorised = profile not in ("walking", "cycling")
    if opts.avoid_tolls and motorised:
        ex.append("toll")
    if opts.avoid_ferries:
        ex.append("ferry")
    if opts.avoid_highways and motorised:
        ex.append("motorway")
    return ",".join(ex)


def _mapbox_message(body: Any) -> str:
    return jstr(jget(body, "message")) or jstr(jget(body, "error"))


def _mapbox_status_code(status: int) -> ProviderCode:
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if status in (422, 400):
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _mapbox_overview(opts: RoutingOptions) -> str:
    """Map the normalized ``polyline_quality`` onto Mapbox's ``overview``.

    ``simplified`` is the default and the reason is measured, not aesthetic: on one
    ~140km route the simplified geometry was 203 characters against 6146 for
    ``full`` — a 30x payload for vertices most callers never look at, with
    identical distances and durations.

    ``steps`` and ``annotations`` are deliberately NOT sent. Nothing in
    ``RoutingResult`` reads turn-by-turn steps or per-segment annotations, and
    steps are the single largest part of a Mapbox routing response — so requesting
    them inflated every response for data the wrapper then discarded. A consumer
    who wants them adds ``passthrough.query``.
    """
    return "full" if opts.polyline_quality == PolylineQuality.DETAILED else "simplified"


def _mapbox_body_code(code: str) -> ProviderCode:
    # The request was well-formed and Mapbox answered — there is simply no
    # connecting route (or no road near a coordinate to snap to). Live-verified:
    # Mapbox serves this on HTTP 200 as well as 422.
    if code in ("NoRoute", "NoTrips", "NoSegment"):
        return ProviderCode.NO_ROUTE
    if code in ("InvalidInput", "ProfileNotFound"):
        return ProviderCode.INVALID_REQUEST
    # Optimization v1 answers a source/destination/roundtrip combination it does
    # not implement with HTTP 200 and this code (live-verified); reachable when
    # passthrough overrides the combination the connector chose.
    if code == "NotImplemented":
        return ProviderCode.UNSUPPORTED_OPTION
    return ProviderCode.UNKNOWN


def _mapbox_routing_vendor_error(status: int, headers, body: Any) -> ConnectorError:
    code = jstr(jget(body, "code"))
    vendor_message = _mapbox_message(body)
    if status in (401, 403):
        pc = ProviderCode.AUTH_FAILED
    elif status == 429:
        pc = ProviderCode.RATE_LIMITED
    elif status == 422:
        # Live-verified: Mapbox serves its no-route envelope with HTTP 422 as well
        # as 200, so the envelope code — not the status — decides. ProcessingError
        # is Mapbox failing on a valid request, so it stays unknown. Every other
        # 422 (InvalidInput, e.g. an exclude class the profile rejects) is a
        # request the caller has to change.
        if code in ("NoRoute", "NoTrips", "NoSegment"):
            pc = ProviderCode.NO_ROUTE
        elif code == "ProcessingError":
            pc = ProviderCode.UNKNOWN
        else:
            pc = ProviderCode.INVALID_REQUEST
    elif 500 <= status < 600:
        pc = ProviderCode.PROVIDER_UNAVAILABLE
    elif status == 400:
        pc = ProviderCode.INVALID_REQUEST
    else:
        pc = ProviderCode.UNKNOWN
    msg = vendor_message or (("Mapbox returned code: " + code) if code else f"Mapbox request failed: HTTP {status}")
    # Route through provider_error (like the matrix/isochrone paths) so a 429
    # Retry-After lands in cause["retryAfter"] and the message/cause are redacted.
    return provider_error(status, headers, body, pc, msg)


def _mapbox_distance_duration(obj: Any, status: int, raw: Any) -> tuple:
    """A route's or leg's ``distance``/``duration``. Mapbox always sends both, so a
    missing one is a malformed response — reading it as 0 would report a free,
    instant trip."""
    distance, duration = jnum_opt(jget(obj, "distance")), jnum_opt(jget(obj, "duration"))
    if distance is None or duration is None:
        raise unknown_error(status, raw, "Mapbox returned a route or leg without distance/duration")
    return distance, duration


def _mapbox_legs(route: Any, status: int, raw: Any) -> List[RoutingLeg]:
    legs = []
    for leg in jlist(jget(route, "legs")) or []:
        distance, duration = _mapbox_distance_duration(leg, status, raw)
        legs.append(RoutingLeg(distance, duration))
    return legs


def _normalize_mapbox_features(raw: Any) -> List[GeocodeCandidate]:
    out: List[GeocodeCandidate] = []
    for f in jlist(jget(raw, "features")) or []:
        coords = jget(jget(f, "geometry"), "coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        lng, lat = jnum_opt(coords[0]), jnum_opt(coords[1])
        if lat is None or lng is None:
            continue
        props = jget(f, "properties")
        addr = jstr(jget(props, "full_address")) or jstr(jget(f, "place_name"))
        viewport = None
        bbox = jget(props, "bbox")
        # [minLng, minLat, maxLng, maxLat]; dropped whole unless all four are numbers.
        corners = [jnum_opt(c) for c in bbox] if isinstance(bbox, list) and len(bbox) == 4 else [None]
        if all(c is not None for c in corners):
            min_lng, min_lat, max_lng, max_lat = corners
            viewport = Viewport(LatLng(min_lat, min_lng), LatLng(max_lat, max_lng))
        out.append(GeocodeCandidate(formatted_address=addr, location=LatLng(lat, lng), place_id=jstr(jget(props, "mapbox_id")) or None, viewport=viewport))
    return out


def _mapbox_effective_geometries(query: Any) -> str:
    """The `geometries` value actually sent, after `_passthrough.query` merged
    over the connector's own ``polyline6``.

    The geometry decoder MUST match what was requested. Decoding a precision-5
    ``polyline`` with the precision-6 decoder divides every coordinate by 10 — a
    silent 10x position shift, not an error — so the connector reads back its own
    effective query rather than assuming its default survived the override.
    """
    if isinstance(query, dict):
        value = query.get("geometries")
        if isinstance(value, str) and value:
            return value
    return "polyline6"


def _normalize_mapbox_geometry(geometry: Any, geometries: str, status: Optional[int] = None) -> str:
    """Normalize a Mapbox route geometry to the canonical precision-5 polyline,
    honoring the effective ``geometries`` parameter:

    * ``polyline6`` (connector default) — decode at precision 6, re-encode at 5.
    * ``polyline`` — already precision-5; emit verbatim (as OSRM does).
    * ``geojson`` — encode the ``[lng, lat]`` coordinate pairs at precision 5.

    Returns ``""`` for an absent, empty, or unparseable geometry rather than
    raising — the leg distance/duration fields are still meaningful.
    """
    if geometries == "geojson":
        coordinates = jget(geometry, "coordinates")
        if not isinstance(coordinates, list):
            return ""
        points: List[LatLng] = []
        for pair in coordinates:
            if not isinstance(pair, list) or len(pair) < 2:
                return ""
            lng, lat = jnum_opt(pair[0]), jnum_opt(pair[1])
            if lat is None or lng is None:
                return ""
            # GeoJSON is [lng, lat] order.
            points.append(LatLng(lat, lng))
        return encode_polyline(points)

    encoded = jstr(geometry)
    if not encoded:
        return ""
    if geometries == "polyline":
        return encoded
    return encode_polyline(_decode_mapbox_p6(encoded, status))


def _decode_mapbox_p6(encoded: str, status: Optional[int] = None) -> List[LatLng]:
    coords: List[LatLng] = []
    index = 0
    lat = lng = 0
    n = len(encoded)

    def decode_signed(idx: int):
        result = 0
        shift = 0
        while True:
            if idx >= n:
                raise _mapbox_malformed(status)
            b = ord(encoded[idx]) - 63
            idx += 1
            if b < 0 or b > 0x3F:
                raise _mapbox_malformed(status)
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        val = ~(result >> 1) if (result & 1) else (result >> 1)
        return val, idx

    while index < n:
        d_lat, index = decode_signed(index)
        lat += d_lat
        d_lng, index = decode_signed(index)
        lng += d_lng
        coords.append(LatLng(lat / 1e6, lng / 1e6))
    return coords


def _mapbox_malformed(status: Optional[int]) -> ConnectorError:
    msg = "Malformed Mapbox polyline6 geometry"
    return ConnectorError(ProviderCode.UNKNOWN, status_code=status, message=msg, provider_message=msg)
