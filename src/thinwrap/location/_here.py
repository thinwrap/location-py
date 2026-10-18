"""HERE connectors: routing (v8 + findsequence2), matrix (always-async v8),
geocoding (v7 + autosuggest), isochrone (isolines v8)."""

from __future__ import annotations

import gzip
import time
from typing import Any, Callable, List, Mapping, Optional, Tuple
from urllib.parse import urlencode, urlparse

from ._contour_geometry import contour_geometry
from ._jsonpath import jget, jlist, jnum, jnum_opt, jstr
from ._route_completeness import assert_route_has_legs
from ._util import decode_json, iso_seconds_string, iso_string, ok_status, traffic_requested
from ._waypoint_order import is_complete_waypoint_order
from .base import BaseConnector
from .config import HereConfig
from .coordinate import assert_finite, assert_finite_number, fmt_coord, to_lat_lng_string
from .defaults import HERE_MATRIX_ATTRIBUTES, HERE_ROUTING_RETURN
from .enums import HereTransportMode, PlaceDetailsInclude, RoutingInclude, TravelMode
from .errors import (
    ConnectorError,
    ProviderCode,
    classified_error,
    invalid_request,
    provider_error,
    require_list,
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
from .isochrone import IsochroneContour, IsochroneOptions, IsochroneResult
from .latlng import LatLng
from .matrix import MatrixCell, MatrixOptions, MatrixResult
from .passthrough import Passthrough, merge_passthrough
from .poll import POLL_BACKOFF, POLL_INITIAL_DELAY, POLL_MAX_DELAY, extract_timeout_ms
from .polyline import decode_flex_polyline, encode_polyline
from .routing import RoutingLeg, RoutingOptions, RoutingResult

_ROUTER_URL = "https://router.hereapi.com/v8/routes"
_SEQUENCE_URL = "https://wps.hereapi.com/v8/findsequence2"
_MATRIX_URL = "https://matrix.router.hereapi.com/v8/matrix"
_MATRIX_HOST = "matrix.router.hereapi.com"
_GEOCODE_URL = "https://geocode.search.hereapi.com/v1/geocode"
_REVGEOCODE_URL = "https://revgeocode.search.hereapi.com/v1/revgeocode"
_AUTOSUGGEST_URL = "https://autosuggest.search.hereapi.com/v1/autosuggest"
_LOOKUP_URL = "https://lookup.search.hereapi.com/v1/lookup"
_ISOLINE_URL = "https://isoline.router.hereapi.com/v8/isolines"


class HereRoutingConnector(BaseConnector):
    def __init__(self, config: HereConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def route(self, opts: RoutingOptions) -> RoutingResult:
        wps = list(opts.waypoints)
        if len(wps) < 2:
            raise invalid_request("HERE Routing requires at least two waypoints")
        for wp in wps:
            assert_finite(wp, "HERE Routing")
        # HERE findsequence2 optimizes an OPEN route (fixed first/last waypoint); it
        # cannot return a closed round trip. Surface the unsupported flag instead of
        # silently returning an open route.
        if opts.is_round_trip:
            raise ConnectorError(
                ProviderCode.UNSUPPORTED_OPTION,
                message="HERE route optimization does not support round trips (isRoundTrip)",
                provider_message="HERE findsequence2 optimizes an open route (fixed first/last waypoint) and cannot return a closed round trip; remove isRoundTrip or use a provider that supports it (e.g. Mapbox/OSRM).",
            )
        use_opt = opts.optimize or opts.optimize_fixed_origin or opts.optimize_fixed_destination

        ordered = wps
        waypoint_order = None
        if use_opt and len(wps) > 2:
            sequence, sequence_status = self._find_sequence(opts)
            # The returned sequence must be a complete permutation of
            # [0..N-1] before it can reorder the waypoints; otherwise a waypoint
            # would be dropped or duplicated in the follow-up /routes request.
            # HERE is the one connector that raises rather than omitting the
            # ordering, because the sequence also drives that request.
            if not is_complete_waypoint_order(sequence, len(wps)):
                raise ConnectorError(ProviderCode.UNKNOWN, status_code=sequence_status, message="HERE findsequence2 returned an invalid waypoint ordering", provider_message="HERE findsequence2 returned an invalid waypoint ordering", cause={"sequence": sequence})
            ordered = [wps[i] for i in sequence]
            waypoint_order = sequence

        result = self._call_routes(ordered, opts)
        return RoutingResult(
            legs=result.legs,
            total_distance_meters=result.total_distance_meters,
            total_duration_seconds=result.total_duration_seconds,
            polyline=result.polyline,
            waypoint_order=waypoint_order,
            raw=result.raw,
            total_duration_without_traffic_seconds=result.total_duration_without_traffic_seconds,
        )

    def _call_routes(self, waypoints: List[LatLng], opts: RoutingOptions) -> RoutingResult:
        first, last = waypoints[0], waypoints[-1]
        intermediates = waypoints[1:-1]
        base_query = {
            "apiKey": self.cfg.api_key,
            "transportMode": _here_transport_mode(opts.travel_mode, opts.transport_mode),
            "return": ",".join(HERE_ROUTING_RETURN),
            "routingMode": "fast",
            "origin": to_lat_lng_string(first),
            "destination": to_lat_lng_string(last),
        }
        # HERE applies live traffic whenever departureTime is omitted (it assumes
        # "now"), and bills such requests as Time-Aware Routing rather than basic
        # Routing unless the request carries departureTime=any or
        # traffic[mode]=disabled. traffic_mode NONE must therefore say so
        # explicitly. `any` still honours long-term closures; `disabled` is the
        # only switch that keeps a caller-supplied time, at the cost of ignoring
        # those closures too. A time supplied through passthrough counts as well:
        # HERE rejects `any` combined with arrivalTime (400), and a passthrough
        # departureTime would replace `any` and silently restore Time-Aware
        # billing. Set before the passthrough merge so a caller can still
        # override either key.
        traffic = traffic_requested(opts.traffic_mode)
        pt_query = (opts.passthrough.query if opts.passthrough else None) or {}
        # A passthrough departureTime=any is the caller asking for `any` itself,
        # so it keeps the closure-preserving default.
        carries_time = (
            bool(opts.departure_time)
            or pt_query.get("departureTime", "any") != "any"
            or "arrivalTime" in pt_query
        )
        if opts.departure_time:
            base_query["departureTime"] = iso_string(opts.departure_time)
        if not traffic:
            if carries_time:
                base_query["traffic[mode]"] = "disabled"
            else:
                base_query["departureTime"] = "any"
        af = _here_avoid_features(opts)
        if af:
            base_query["avoid[features]"] = af
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        from urllib.parse import urlencode

        pairs = list(m_query.items()) + [("via", to_lat_lng_string(wp)) for wp in intermediates]
        full_url = _ROUTER_URL + "?" + urlencode(pairs)
        resp = self.send_get(full_url, m_headers, None)
        data = resp.body
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, data)
        raw = decode_json(data)
        routes = raw.get("routes") if isinstance(raw, dict) else None
        if not routes:
            # Live-verified: HERE answers an unroutable request with HTTP 200,
            # routes: [], and a critical entry in notices[] (e.g.
            # couldNotMatchOrigin). The notice is the only statement of *why*, so
            # surface it rather than leaving the caller to dig through cause.
            notice = _here_notice(raw)
            msg = "HERE Routing returned no routes"
            if notice:
                msg = f"{msg}: {notice}"
            raise classified_error(ProviderCode.NO_ROUTE, resp.status, raw, msg)
        route = routes[0] if isinstance(routes, list) else None
        sections = jget(route, "sections")
        if not isinstance(route, dict) or not all(isinstance(s, dict) for s in (jlist(sections) or [])):
            raise unknown_error(resp.status, raw, "HERE Routing returned a malformed route")

        # HERE ships baseDuration inside the summary block already requested, so the
        # opt-in costs nothing extra here — it only gates whether the field is
        # surfaced, keeping the normalized shape identical across providers.
        wants_base = opts.includes(RoutingInclude.DURATION_WITHOUT_TRAFFIC)

        all_coords: List[LatLng] = []
        section_legs: List[RoutingLeg] = []
        total_dist = total_dur = 0.0
        sections = jlist(sections) or []
        for section in sections:
            poly = jstr(section.get("polyline"))
            if poly:
                all_coords.extend(decode_flex_polyline(poly))
            summ = section.get("summary")
            length, duration = jnum(jget(summ, "length")), jnum(jget(summ, "duration"))
            base = jget(summ, "baseDuration")
            section_legs.append(
                RoutingLeg(
                    length,
                    duration,
                    duration_without_traffic_seconds=(
                        float(base)
                        if wants_base and isinstance(base, (int, float)) and not isinstance(base, bool)
                        else None
                    ),
                )
            )
            total_dist += length
            total_dur += duration

        legs = _here_group_sections_into_legs(sections, section_legs, len(waypoints))
        assert_route_has_legs(len(legs), len(waypoints), "HERE Routing", raw, resp.status)

        # Summed from the sections HERE returned, matching how the traffic-aware
        # totals are derived — and only when EVERY section carried the value, so a
        # partial response omits the field rather than under-reporting it.
        total_base = None
        if wants_base and section_legs and all(l.duration_without_traffic_seconds is not None for l in section_legs):
            total_base = sum(l.duration_without_traffic_seconds or 0.0 for l in section_legs)

        return RoutingResult(
            legs=legs,
            total_distance_meters=total_dist,
            total_duration_seconds=total_dur,
            polyline=encode_polyline(all_coords),
            raw=raw,
            total_duration_without_traffic_seconds=total_base,
        )

    def _find_sequence(self, opts: RoutingOptions) -> Tuple[List[int], int]:
        wps = list(opts.waypoints)
        first, last = wps[0], wps[-1]
        intermediates = wps[1:-1]
        tm = _here_transport_mode(opts.travel_mode, opts.transport_mode)
        query = {
            "apiKey": self.cfg.api_key,
            "start": to_lat_lng_string(first),
            "end": to_lat_lng_string(last),
            # The avoid features ride in `mode` so the optimizer orders the stops
            # under the same constraints the follow-up /routes call honours —
            # otherwise the ordering and the route can disagree. Traffic is kept
            # separate because enabling it on findsequence2 is BILLABLE, so it
            # follows the explicit traffic_mode opt-in.
            "mode": _here_sequence_mode(tm, opts),
        }
        if opts.departure_time:
            # findsequence2 documents the departure-time param as `departure`
            # (ISO 8601); it ignores an unrecognized `departureTime` without an
            # error. Seconds precision — the endpoint 400s on a fractional value.
            query["departure"] = iso_seconds_string(opts.departure_time)
        for i, wp in enumerate(intermediates):
            query[f"destination{i + 1}"] = to_lat_lng_string(wp)

        # `_passthrough` (query + headers) applies to this leg too, so a consumer
        # can tune the sequence request as well as the route.
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, query)
        resp = self.send_get(_SEQUENCE_URL, m_headers, m_query)
        data = resp.body
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, data)
        raw = decode_json(data)
        results = jlist(jget(raw, "results"))
        entries = jget(results[0], "waypoints") if results else None
        if not isinstance(entries, list) or not all(isinstance(w, dict) for w in entries):
            # The legacy WPS endpoint also reports a rejected request as HTTP 200
            # with {"results": None, "errors": [...], "responseCode": "400"}, so
            # the reason arrives here rather than through _here_http_error.
            reason = _here_error_message(raw)
            message = (
                f"HERE findsequence2 returned no sequence: {reason}"
                if reason
                else "HERE findsequence2 returned no sequence"
            )
            raise unknown_error(resp.status, raw, message)
        entries = sorted(entries, key=lambda w: jnum(w.get("sequence")))
        last_index = len(wps) - 1
        absolute: List[int] = []
        for e in entries:
            wid = jstr(e.get("id"))
            if wid == "start":
                absolute.append(0)
            elif wid == "end":
                absolute.append(last_index)
            elif wid.startswith("destination"):
                try:
                    n = int(wid[len("destination"):])
                except ValueError:
                    continue
                if 1 <= n < last_index:
                    absolute.append(n)
        return absolute, resp.status


class HereMatrixConnector(BaseConnector):
    def __init__(self, config: HereConfig, transport=None, sleep: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(transport)
        self.cfg = config
        self._sleep = sleep or time.sleep

    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        if not opts.origins or not opts.destinations:
            raise invalid_request("HERE Matrix requires at least one origin and one destination")
        deadline, clean_pt = extract_timeout_ms(opts.passthrough)
        matrix_id, status_url = self._submit(opts, clean_pt)
        result_url = self._poll(matrix_id, status_url, deadline)
        return self._retrieve(result_url, opts)

    def _submit(self, opts: MatrixOptions, pt: Optional[Passthrough]):
        for o in opts.origins:
            assert_finite(o, "HERE matrix origin")
        for d in opts.destinations:
            assert_finite(d, "HERE matrix destination")
        body: dict[str, Any] = {
            "origins": [{"lat": o.lat, "lng": o.lng} for o in opts.origins],
            "destinations": [{"lat": d.lat, "lng": d.lng} for d in opts.destinations],
            "regionDefinition": {"type": "autoCircle"},
            "matrixAttributes": list(HERE_MATRIX_ATTRIBUTES),
        }
        profile = _here_transport_mode(opts.travel_mode, opts.transport_mode)
        if profile != "car":
            body["transportMode"] = profile
        if opts.avoid_tolls:
            body["avoid"] = {"features": ["tollRoad"]}
        # Matrix v8 is a single SKU, but like /v8/routes it computes against live
        # traffic whenever departureTime is omitted, which contradicts the
        # traffic_mode NONE default. Same switches as routing (see
        # HereRoutingConnector._call_routes), in body form, including the
        # passthrough time fields (matrix also 400s on `any` + arrivalTime).
        traffic = traffic_requested(opts.traffic_mode)
        pt_body = (pt.body if pt else None) or {}
        carries_time = (
            bool(opts.departure_time)
            or pt_body.get("departureTime", "any") != "any"
            or "arrivalTime" in pt_body
        )
        if opts.departure_time:
            body["departureTime"] = iso_string(opts.departure_time)
        if not traffic:
            if carries_time:
                body["traffic"] = {"mode": "disabled"}
            else:
                body["departureTime"] = "any"
        base_query = {"apiKey": self.cfg.api_key, "async": "true"}
        m_body, m_headers, m_query = merge_passthrough(body, {}, pt, base_query)
        resp = self.send_post_json(_MATRIX_URL, m_body, m_headers, m_query)
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, resp.body)
        st = decode_json(resp.body)
        matrix_id, status_url = jstr(jget(st, "matrixId")), jstr(jget(st, "statusUrl"))
        if not matrix_id or not status_url:
            raise unknown_error(resp.status, st, "HERE Matrix submit response missing matrixId or statusUrl")
        return matrix_id, status_url

    def _poll(self, matrix_id: str, status_url: str, deadline: float) -> str:
        _assert_here_api_url(status_url, "statusUrl")
        deadline_at = time.monotonic() + deadline
        delay = POLL_INITIAL_DELAY
        while True:
            now = time.monotonic()
            if now >= deadline_at:
                break
            self._sleep(min(delay, deadline_at - now))
            delay = min(POLL_MAX_DELAY, delay * POLL_BACKOFF)
            resp = self.send_get(status_url, None, {"apiKey": self.cfg.api_key})
            # Real HERE Matrix v8: on completion the poll returns 303 See Other
            # with Location: <resultUrl> and a body {matrixId, status:"completed",
            # resultUrl}. This MUST be handled BEFORE the generic non-2xx guard
            # below — a 303 is not ok_status and would otherwise raise. The
            # default urllib transport surfaces the 303 as a response (redirects
            # disabled) rather than following it.
            if resp.status == 303:
                return _require_result_url(resp, None)
            if not ok_status(resp.status):
                raise _here_http_error(resp.status, resp.headers, resp.body)
            st = decode_json(resp.body)
            state = jstr(jget(st, "status")) or jstr(jget(st, "state"))
            # Belt-and-braces alongside the 303 path: a 200 body carrying
            # status "completed" is also treated as completion.
            if state == "completed":
                return _require_result_url(resp, st)
            if state == "failed":
                raise ConnectorError(ProviderCode.PROVIDER_UNAVAILABLE, status_code=resp.status, provider_message="HERE Matrix job failed", cause=st)
        raise ConnectorError(ProviderCode.MATRIX_POLLING_TIMEOUT, message="HERE Matrix polling deadline exceeded", provider_message=f"matrixId: {matrix_id}", cause={"matrixId": matrix_id, "statusUrl": status_url})

    def _retrieve(self, result_url: str, opts: MatrixOptions) -> MatrixResult:
        _assert_here_api_url(result_url, "resultUrl")
        # Step 3a: GET the validated hereapi.com resultUrl WITH the apiKey (401
        # without) and request header Accept-Encoding: gzip (406 Not Acceptable
        # without). HERE does NOT return the payload inline — it responds 303 See
        # Other with Location: <pre-signed S3 URL>. The default urllib transport
        # does not follow redirects, so the 303 is observable here and we follow
        # the single hop MANUALLY (below) — the apiKey is never forwarded off the
        # HERE host to the storage backend.
        resp = self.send_get(result_url, {"Accept-Encoding": "gzip"}, {"apiKey": self.cfg.api_key})
        # Step 3b: follow the single redirect hop to the pre-signed URL WITHOUT
        # the apiKey — the signed URL is self-authenticating (query-signed) and
        # lives on a non-HERE host, so it is intentionally NOT run through
        # _assert_here_api_url and never receives the key. A direct 200 (no
        # redirect — the shape the public docs describe) skips this hop.
        if 300 <= resp.status < 400:
            location = _here_header(resp.headers, "location")
            if not location:
                raise unknown_error(resp.status, None, "HERE Matrix retrieve redirect missing Location header")
            # The redirect target is a non-HERE (pre-signed storage) host so it
            # isn't run through _assert_here_api_url, but it MUST still be https —
            # refuse a plaintext/other-scheme downgrade.
            if urlparse(location).scheme.lower() != "https":
                raise unknown_error(resp.status, None, "HERE Matrix result redirect must be an https URL")
            resp = self.send_get(location, {"Accept-Encoding": "gzip"}, None)
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(_gunzip_if_needed(resp.headers, resp.body))
        matrix = jget(raw, "matrix")
        if not isinstance(matrix, dict):
            raise unknown_error(resp.status, raw, "HERE Matrix retrieve missing matrix payload")
        no, nd = len(opts.origins), len(opts.destinations)
        stride = int(jnum(matrix.get("numDestinations"))) if jnum(matrix.get("numDestinations")) > 0 else nd
        travel_times = matrix.get("travelTimes")
        distances = matrix.get("distances")
        # Per-cell status parallel to travelTimes/distances (0 = OK, 3 = usable
        # despite a violated constraint); any other non-zero code marks that
        # cell's value as unspecified.
        error_codes = jlist(matrix.get("errorCodes")) or []
        expected = (no - 1) * stride + nd if no > 0 and nd > 0 else 0
        if not isinstance(travel_times, list) or not isinstance(distances, list) or len(travel_times) < expected or len(distances) < expected:
            raise unknown_error(resp.status, raw, "HERE Matrix returned arrays too short for the requested dimensions")

        # travelTimes -> duration_seconds, distances -> distance_meters. Omit
        # cells HERE flagged as failed (errorCode not 0/3) — their value is
        # unspecified — and any entry that is not a number, rather than read it
        # as 0. Contract: failed entries are omitted from cells.
        def _cell_ok(idx: int) -> bool:
            if idx < len(error_codes):
                ec = error_codes[idx]
                return ec in (0, 3)
            return True

        cells = []
        for oi in range(no):
            for di in range(nd):
                idx = oi * stride + di
                distance, duration = jnum_opt(distances[idx]), jnum_opt(travel_times[idx])
                if _cell_ok(idx) and distance is not None and duration is not None:
                    cells.append(MatrixCell(oi, di, distance, duration))
        return MatrixResult(cells=cells, raw=raw)


def _here_country_code_filter(country_filter: Optional[list[str]]) -> Optional[str]:
    """Turn a ``country_filter`` (alpha-2) into HERE's ``in=countryCode:<alpha-3 CSV>``.

    Shared by ``geocode()`` and ``autocomplete()`` so the two cannot drift on which
    codes they accept. Returns None when there is nothing to filter on, which keeps
    "no country filter" distinguishable from "an empty one".
    """
    if not country_filter:
        return None
    alpha3 = []
    for code in country_filter:
        # Skip empty/whitespace-only entries rather than emitting a confusing
        # "mapping unavailable for " error.
        if not code.strip():
            continue
        mapped = _ISO_ALPHA2_TO_ALPHA3.get(code.upper())
        if mapped is None:
            raise invalid_request(f"HERE country code mapping unavailable for {code}; please use _passthrough.query.in to pass HERE's alpha-3 directly.")
        alpha3.append(mapped)
    return "countryCode:" + ",".join(alpha3) if alpha3 else None


class HereGeocodingConnector(BaseConnector):
    def __init__(self, config: HereConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def geocode(self, opts: GeocodeOptions) -> GeocodeResult:
        base_query = {"q": opts.address, "apiKey": self.cfg.api_key}
        if opts.language:
            base_query["lang"] = opts.language
        country_code_filter = _here_country_code_filter(opts.country_filter)
        if country_code_filter is not None:
            base_query["in"] = country_code_filter
        raw = self._get(_GEOCODE_URL, base_query, opts.passthrough, "HERE Geocoding")
        return GeocodeResult(candidates=_normalize_here_items(raw), raw=raw)

    def reverse_geocode(self, opts: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        assert_finite(opts.location, "HERE reverseGeocode")
        base_query = {"at": to_lat_lng_string(opts.location), "apiKey": self.cfg.api_key}
        if opts.language:
            base_query["lang"] = opts.language
        raw = self._get(_REVGEOCODE_URL, base_query, opts.passthrough, "HERE Reverse Geocoding")
        return ReverseGeocodeResult(candidates=_normalize_here_items(raw), raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        base_query = {"q": opts.input, "apiKey": self.cfg.api_key, "limit": "10"}
        if opts.language:
            base_query["lang"] = opts.language
        if opts.location is not None:
            assert_finite(opts.location, "HERE autocomplete location")
            if opts.radius is not None:
                assert_finite_number(opts.radius, "HERE autocomplete radius")
                base_query["in"] = f"circle:{to_lat_lng_string(opts.location)};r={fmt_coord(opts.radius)}"
            else:
                base_query["at"] = to_lat_lng_string(opts.location)
        country_code_filter = _here_country_code_filter(opts.country_filter)

        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)

        # Autosuggest is the one HERE endpoint that mandates a search context:
        # exactly one of `at`, `in=circle` or `in=bbox`, and a country filter has to
        # accompany one of them. Without it HERE rejects the request, so fail here
        # with something actionable instead of relaying a vendor 400. Checked after
        # the merge so a consumer supplying their own `in=bbox:` still satisfies it.
        if not m_query.get("at") and not m_query.get("in"):
            raise invalid_request(
                "HERE Autosuggest requires a search context: pass `location` (optionally with `radius`), "
                "or supply one via _passthrough.query.at / _passthrough.query.in."
            )

        # The country filter rides ALONGSIDE the spatial context rather than
        # replacing it, and HERE spells both as `in`. A list of (key, value) pairs
        # keeps the repeated key, the same way repeated `via` works on routing.
        pairs = list(m_query.items())
        if country_code_filter is not None:
            pairs.append(("in", country_code_filter))
        resp = self.send_get(_AUTOSUGGEST_URL + "?" + urlencode(pairs), m_headers, None)
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        preds = []
        for it in require_list(resp.status, raw, "items", "HERE Autosuggest"):
            title = jstr(jget(it, "title"))
            # HERE's *query*-type suggestions carry a title but no address at all, so
            # `secondary_text` stays None rather than an empty string.
            structured = None
            if title:
                structured = AutocompleteStructuredFormat(
                    main_text=title,
                    secondary_text=jstr(jget(jget(it, "address"), "label")) or None,
                )
            preds.append(
                AutocompletePrediction(
                    description=title,
                    place_id=jstr(jget(it, "id")) or None,
                    structured_format=structured,
                )
            )
        return AutocompleteResult(predictions=preds, raw=raw)

    def _get(self, url, base_query, pt, label: str):
        _, m_headers, m_query = merge_passthrough({}, {}, pt, base_query)
        resp = self.send_get(url, m_headers, m_query)
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        require_list(resp.status, raw, "items", label)
        return raw

    def place_details(self, opts: PlaceDetailsOptions) -> PlaceDetailsResult:
        """Resolve a HERE place id to a full candidate.

        ``GET https://lookup.search.hereapi.com/v1/lookup?id=`` — HERE returns one
        ``items[]``-shaped entry, so the geocode normalizer applies unchanged.
        """
        base_query = {"apiKey": self.cfg.api_key, "id": opts.place_id}
        if opts.language:
            base_query["lang"] = opts.language

        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        resp = self.send_get(_LOOKUP_URL, m_headers, m_query)
        raw = decode_json(resp.body)
        if resp.status == 404:
            # Lookup answers an id it does not know with 404; that is "no such
            # place", not a failed request.
            return PlaceDetailsResult(candidate=None, raw=raw)
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, resp.body)
        if not isinstance(raw, dict):
            raise unknown_error(resp.status, raw, "HERE Place Details returned a malformed response body")

        candidates = _normalize_here_items({"items": [raw]})
        if not candidates:
            return PlaceDetailsResult(candidate=None, raw=raw)

        # HERE's `title` IS the display name; free, but still gated so the shape
        # matches every other provider.
        name = None
        if opts.includes(PlaceDetailsInclude.NAME):
            name = jstr(jget(raw, "title")) or None

        return PlaceDetailsResult(candidate=candidates[0], name=name, raw=raw)


class HereIsochroneConnector(BaseConnector):
    def __init__(self, config: HereConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def isochrone(self, opts: IsochroneOptions) -> IsochroneResult:
        from .isochrone_validate import isochrone_departure, validate_isochrone_cap

        validate_isochrone_cap(opts.values)
        assert_finite(opts.center, "HERE isochrone center")
        if opts.travel_mode == TravelMode.CYCLING:
            raise ConnectorError(ProviderCode.UNSUPPORTED_TRAVEL_MODE, message="HERE isochrone does not support cycling", provider_message="HERE isochrone does not support cycling")
        transport_mode = "pedestrian" if opts.travel_mode == TravelMode.WALKING else "car"
        base_query = {
            "apiKey": self.cfg.api_key,
            "origin": to_lat_lng_string(opts.center),
            "range[type]": str(opts.type.value if hasattr(opts.type, "value") else opts.type),
            "range[values]": ",".join(fmt_coord(v) for v in opts.values),
            "transportMode": transport_mode,
        }
        if opts.departure_time is not None:
            base_query["departureTime"] = isochrone_departure(opts.departure_time, "HERE Isochrone")
        _, m_headers, m_query = merge_passthrough({}, {}, opts.passthrough, base_query)
        resp = self.send_get(_ISOLINE_URL, m_headers, m_query)
        if not ok_status(resp.status):
            raise _here_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        contours: List[IsochroneContour] = []
        for iso in require_list(resp.status, raw, "isolines", "HERE Isochrone"):
            # `polygons` is a MultiPolygon: an isoline can split into several
            # parts (islands, areas joined only by a ferry `connection`), each
            # with its own `inner` holes. Every part is kept.
            parts = [p for p in (_here_isoline_part(poly) for poly in jlist(jget(iso, "polygons")) or []) if p]
            value = jnum_opt(jget(jget(iso, "range"), "value"))
            if value is None or not parts:
                raise unknown_error(resp.status, raw, "HERE Isochrone returned an isoline without a value or outer polygon")
            contours.append(IsochroneContour(value=value, geometry=contour_geometry(parts)))
        if not contours:
            raise unknown_error(resp.status, raw, "HERE Isochrone returned no isolines")
        contours.sort(key=lambda c: c.value)
        return IsochroneResult(contours=contours, raw=raw)


# ---- shared here helpers ----

def _here_isoline_part(poly: Any) -> List[List[List[float]]]:
    """One isoline polygon as ``[outer, *inner]`` [lng, lat] rings; empty when it
    has no decodable outer ring."""
    outer = jstr(jget(poly, "outer"))
    ring = [[p.lng, p.lat] for p in decode_flex_polyline(outer)] if outer else []
    if not ring:
        return []
    rings = [ring]
    for inner in jlist(jget(poly, "inner")) or []:
        encoded = jstr(inner)
        hole = [[p.lng, p.lat] for p in decode_flex_polyline(encoded)] if encoded else []
        if hole:
            rings.append(hole)
    return rings


def _here_header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup. The Transport contract lower-cases keys,
    but stay defensive for BYO transports that don't."""
    name = name.lower()
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return ""


def _require_result_url(resp, body: Any) -> str:
    """Resolve the async resultUrl from a completed HERE poll response: the
    body's ``resultUrl`` (preferred; present in both the 303 body and any 200
    completed body) or the ``Location`` response header (set on the 303)."""
    if body is None:
        body = decode_json(resp.body)
    result_url = jstr(jget(body, "resultUrl")) or _here_header(resp.headers, "location")
    if not result_url:
        raise unknown_error(resp.status, body, "HERE Matrix poll completed without resultUrl")
    return result_url


def _gunzip_if_needed(headers: Mapping[str, str], body: bytes) -> bytes:
    """Decompress the retrieve body when gzip-encoded. HERE serves the matrix
    result gzip-only, and because the connector sets ``Accept-Encoding: gzip``
    itself the default urllib transport does NOT auto-decompress — the body
    arrives as raw gzip bytes (Content-Encoding: gzip, magic 0x1f 0x8b) that we
    gunzip via the stdlib (zero runtime deps). A BYO transport that already
    decompressed presents plain JSON and is returned untouched; a stray
    Content-Encoding header on an already-decompressed body is tolerated."""
    if not body:
        return body
    encoding = _here_header(headers, "content-encoding").lower()
    looks_gzipped = "gzip" in encoding or (len(body) >= 2 and body[0] == 0x1F and body[1] == 0x8B)
    if not looks_gzipped:
        return body
    try:
        return gzip.decompress(body)
    except (OSError, EOFError):
        return body


def _here_transport_mode(mode: TravelMode, override: Optional[HereTransportMode]) -> str:
    if override is not None:
        return override.value if hasattr(override, "value") else str(override)
    if mode == TravelMode.WALKING:
        return "pedestrian"
    if mode == TravelMode.CYCLING:
        return "bicycle"
    return "car"


def _here_group_sections_into_legs(
    sections: List[Any], section_legs: List[RoutingLeg], waypoint_count: int
) -> List[RoutingLeg]:
    """Fold HERE route sections into one leg per consecutive waypoint pair.

    A HERE section is the stretch between consecutive waypoints OR an implicit mode
    change — a ferry or car shuttle inside a driving route starts its own
    ``TransitSection`` — so sections are not legs. The waypoint boundary is marked
    on the place itself: ``arrival.place.waypoint`` is present when that place is
    the ``via`` with the same index, and absent for the origin, the destination and
    any stop the router added. A leg therefore closes after a section whose arrival
    carries ``waypoint``, and after the final section.

    Values are summed across the grouped sections; ``duration_without_traffic``
    only when every grouped section carried it, so a partial group omits it rather
    than under-reporting.

    When no section carries the marker and there is exactly one section per
    waypoint pair, the sections already are the legs and are kept 1:1.
    """
    closes = [jget(jget(jget(s, "arrival"), "place"), "waypoint") is not None for s in sections]
    if not any(closes) and len(section_legs) == waypoint_count - 1:
        return list(section_legs)

    legs: List[RoutingLeg] = []
    group: List[RoutingLeg] = []
    for i, leg in enumerate(section_legs):
        group.append(leg)
        if closes[i] or i == len(section_legs) - 1:
            bases = [g.duration_without_traffic_seconds for g in group]
            legs.append(
                RoutingLeg(
                    sum(g.distance_meters for g in group),
                    sum(g.duration_seconds for g in group),
                    duration_without_traffic_seconds=(
                        sum(b for b in bases if b is not None) if all(b is not None for b in bases) else None
                    ),
                )
            )
            group = []
    return legs


def _here_avoid_features(opts: RoutingOptions) -> str:
    a = []
    if opts.avoid_tolls:
        a.append("tollRoad")
    if opts.avoid_ferries:
        a.append("ferry")
    if opts.avoid_highways:
        a.append("controlledAccessHighway")
    return ",".join(a)


def _here_map_vendor_error(status: int) -> ProviderCode:
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 429:
        return ProviderCode.RATE_LIMITED
    if status == 400:
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _here_error_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    title, cause = jstr(body.get("title")), jstr(body.get("cause"))
    if title:
        return f"{title}: {cause}" if cause else title
    if cause:
        return cause
    # findsequence2 is the legacy WPS shape — no title/cause, just
    # {"results": None, "errors": ["Bad Format for Date and Time: …"],
    #  "responseCode": "400"}. Without this the one statement of *why* is dropped
    # and the caller sees a bare "failed: 400".
    errors = _here_error_list(body.get("errors"))
    if errors:
        return errors
    em = jstr(jget(body.get("error"), "message"))
    if em:
        return em
    return jstr(body.get("message")) or jstr(body.get("error"))


def _here_error_list(value: Any) -> str:
    """Join the non-empty strings of a WPS ``errors`` array."""
    if not isinstance(value, list):
        return ""
    return "; ".join(s for s in (jstr(item) for item in value) if s)


def _here_http_error(status: int, headers, data: bytes) -> ConnectorError:
    raw = decode_json(data)
    return provider_error(status, headers, raw, _here_map_vendor_error(status), _here_error_message(raw))


def _assert_here_api_url(raw_url: str, label: str) -> None:
    try:
        u = urlparse(raw_url)
    except ValueError:
        u = None
    if u is None or not u.hostname:
        raise ConnectorError(ProviderCode.INVALID_REQUEST, message=f"HERE Matrix {label} is not a valid URL", provider_message=f"HERE Matrix {label} is not a valid URL", cause={"url": raw_url})
    host = u.hostname
    allowed = host == _MATRIX_HOST or host == "hereapi.com" or host.endswith(".hereapi.com")
    if u.scheme != "https" or not allowed:
        raise ConnectorError(ProviderCode.INVALID_REQUEST, message=f"HERE Matrix {label} points to an unexpected host", provider_message=f"HERE Matrix {label} points to an unexpected host", cause={"url": raw_url})


def _normalize_here_items(raw: Any) -> List[GeocodeCandidate]:
    out: List[GeocodeCandidate] = []
    for item in jlist(jget(raw, "items")) or []:
        # `address.label` is the full address; `title` is the result's display name,
        # which for a POI is just its name (live: title "Empire State Building",
        # label "Empire State Building, 20 W 34th St, New York, NY 10118, United
        # States"). The title is only a fallback for an item without a label.
        addr = jstr(jget(jget(item, "address"), "label")) or jstr(jget(item, "title"))
        pos = jget(item, "position")
        # Skip an item without a real position rather than emitting a fabricated
        # (0,0) "Null Island" candidate.
        lat, lng = jnum_opt(jget(pos, "lat")), jnum_opt(jget(pos, "lng"))
        if lat is None or lng is None:
            continue
        viewport = None
        mv = jget(item, "mapView")
        corners = [jnum_opt(jget(mv, k)) for k in ("south", "west", "north", "east")]
        # A partial mapView is dropped whole: a 0-filled edge would stretch the
        # consumer's bounds to the equator or the prime meridian.
        if all(c is not None for c in corners):
            south, west, north, east = corners
            viewport = Viewport(LatLng(south, west), LatLng(north, east))
        out.append(GeocodeCandidate(formatted_address=addr, location=LatLng(lat, lng), place_id=jstr(jget(item, "id")) or None, viewport=viewport))
    return out


_ISO_ALPHA2_TO_ALPHA3 = {
    "AD": "AND", "AE": "ARE", "AF": "AFG", "AG": "ATG", "AI": "AIA", "AL": "ALB", "AM": "ARM",
    "AO": "AGO", "AQ": "ATA", "AR": "ARG", "AS": "ASM", "AT": "AUT", "AU": "AUS", "AW": "ABW",
    "AX": "ALA", "AZ": "AZE", "BA": "BIH", "BB": "BRB", "BD": "BGD", "BE": "BEL", "BF": "BFA",
    "BG": "BGR", "BH": "BHR", "BI": "BDI", "BJ": "BEN", "BL": "BLM", "BM": "BMU", "BN": "BRN",
    "BO": "BOL", "BQ": "BES", "BR": "BRA", "BS": "BHS", "BT": "BTN", "BV": "BVT", "BW": "BWA",
    "BY": "BLR", "BZ": "BLZ", "CA": "CAN", "CC": "CCK", "CD": "COD", "CF": "CAF", "CG": "COG",
    "CH": "CHE", "CI": "CIV", "CK": "COK", "CL": "CHL", "CM": "CMR", "CN": "CHN", "CO": "COL",
    "CR": "CRI", "CU": "CUB", "CV": "CPV", "CW": "CUW", "CX": "CXR", "CY": "CYP", "CZ": "CZE",
    "DE": "DEU", "DJ": "DJI", "DK": "DNK", "DM": "DMA", "DO": "DOM", "DZ": "DZA", "EC": "ECU",
    "EE": "EST", "EG": "EGY", "EH": "ESH", "ER": "ERI", "ES": "ESP", "ET": "ETH", "FI": "FIN",
    "FJ": "FJI", "FK": "FLK", "FM": "FSM", "FO": "FRO", "FR": "FRA", "GA": "GAB", "GB": "GBR",
    "GD": "GRD", "GE": "GEO", "GF": "GUF", "GG": "GGY", "GH": "GHA", "GI": "GIB", "GL": "GRL",
    "GM": "GMB", "GN": "GIN", "GP": "GLP", "GQ": "GNQ", "GR": "GRC", "GS": "SGS", "GT": "GTM",
    "GU": "GUM", "GW": "GNB", "GY": "GUY", "HK": "HKG", "HM": "HMD", "HN": "HND", "HR": "HRV",
    "HT": "HTI", "HU": "HUN", "ID": "IDN", "IE": "IRL", "IL": "ISR", "IM": "IMN", "IN": "IND",
    "IO": "IOT", "IQ": "IRQ", "IR": "IRN", "IS": "ISL", "IT": "ITA", "JE": "JEY", "JM": "JAM",
    "JO": "JOR", "JP": "JPN", "KE": "KEN", "KG": "KGZ", "KH": "KHM", "KI": "KIR", "KM": "COM",
    "KN": "KNA", "KP": "PRK", "KR": "KOR", "KW": "KWT", "KY": "CYM", "KZ": "KAZ", "LA": "LAO",
    "LB": "LBN", "LC": "LCA", "LI": "LIE", "LK": "LKA", "LR": "LBR", "LS": "LSO", "LT": "LTU",
    "LU": "LUX", "LV": "LVA", "LY": "LBY", "MA": "MAR", "MC": "MCO", "MD": "MDA", "ME": "MNE",
    "MF": "MAF", "MG": "MDG", "MH": "MHL", "MK": "MKD", "ML": "MLI", "MM": "MMR", "MN": "MNG",
    "MO": "MAC", "MP": "MNP", "MQ": "MTQ", "MR": "MRT", "MS": "MSR", "MT": "MLT", "MU": "MUS",
    "MV": "MDV", "MW": "MWI", "MX": "MEX", "MY": "MYS", "MZ": "MOZ", "NA": "NAM", "NC": "NCL",
    "NE": "NER", "NF": "NFK", "NG": "NGA", "NI": "NIC", "NL": "NLD", "NO": "NOR", "NP": "NPL",
    "NR": "NRU", "NU": "NIU", "NZ": "NZL", "OM": "OMN", "PA": "PAN", "PE": "PER", "PF": "PYF",
    "PG": "PNG", "PH": "PHL", "PK": "PAK", "PL": "POL", "PM": "SPM", "PN": "PCN", "PR": "PRI",
    "PS": "PSE", "PT": "PRT", "PW": "PLW", "PY": "PRY", "QA": "QAT", "RE": "REU", "RO": "ROU",
    "RS": "SRB", "RU": "RUS", "RW": "RWA", "SA": "SAU", "SB": "SLB", "SC": "SYC", "SD": "SDN",
    "SE": "SWE", "SG": "SGP", "SH": "SHN", "SI": "SVN", "SJ": "SJM", "SK": "SVK", "SL": "SLE",
    "SM": "SMR", "SN": "SEN", "SO": "SOM", "SR": "SUR", "SS": "SSD", "ST": "STP", "SV": "SLV",
    "SX": "SXM", "SY": "SYR", "SZ": "SWZ", "TC": "TCA", "TD": "TCD", "TF": "ATF", "TG": "TGO",
    "TH": "THA", "TJ": "TJK", "TK": "TKL", "TL": "TLS", "TM": "TKM", "TN": "TUN", "TO": "TON",
    "TR": "TUR", "TT": "TTO", "TV": "TUV", "TW": "TWN", "TZ": "TZA", "UA": "UKR", "UG": "UGA",
    "UM": "UMI", "US": "USA", "UY": "URY", "UZ": "UZB", "VA": "VAT", "VC": "VCT", "VE": "VEN",
    "VG": "VGB", "VI": "VIR", "VN": "VNM", "VU": "VUT", "WF": "WLF", "WS": "WSM", "YE": "YEM",
    "YT": "MYT", "ZA": "ZAF", "ZM": "ZMB", "ZW": "ZWE",
}


def _here_sequence_mode(transport_mode: str, opts: RoutingOptions) -> str:
    """Build the findsequence2 ``mode`` string (see the call site for the why).

    HERE's format is ``Type;TransportModes;TrafficMode;Feature:weight,...`` — the
    road features share ONE segment, comma-delimited, and ``-3`` means "strictly
    avoid". Ferries are two features there (``boatFerry``, ``railFerry``) where
    /routes has one ``ferry``, so both are avoided."""
    traffic = "enabled" if traffic_requested(opts.traffic_mode) else "disabled"
    mode = f"fastest;{transport_mode};traffic:{traffic}"
    features = []
    if opts.avoid_tolls:
        features.append("tollroad:-3")
    if opts.avoid_highways:
        features.append("motorway:-3")
    if opts.avoid_ferries:
        features += ["boatFerry:-3", "railFerry:-3"]
    if features:
        mode += ";" + ",".join(features)
    return mode


def _here_notice(raw: Any) -> Optional[str]:
    """Read the first usable ``notices[]`` entry from a HERE routing response.

    Live-verified shape: an unroutable request returns HTTP 200 with
    ``{"routes": [], "notices": [{"title", "code", "severity"}]}`` — e.g.
    ``couldNotMatchOrigin`` / severity ``critical``.
    """
    notices = jlist(jget(raw, "notices"))
    if not notices:
        return None
    for notice in notices:
        code = jstr(jget(notice, "code"))
        title = jstr(jget(notice, "title"))
        if code and title:
            return f"{title} ({code})"
        if title:
            return title
        if code:
            return code
    return None
