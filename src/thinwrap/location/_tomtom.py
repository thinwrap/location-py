"""TomTom connectors: routing, matrix (conditional sync/async at 2500 cells),
geocoding (Search v2), isochrone (calculateReachableRange, one call per value)."""

from __future__ import annotations

import time
from typing import Any, Callable, List, Optional
from urllib.parse import quote

from ._jsonpath import jget, jlist, jnum, jnum_opt, jstr
from ._util import decode_json, iso_string, ok_status
from ._waypoint_order import is_complete_waypoint_order
from .base import BaseConnector
from .config import TomTomConfig
from .coordinate import assert_finite, fmt_coord, to_lat_lng_string
from .enums import IsochroneType, PlaceDetailsInclude, RoutingInclude, TrafficMode, TravelMode
from .errors import (
    ConnectorError,
    ProviderCode,
    classified_error,
    invalid_request,
    provider_error,
    unknown_error,
)
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
from .passthrough import Passthrough, merge_passthrough
from .poll import POLL_BACKOFF, POLL_INITIAL_DELAY, POLL_MAX_DELAY, extract_timeout_ms
from .polyline import encode_polyline
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_ROUTE_URL = "https://api.tomtom.com/routing/1/calculateRoute"
_SYNC_MATRIX_URL = "https://api.tomtom.com/routing/matrix/2"
_ASYNC_MATRIX_URL = "https://api.tomtom.com/routing/matrix/2/async"
_GEOCODE_URL = "https://api.tomtom.com/search/2/geocode"
_REVERSE_URL = "https://api.tomtom.com/search/2/reverseGeocode"
_SEARCH_URL = "https://api.tomtom.com/search/2/search"
_PLACE_BY_ID_URL = "https://api.tomtom.com/search/2/place.json"
_REACHABLE_RANGE_URL = "https://api.tomtom.com/routing/1/calculateReachableRange"
_SYNC_CELL_THRESHOLD = 2500


class TomTomRoutingConnector(BaseConnector):
    def __init__(self, config: TomTomConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def route(self, opts: RoutingOptions) -> RoutingResult:
        wps = list(opts.waypoints)
        if len(wps) < 2:
            raise invalid_request("TomTom Routing requires at least two waypoints")
        for w in wps:
            assert_finite(w, "TomTom Routing")
        # TomTom computeBestOrder reorders intermediate waypoints while keeping the
        # first/last fixed (an OPEN route); it has no closed round-trip mode.
        if opts.is_round_trip:
            raise ConnectorError(
                ProviderCode.UNSUPPORTED_OPTION,
                message="TomTom route optimization does not support round trips (isRoundTrip)",
                provider_message="TomTom computeBestOrder optimizes an open route (fixed first/last waypoint) and cannot return a closed round trip; remove isRoundTrip or use a provider that supports it (e.g. Mapbox/OSRM).",
            )
        locations = ":".join(to_lat_lng_string(w) for w in wps)
        url = f"{_ROUTE_URL}/{locations}/json"
        base_query = {"key": self.cfg.api_key, "travelMode": _tomtom_travel_mode(opts.travel_mode), "routeType": "fastest", "routeRepresentation": "polyline"}
        if opts.optimize and len(wps) > 2:
            base_query["computeBestOrder"] = "true"
        # TomTom's `traffic` parameter defaults to ON at the vendor, so leaving it
        # unset would contradict the normalized default of TrafficMode.NONE. Send it
        # explicitly in both directions.
        base_query["traffic"] = "true" if opts.traffic_mode == TrafficMode.LIVE else "false"

        # noTrafficTravelTimeInSeconds only appears when computeTravelTimeFor=all is
        # requested, and that asks TomTom for extra computed values — so unlike
        # Google/HERE it is a real request change and stays strictly opt-in.
        wants_no_traffic = opts.includes(RoutingInclude.DURATION_WITHOUT_TRAFFIC)
        if wants_no_traffic:
            base_query["computeTravelTimeFor"] = "all"

        if opts.departure_time:
            base_query["departAt"] = iso_string(opts.departure_time)
        av = _tomtom_avoids(opts)
        if av:
            base_query["avoid"] = av
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _tomtom_http_error(resp.status, resp.headers, resp.body, True)
        raw = decode_json(resp.body)
        routes = raw.get("routes") if isinstance(raw, dict) else None
        if not routes:
            raise classified_error(
                ProviderCode.NO_ROUTE, resp.status, raw, "TomTom Routing returned no routes"
            )
        route = routes[0]

        legs: List[RoutingLeg] = []
        pts: List[LatLng] = []
        for leg in route.get("legs") or []:
            summ = leg.get("summary") or {}
            no_traffic = summ.get("noTrafficTravelTimeInSeconds")
            legs.append(
                RoutingLeg(
                    jnum(summ.get("lengthInMeters")),
                    jnum(summ.get("travelTimeInSeconds")),
                    duration_without_traffic_seconds=(
                        float(no_traffic)
                        if wants_no_traffic
                        and isinstance(no_traffic, (int, float))
                        and not isinstance(no_traffic, bool)
                        else None
                    ),
                )
            )
            for p in leg.get("points") or []:
                pts.append(LatLng(jnum(p.get("latitude")), jnum(p.get("longitude"))))

        waypoint_order = None
        ow = raw.get("optimizedWaypoints")
        if opts.optimize and isinstance(ow, list):
            ordered = sorted(ow, key=lambda w: jnum(w.get("optimizedIndex")))
            # TomTom optimizedWaypoints covers ONLY the intermediate waypoints
            # (providedIndex 0-based over intermediates, origin/destination
            # excluded). Project to full input indices and bracket with the
            # fixed origin (0) and destination (N-1) for the canonical order.
            #
            # The projection is only meaningful if it yields a complete
            # permutation: a short, duplicated, or sentinel providedIndex list
            # would otherwise produce an ordering that silently drops or repeats
            # a waypoint.
            intermediates = [int(jnum(w.get("providedIndex"))) + 1 for w in ordered]
            candidate = [0, *intermediates, len(opts.waypoints) - 1]
            if is_complete_waypoint_order(candidate, len(opts.waypoints)):
                waypoint_order = candidate

        summary = route.get("summary") or {}
        total_no_traffic = summary.get("noTrafficTravelTimeInSeconds")
        return RoutingResult(
            legs=legs,
            total_distance_meters=jnum(summary.get("lengthInMeters")),
            total_duration_seconds=jnum(summary.get("travelTimeInSeconds")),
            polyline=encode_polyline(pts),
            waypoint_order=waypoint_order,
            raw=raw,
            total_duration_without_traffic_seconds=(
                float(total_no_traffic)
                if wants_no_traffic
                and isinstance(total_no_traffic, (int, float))
                and not isinstance(total_no_traffic, bool)
                else None
            ),
        )


class TomTomMatrixConnector(BaseConnector):
    def __init__(self, config: TomTomConfig, transport=None, sleep: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(transport)
        self.cfg = config
        self._sleep = sleep or time.sleep

    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        if len(opts.origins) * len(opts.destinations) <= _SYNC_CELL_THRESHOLD:
            return self._matrix_sync(opts)
        return self._matrix_async(opts)

    def _build_request(self, opts: MatrixOptions):
        for o in opts.origins:
            assert_finite(o, "TomTom matrix origin")
        for d in opts.destinations:
            assert_finite(d, "TomTom matrix destination")
        body: dict[str, Any] = {"origins": [{"point": {"latitude": o.lat, "longitude": o.lng}} for o in opts.origins], "destinations": [{"point": {"latitude": d.lat, "longitude": d.lng}} for d in opts.destinations]}
        options: dict[str, Any] = {"travelMode": _tomtom_matrix_travel_mode(opts.travel_mode)}
        if opts.avoid_tolls:
            options["avoid"] = ["tollRoads"]
        if opts.departure_time:
            options["departAt"] = iso_string(opts.departure_time)
        body["options"] = options
        return body, {"key": self.cfg.api_key}

    def _matrix_sync(self, opts: MatrixOptions) -> MatrixResult:
        body, query = self._build_request(opts)
        _, clean_pt = extract_timeout_ms(opts.passthrough)
        m_body, m_headers, m_query = merge_passthrough(body, {}, clean_pt, query)
        resp = self.send_post_json(_SYNC_MATRIX_URL, m_body, m_headers, m_query)
        if not ok_status(resp.status):
            raise _tomtom_http_error(resp.status, resp.headers, resp.body, True)
        return _tomtom_normalize_cells(resp.status, resp.body, len(opts.origins), len(opts.destinations))

    def _matrix_async(self, opts: MatrixOptions) -> MatrixResult:
        deadline, clean_pt = extract_timeout_ms(opts.passthrough)
        job_id = self._submit(opts, clean_pt)
        self._poll(job_id, deadline)
        return self._retrieve(job_id, len(opts.origins), len(opts.destinations))

    def _submit(self, opts: MatrixOptions, pt: Optional[Passthrough]) -> str:
        body, query = self._build_request(opts)
        m_body, m_headers, m_query = merge_passthrough(body, {}, pt, query)
        resp = self.send_post_json(_ASYNC_MATRIX_URL, m_body, m_headers, m_query)
        if not ok_status(resp.status):
            raise _tomtom_http_error(resp.status, resp.headers, resp.body, True)
        job_id = jstr(jget(decode_json(resp.body), "jobId"))
        if not job_id:
            raise unknown_error(resp.status, decode_json(resp.body), "TomTom Matrix submit response missing jobId")
        return job_id

    def _poll(self, job_id: str, deadline: float) -> None:
        status_url = f"{_ASYNC_MATRIX_URL}/{quote(job_id, safe='')}"
        deadline_at = time.monotonic() + deadline
        delay = POLL_INITIAL_DELAY
        while True:
            now = time.monotonic()
            if now >= deadline_at:
                break
            self._sleep(min(delay, deadline_at - now))
            delay = min(POLL_MAX_DELAY, delay * POLL_BACKOFF)
            resp = self.send_get(status_url, None, {"key": self.cfg.api_key})
            if not ok_status(resp.status):
                raise _tomtom_http_error(resp.status, resp.headers, resp.body, True)
            state = jstr(jget(decode_json(resp.body), "state"))
            # TomTom Matrix v2 async job states are Submitted | Validated |
            # Completed | Failed. Success is "Completed" (there is no "Succeeded").
            if state == "Completed":
                return
            if state == "Failed":
                raise ConnectorError(ProviderCode.PROVIDER_UNAVAILABLE, status_code=resp.status, provider_message="TomTom Matrix job failed", cause=decode_json(resp.body))
        raise ConnectorError(ProviderCode.MATRIX_POLLING_TIMEOUT, message="TomTom Matrix polling deadline exceeded", provider_message=f"jobId: {job_id}", cause={"jobId": job_id})

    def _retrieve(self, job_id: str, no: int, nd: int) -> MatrixResult:
        result_url = f"{_ASYNC_MATRIX_URL}/{quote(job_id, safe='')}/result"
        resp = self.send_get(result_url, None, {"key": self.cfg.api_key})
        if not ok_status(resp.status):
            raise _tomtom_http_error(resp.status, resp.headers, resp.body, True)
        return _tomtom_normalize_cells(resp.status, resp.body, no, nd)


class TomTomGeocodingConnector(BaseConnector):
    def __init__(self, config: TomTomConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def geocode(self, opts: GeocodeOptions) -> GeocodeResult:
        if opts.address == "":
            raise invalid_request("TomTom Geocoding requires a non-empty address")
        url = f"{_GEOCODE_URL}/{quote(opts.address, safe='')}.json"
        base_query = {"key": self.cfg.api_key}
        if opts.language:
            base_query["language"] = opts.language
        if opts.country_filter:
            base_query["countrySet"] = ",".join(opts.country_filter)
        raw = self._get(url, base_query, opts.passthrough)
        cands = [
            c
            for c in (_normalize_tomtom_candidate(r) for r in (jlist(jget(raw, "results")) or []))
            if c is not None
        ]
        return GeocodeResult(candidates=cands, raw=raw)

    def reverse_geocode(self, opts: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        assert_finite(opts.location, "TomTom reverseGeocode")
        url = f"{_REVERSE_URL}/{to_lat_lng_string(opts.location)}.json"
        base_query = {"key": self.cfg.api_key}
        if opts.language:
            base_query["language"] = opts.language
        raw = self._get(url, base_query, opts.passthrough)
        cands: List[GeocodeCandidate] = []
        for a in jlist(jget(raw, "addresses")) or []:
            pos = jstr(jget(a, "position"))
            parts = pos.split(",")
            if len(parts) != 2:
                continue
            try:
                lat, lng = float(parts[0].strip()), float(parts[1].strip())
            except ValueError:
                continue
            cands.append(GeocodeCandidate(formatted_address=jstr(jget(jget(a, "address"), "freeformAddress")), location=LatLng(lat, lng), place_id=jstr(jget(a, "id")) or None))
        return ReverseGeocodeResult(candidates=cands, raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        if opts.input == "":
            raise invalid_request("TomTom Autocomplete requires a non-empty input")
        url = f"{_SEARCH_URL}/{quote(opts.input, safe='')}.json"
        base_query = {"key": self.cfg.api_key, "typeahead": "true", "limit": "5"}
        if opts.language:
            base_query["language"] = opts.language
        if opts.location is not None:
            assert_finite(opts.location, "TomTom autocomplete location")
            base_query["lat"] = fmt_coord(opts.location.lat)
            base_query["lon"] = fmt_coord(opts.location.lng)
        if opts.radius is not None:
            base_query["radius"] = fmt_coord(opts.radius)
        # `country_filter` (ISO 3166-1 alpha-2) → TomTom `countrySet=<comma-csv>`,
        # same translation as forward geocode.
        if opts.country_filter:
            base_query["countrySet"] = ",".join(opts.country_filter)
        raw = self._get(url, base_query, opts.passthrough)
        preds: List[AutocompletePrediction] = []
        for r in jlist(jget(raw, "results")) or []:
            free = jstr(jget(jget(r, "address"), "freeformAddress"))
            poi_name = jstr(jget(jget(r, "poi"), "name"))
            desc = f"{poi_name}, {free}" if poi_name else free
            # Live-verified: `poi.name` is undefined for street/address results, which
            # have no distinct main part. Leave the whole object None there rather than
            # splitting `freeformAddress` on a comma, which would be a guess.
            poi_name = jstr(jget(jget(r, "poi"), "name"))
            structured = None
            if poi_name:
                structured = AutocompleteStructuredFormat(
                    main_text=poi_name,
                    secondary_text=jstr(jget(jget(r, "address"), "freeformAddress")) or None,
                )
            preds.append(
                AutocompletePrediction(
                    description=desc,
                    place_id=jstr(jget(r, "id")) or None,
                    structured_format=structured,
                )
            )
        return AutocompleteResult(predictions=preds, raw=raw)

    def _get(self, url, base_query, pt):
        _, m_headers, m_query = merge_passthrough({}, {}, pt, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _tomtom_http_error(resp.status, resp.headers, resp.body, False)
        raw = decode_json(resp.body)
        if raw is None:
            raise unknown_error(resp.status, None, "TomTom returned a non-JSON/unparseable body")
        return raw

    def place_details(self, opts: PlaceDetailsOptions) -> PlaceDetailsResult:
        """Resolve a TomTom result id to a full candidate.

        ``GET https://api.tomtom.com/search/2/place.json?entityId=`` — a plain lookup,
        no per-session billing concept.
        """
        base_query = {"key": self.cfg.api_key, "entityId": opts.place_id}
        if opts.language:
            base_query["language"] = opts.language

        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        resp = self.send_get(_PLACE_BY_ID_URL, m_headers, m_query)
        raw = decode_json(resp.body)
        if not ok_status(resp.status):
            raise _tomtom_http_error(resp.status, resp.headers, resp.body, True)

        results = jlist(jget(raw, "results")) or []
        first = results[0] if results else None
        candidate = _normalize_tomtom_candidate(first) if first is not None else None
        if candidate is None:
            raise classified_error(
                ProviderCode.NO_ROUTE, resp.status, raw, "TomTom Place Details returned no result"
            )

        name = None
        if opts.includes(PlaceDetailsInclude.NAME):
            name = jstr(jget(jget(first, "poi"), "name")) or None

        return PlaceDetailsResult(candidate=candidate, name=name, raw=raw)


class TomTomIsochroneConnector(BaseConnector):
    def __init__(self, config: TomTomConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def isochrone(self, opts: IsochroneOptions) -> IsochroneResult:
        from .isochrone_validate import validate_isochrone_cap

        validate_isochrone_cap(opts.values)
        assert_finite(opts.center, "TomTom isochrone center")
        base_url = f"{_REACHABLE_RANGE_URL}/{to_lat_lng_string(opts.center)}/json"
        travel_mode = _tomtom_travel_mode(opts.travel_mode)

        contours: List[IsochroneContour] = []
        raws: List[Any] = []
        for value in opts.values:
            base_query = {"key": self.cfg.api_key, "travelMode": travel_mode}
            if opts.type == IsochroneType.TIME:
                base_query["timeBudgetInSec"] = fmt_coord(value)
            else:
                base_query["distanceBudgetInMeters"] = fmt_coord(value)
            if opts.departure_time:
                base_query["departAt"] = opts.departure_time
            _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
            resp = self.send_get(base_url, m_headers, m_query)
            if not ok_status(resp.status):
                raise _tomtom_http_error(resp.status, resp.headers, resp.body, False)
            raw = decode_json(resp.body)
            raws.append(raw)
            boundary = jget(jget(raw, "reachableRange"), "boundary")
            if not isinstance(boundary, list):
                raise unknown_error(resp.status, raw, "TomTom Isochrone returned an unparseable or malformed body")
            ring = [[jnum(jget(p, "longitude")), jnum(jget(p, "latitude"))] for p in boundary]
            if ring:
                ring.append([ring[0][0], ring[0][1]])
            contours.append(IsochroneContour(value=value, geometry=Polygon(type="Polygon", coordinates=[ring])))
        contours.sort(key=lambda c: c.value)
        meta = IsochroneMeta(request_count=len(opts.values)) if len(opts.values) > 1 else None
        return IsochroneResult(contours=contours, raw=raws, meta=meta)


# ---- shared tomtom helpers ----

def _tomtom_travel_mode(m: TravelMode) -> str:
    if m == TravelMode.WALKING:
        return "pedestrian"
    if m == TravelMode.CYCLING:
        return "bicycle"
    return "car"


def _tomtom_matrix_travel_mode(m: TravelMode) -> str:
    if m == TravelMode.WALKING:
        return "pedestrian"
    if m == TravelMode.CYCLING:
        msg = "TomTom Matrix v2 does not support cycling"
        raise ConnectorError(ProviderCode.UNSUPPORTED_TRAVEL_MODE, message=msg, provider_message=msg)
    return "car"


def _tomtom_avoids(opts: RoutingOptions) -> str:
    a = []
    if opts.avoid_tolls:
        a.append("tollRoads")
    if opts.avoid_ferries:
        a.append("ferries")
    if opts.avoid_highways:
        a.append("motorways")
    return ",".join(a)


def _tomtom_normalize_cells(status: int, data: bytes, no: int, nd: int) -> MatrixResult:
    raw = decode_json(data)
    if not isinstance(raw, dict):
        raise unknown_error(status, raw, "TomTom Matrix returned non-JSON body")
    cells: List[MatrixCell] = []
    for entry in raw.get("data") or []:
        rs = entry.get("routeSummary")
        if isinstance(rs, dict):
            cells.append(MatrixCell(int(jnum(entry.get("originIndex"))), int(jnum(entry.get("destinationIndex"))), jnum(rs.get("lengthInMeters")), jnum(rs.get("travelTimeInSeconds"))))
    # A sparse result (fewer routable cells than the requested grid) is normal:
    # an unroutable origin×destination pair is OMITTED, not an error for the whole
    # matrix — parity with the Mapbox/OSRM/HERE/Google cell-omission semantics.
    return MatrixResult(cells=cells, raw=raw)


def _tomtom_map_vendor_error(status: int, with_404: bool) -> ProviderCode:
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if status == 400:
        return ProviderCode.INVALID_REQUEST
    if status == 404 and with_404:
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _tomtom_classify_error(status: int, raw: Any, with_404: bool) -> ProviderCode:
    """Extend the status mapping with TomTom's machine-readable
    ``detailedError.code``, which is the only way to tell "no route" from
    "malformed request" — both arrive as HTTP 400.

    Live-verified: ``MAP_MATCHING_FAILURE`` on a 400. ``NO_ROUTE_FOUND`` is
    TomTom's documented sibling code for the same outcome; it is mapped too, but
    note it is doc-sourced rather than reproduced live — every live attempt at a
    truly unreachable pair returned a route, because TomTom (like every other
    provider tested) routes via ferries.
    """
    # Proxy/auth statuses win: they carry no routing envelope.
    if status in (401, 403, 429) or 500 <= status < 600:
        return _tomtom_map_vendor_error(status, with_404)
    if jstr(jget(jget(raw, "detailedError"), "code")) in ("MAP_MATCHING_FAILURE", "NO_ROUTE_FOUND"):
        return ProviderCode.NO_ROUTE
    return _tomtom_map_vendor_error(status, with_404)


def _tomtom_error_message(body: Any) -> str:
    m = jstr(jget(jget(body, "detailedError"), "message"))
    if m:
        return m
    e = jget(body, "error")
    d = jstr(jget(e, "description"))
    if d:
        return d
    em = jstr(jget(e, "message"))
    if em:
        return em
    es = jstr(jget(body, "error"))
    if es:
        return es
    m2 = jstr(jget(body, "message"))
    if m2:
        return m2
    return jstr(jget(body, "errorText"))


def _tomtom_http_error(status: int, headers, data: bytes, with_404: bool) -> ConnectorError:
    raw = decode_json(data)
    return provider_error(status, headers, raw, _tomtom_classify_error(status, raw, with_404), _tomtom_error_message(raw))


def _normalize_tomtom_candidate(r: Any) -> Optional[GeocodeCandidate]:
    """Map a TomTom search result onto a GeocodeCandidate.

    Returns ``None`` when the result has no real position — the caller skips it
    rather than emitting a fabricated (0,0) "Null Island" candidate.
    """
    pos = jget(r, "position") or {}
    lat, lon = jnum_opt(pos.get("lat")), jnum_opt(pos.get("lon"))
    if lat is None or lon is None:
        return None
    viewport = None
    vp = jget(r, "viewport")
    if isinstance(vp, dict):
        tl, br = jget(vp, "topLeftPoint"), jget(vp, "btmRightPoint")
        if isinstance(tl, dict) and isinstance(br, dict):
            viewport = Viewport(LatLng(jnum(br.get("lat")), jnum(tl.get("lon"))), LatLng(jnum(tl.get("lat")), jnum(br.get("lon"))))
    return GeocodeCandidate(formatted_address=jstr(jget(jget(r, "address"), "freeformAddress")), location=LatLng(lat, lon), place_id=jstr(jget(r, "id")) or None, viewport=viewport)
