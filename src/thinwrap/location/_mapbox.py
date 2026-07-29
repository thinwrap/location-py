"""Mapbox connectors: routing (Directions / Optimized-Trips), matrix, geocoding
(Geocoding v6 + Searchbox), isochrone."""

from __future__ import annotations

from urllib.parse import quote

import uuid
from typing import Any, List, Optional

from ._jsonpath import jget, jlist, jnum, jnum_opt, jstr
from ._route_completeness import assert_route_has_legs
from ._util import decode_json, iso_string, ok_status
from ._waypoint_order import invert_waypoint_positions
from .base import BaseConnector
from .config import MapboxConfig
from .coordinate import assert_finite, fmt_coord, join_coords
from .enums import IsochroneType, PlaceDetailsInclude, PolylineQuality, TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, unknown_error
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
from .isochrone import IsochroneContour, IsochroneOptions, IsochroneResult, Polygon
from .latlng import LatLng
from .matrix import MatrixCell, MatrixOptions, MatrixResult
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
        use_optimized = opts.optimize or opts.optimize_fixed_origin or opts.optimize_fixed_destination or opts.is_round_trip
        profile = _mapbox_profile(opts.travel_mode)
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
        route = routes[0]

        legs = [RoutingLeg(jnum(l.get("distance")), jnum(l.get("duration"))) for l in (route.get("legs") or [])]
        # Normalize the geometry to precision-5, decoding according to the
        # `geometries` value actually sent — NOT the connector's polyline6
        # default, which _passthrough.query may have overridden.
        polyline = _normalize_mapbox_geometry(route.get("geometry"), geometries)

        assert_route_has_legs(len(legs), len(opts.waypoints), 'Mapbox Routing', raw)

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
            total_distance_meters=jnum(route.get("distance")),
            total_duration_seconds=jnum(route.get("duration")),
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
        ex = _mapbox_excludes(opts)
        if ex:
            base_query["exclude"] = ex
        if opts.departure_time:
            base_query["depart_at"] = iso_string(opts.departure_time)
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        return self.send_get(url, m_headers, m_query), _mapbox_effective_geometries(m_query)

    def _dispatch_optimized(self, opts: RoutingOptions, profile: str):
        # GET /optimized-trips/v1 — the single-vehicle waypoint-order optimizer
        # that matches every sibling provider (v2 is a fleet/VRP product for a
        # future multi-vehicle surface). v1 (OSRM-trip-based) rejects
        # source=any + destination=any + roundtrip=false, so plain optimize (and
        # the both-fixed case) keeps BOTH endpoints and reorders the middle,
        # matching Google/TomTom/HERE/Esri; the fixed flags pin just their
        # endpoint; is_round_trip returns to the first waypoint.
        coords = join_coords(opts.waypoints, "lnglat", ";")
        url = f"{_OPTIMIZED_TRIPS_URL}/{profile}/{coords}"
        base_query = {
            "access_token": self.cfg.access_token,
            "geometries": "polyline6",
            "overview": _mapbox_overview(opts),
            "roundtrip": "true" if opts.is_round_trip else "false",
        }
        if opts.is_round_trip:
            base_query["source"] = "first"
        elif opts.optimize_fixed_origin and not opts.optimize_fixed_destination:
            base_query["source"] = "first"
            base_query["destination"] = "any"
        elif opts.optimize_fixed_destination and not opts.optimize_fixed_origin:
            base_query["source"] = "any"
            base_query["destination"] = "last"
        else:
            base_query["source"] = "first"
            base_query["destination"] = "last"
        ex = _mapbox_excludes(opts)
        if ex:
            base_query["exclude"] = ex
        if opts.departure_time:
            base_query["depart_at"] = iso_string(opts.departure_time)
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        return self.send_get(url, m_headers, m_query), _mapbox_effective_geometries(m_query)


class MapboxMatrixConnector(BaseConnector):
    def __init__(self, config: MapboxConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        if not opts.origins or not opts.destinations:
            raise invalid_request("Mapbox Matrix requires non-empty origins and destinations")
        profile = _mapbox_profile(opts.travel_mode)
        coords = join_coords(list(opts.origins) + list(opts.destinations), "lnglat", ";")
        sources = ";".join(str(i) for i in range(len(opts.origins)))
        dests = ";".join(str(i + len(opts.origins)) for i in range(len(opts.destinations)))
        url = f"{_MATRIX_URL}/{profile}/{coords}"
        # `annotations` is a connector default set BEFORE the merge so a consumer's
        # passthrough.query can override it (setting it after the merge silently
        # ignored the override).
        base_query = {"access_token": self.cfg.access_token, "sources": sources, "destinations": dests, "annotations": "duration,distance"}
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raw = decode_json(resp.body)
            raise provider_error(resp.status, resp.headers, raw, _mapbox_status_code(resp.status), _mapbox_message(raw))
        raw = decode_json(resp.body)
        if not isinstance(raw, dict):
            raise unknown_error(resp.status, raw, "Mapbox Matrix returned a malformed response body")
        if raw.get("code") != "Ok":
            msg = "Mapbox returned code: " + jstr(raw.get("code"))
            raise classified_error(_mapbox_body_code(jstr(raw.get("code"))), resp.status, raw, msg)

        durations = raw.get("durations") or []
        distances = raw.get("distances") or []
        no, nd = len(opts.origins), len(opts.destinations)
        if not _dimensions_ok(durations, distances, no, nd):
            raise unknown_error(resp.status, raw, f"Mapbox Matrix returned a matrix that does not match the requested {no}x{nd} dimensions")
        # Mapbox returns null for an unroutable pair — omit the cell rather than
        # coercing to 0 (which reads as "same location"). Contract: missing/failed
        # entries are omitted from cells.
        cells = [
            MatrixCell(i, j, jnum(distances[i][j]), jnum(durations[i][j]))
            for i in range(no)
            for j in range(nd)
            if distances[i][j] is not None and durations[i][j] is not None
        ]
        return MatrixResult(cells=cells, raw=raw)


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
        raw = self._get(_GEOCODE_FORWARD_URL, base_query, opts.passthrough)
        return GeocodeResult(candidates=_normalize_mapbox_features(raw), raw=raw)

    def reverse_geocode(self, opts: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        assert_finite(opts.location, "Mapbox reverseGeocode")
        base_query = {"longitude": fmt_coord(opts.location.lng), "latitude": fmt_coord(opts.location.lat), "access_token": self.cfg.access_token}
        if opts.language:
            base_query["language"] = opts.language
        raw = self._get(_GEOCODE_REVERSE_URL, base_query, opts.passthrough)
        return ReverseGeocodeResult(candidates=_normalize_mapbox_features(raw), raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        base_query = {"q": opts.input, "access_token": self.cfg.access_token, "session_token": str(uuid.uuid4())}
        if opts.language:
            base_query["language"] = opts.language
        # `country_filter` (ISO 3166-1 alpha-2) → Searchbox `country=` (lowercase,
        # comma-separated), same translation as forward geocode.
        if opts.country_filter:
            base_query["country"] = ",".join(c.lower() for c in opts.country_filter)
        raw = self._get(_SEARCHBOX_URL, base_query, opts.passthrough)
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

    def _get(self, url, base_query, pt):
        _, m_headers, m_query = merge_passthrough({}, {}, pt, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raw = decode_json(resp.body)
            raise provider_error(resp.status, resp.headers, raw, _mapbox_status_code(resp.status), _mapbox_message(raw))
        return decode_json(resp.body)

    def place_details(self, opts: PlaceDetailsOptions) -> PlaceDetailsResult:
        """Resolve a Mapbox ``mapbox_id`` to a full candidate.

        ``GET https://api.mapbox.com/search/searchbox/v1/retrieve/{mapbox_id}``

        Pass the SAME ``session_token`` used for the preceding ``autocomplete()``
        call: Search Box bills per *session*, so a matching token makes
        suggest+retrieve one billable session while a missing or fresh one makes it
        two.
        """
        base_query = {"access_token": self.cfg.access_token}
        if opts.session_token:
            base_query["session_token"] = opts.session_token
        if opts.language:
            base_query["language"] = opts.language

        url = f"{_RETRIEVE_URL}/{quote(opts.place_id, safe='')}"
        raw = self._get(url, base_query, opts.passthrough)

        # Retrieve returns a GeoJSON FeatureCollection — the same shape as v6
        # geocode, so the geocode normalizer applies unchanged.
        candidates = _normalize_mapbox_features(raw)
        if not candidates:
            raise classified_error(
                ProviderCode.NO_ROUTE, None, raw, "Mapbox Place Details returned no feature"
            )

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
        from .isochrone_validate import validate_isochrone_cap

        validate_isochrone_cap(opts.values)
        assert_finite(opts.center, "Mapbox isochrone center")
        profile = _mapbox_profile(opts.travel_mode)
        url = f"{_ISOCHRONE_URL}/{profile}/{fmt_coord(opts.center.lng)},{fmt_coord(opts.center.lat)}"
        # `polygons` is a connector default set BEFORE the merge so a consumer's
        # passthrough.query can override it (setting it after the merge silently
        # ignored the override).
        base_query = {"access_token": self.cfg.access_token, "polygons": "true"}
        if opts.type == IsochroneType.TIME:
            from .polyline import _js_round

            base_query["contours_minutes"] = ",".join(str(_js_round(v / 60)) for v in opts.values)
        else:
            base_query["contours_meters"] = ",".join(fmt_coord(v) for v in opts.values)
        if opts.departure_time:
            base_query["depart_at"] = opts.departure_time
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
            contour = jnum(jget(jget(f, "properties"), "contour"))
            value = contour * 60 if opts.type == IsochroneType.TIME else contour
            coords = jget(jget(f, "geometry"), "coordinates") or []
            contours.append(IsochroneContour(value=value, geometry=Polygon(type="Polygon", coordinates=coords)))
        contours.sort(key=lambda c: c.value)
        return IsochroneResult(contours=contours, raw=raw)


# ---- shared mapbox helpers ----

def _mapbox_profile(m: TravelMode) -> str:
    if m == TravelMode.WALKING:
        return "walking"
    if m == TravelMode.CYCLING:
        return "cycling"
    return "driving"


def _mapbox_excludes(opts: RoutingOptions) -> str:
    ex = []
    if opts.avoid_tolls:
        ex.append("toll")
    if opts.avoid_ferries:
        ex.append("ferry")
    if opts.avoid_highways:
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
        # as 200, so the envelope code — not the status — decides.
        pc = (
            ProviderCode.NO_ROUTE
            if code in ("NoRoute", "NoTrips", "NoSegment")
            else ProviderCode.UNKNOWN
        )
    elif 500 <= status < 600:
        pc = ProviderCode.PROVIDER_UNAVAILABLE
    elif status == 400:
        pc = ProviderCode.INVALID_REQUEST
    else:
        pc = ProviderCode.UNKNOWN
    msg = vendor_message or (("Mapbox returned code: " + code) if code else f"Mapbox routing failed: HTTP {status}")
    # Route through provider_error (like the matrix/isochrone paths) so a 429
    # Retry-After lands in cause["retryAfter"] and the message/cause are redacted.
    return provider_error(status, headers, body, pc, msg)


def _dimensions_ok(durations, distances, no, nd) -> bool:
    if len(durations) < no or len(distances) < no:
        return False
    for i in range(no):
        if len(durations[i]) < nd or len(distances[i]) < nd:
            return False
    return True


def _normalize_mapbox_features(raw: Any) -> List[GeocodeCandidate]:
    out: List[GeocodeCandidate] = []
    for f in jlist(jget(raw, "features")) or []:
        coords = jget(jget(f, "geometry"), "coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        lng, lat = coords[0], coords[1]
        props = jget(f, "properties") or {}
        addr = jstr(jget(props, "full_address")) or jstr(jget(f, "place_name"))
        viewport = None
        bbox = jget(props, "bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            viewport = Viewport(LatLng(bbox[1], bbox[0]), LatLng(bbox[3], bbox[2]))
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


def _normalize_mapbox_geometry(geometry: Any, geometries: str) -> str:
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
    return encode_polyline(_decode_mapbox_p6(encoded))


def _decode_mapbox_p6(encoded: str) -> List[LatLng]:
    coords: List[LatLng] = []
    index = 0
    lat = lng = 0
    n = len(encoded)

    def decode_signed(idx: int):
        result = 0
        shift = 0
        while True:
            if idx >= n:
                raise _mapbox_malformed()
            b = ord(encoded[idx]) - 63
            idx += 1
            if b < 0 or b > 0x3F:
                raise _mapbox_malformed()
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


def _mapbox_malformed() -> ConnectorError:
    msg = "Malformed Mapbox polyline6 geometry"
    return ConnectorError(ProviderCode.UNKNOWN, message=msg, provider_message=msg)
