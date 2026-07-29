"""ESRI/ArcGIS connectors: routing, matrix (OD cost matrix), geocoding,
isochrone (ServiceArea). Dual-auth, form POST, 200-with-error-body."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, List, Optional

from ._jsonpath import jget, jlist, jnum, jnum_opt, jstr
from ._util import compact_json, decode_json, ok_status
from ._waypoint_order import invert_waypoint_positions
from .base import BaseConnector
from .config import EsriConfig
from .coordinate import assert_finite, fmt_coord, to_lng_lat_string
from .enums import IsochroneType, PlaceDetailsInclude, TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, unknown_error
from .geocoding import (
    AutocompleteOptions,
    AutocompletePrediction,
    AutocompleteResult,
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

_ROUTE_URL = "https://route-api.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/solve"
_MATRIX_URL = "https://route-api.arcgis.com/arcgis/rest/services/World/OriginDestinationCostMatrix/NAServer/OriginDestinationCostMatrix_World/solveODCostMatrix"
_GEOCODE_URL = "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
_REVGEOCODE_URL = "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
_SUGGEST_URL = "https://geocode-api.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest"
_SERVICE_AREA_URL = "https://route-api.arcgis.com/arcgis/rest/services/World/ServiceAreas/NAServer/ServiceArea_World/solveServiceArea"
_MIN_TO_SEC = 60
_KM_TO_M = 1000
# Only reached if a service reports distance in miles rather than kilometers.
_METERS_PER_MILE = 1609.344


def _esri_epoch_ms(d: _dt.datetime) -> str:
    """Esri wants epoch milliseconds. A NAIVE datetime is interpreted as UTC
    (parity with the other operations and the sibling languages), NOT the host's
    local timezone — otherwise the same input yields a different instant per host."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return str(int(d.timestamp() * 1000))


def resolve_esri_bearer_token(cfg: EsriConfig) -> str:
    has_key, has_tok = bool(cfg.api_key), bool(cfg.arcgis_token)
    if has_key and has_tok:
        raise invalid_request("EsriConfig: apiKey and arcgisToken are mutually exclusive — provide exactly one.")
    if not has_key and not has_tok:
        msg = "EsriConfig: one of apiKey or arcgisToken is required."
        raise ConnectorError(ProviderCode.AUTH_FAILED, message=msg, provider_message=msg)
    return cfg.api_key if has_key else cfg.arcgis_token


class _EsriBase(BaseConnector):
    def __init__(self, config: EsriConfig, transport=None) -> None:
        super().__init__(transport)
        self.cfg = config

    def _post_form(self, url, form, pt):
        m_body, m_headers, m_query = merge_passthrough(form, {}, pt, None)
        final = {k: _esri_stringify(v) for k, v in m_body.items()}
        return self.send_post_form(url, final, m_headers, m_query)


class EsriRoutingConnector(_EsriBase):
    def route(self, opts: RoutingOptions) -> RoutingResult:
        wps = list(opts.waypoints)
        if len(wps) < 2:
            raise invalid_request("ESRI Routing requires at least two waypoints")
        for w in wps:
            assert_finite(w, "ESRI Routing")
        # ESRI findBestSequence optimizes an OPEN route (optionally preserving the
        # first/last stop); it has no closed round-trip mode.
        if opts.is_round_trip:
            raise ConnectorError(
                ProviderCode.UNSUPPORTED_OPTION,
                message="ESRI route optimization does not support round trips (isRoundTrip)",
                provider_message="ESRI findBestSequence optimizes an open route and cannot return a closed round trip; remove isRoundTrip or use a provider that supports it (e.g. Mapbox/OSRM).",
            )
        token = resolve_esri_bearer_token(self.cfg)
        form: dict[str, Any] = {
            "f": "json",
            "token": token,
            "stops": _esri_point_feature_set(wps),
            "returnRoutes": "true",
            # Legs come from the ``stops`` cumulative costs rather than the
            # ``directions`` output: Esri documents that output as superseded, and its
            # ``esriDMT*`` maneuver values are not enumerated in the REST reference.
            "returnStops": "true",
            # Explicit because the service default is ``true``, so omitting this still
            # ships the whole turn-by-turn payload.
            "returnDirections": "false",
            # Produces the ``Cumul_<attr>`` fields; no ``impedanceAttributeName`` needed.
            "accumulateAttributeNames": f"{_esri_time_attribute_for(opts.travel_mode)},Kilometers",
            # Only ``paths`` is read; ``...WithMeasure`` adds an m-value per point.
            "outputLines": "esriNAOutputLineTrueShape",
            "outSR": "4326",
        }
        if opts.optimize:
            form["findBestSequence"] = "true"
            # Needed to recover the optimized visiting order: the `stops`
            # FeatureSet carries each stop's 1-based Sequence (there is no
            # `Stops` route attribute).
            if opts.optimize_fixed_origin:
                form["preserveFirstStop"] = "true"
            if opts.optimize_fixed_destination:
                form["preserveLastStop"] = "true"
        tm = _esri_travel_mode(opts.travel_mode, "ESRI Routing")
        if tm:
            form["travelMode"] = tm
        r = _esri_routing_restrictions(opts)
        if r:
            form["restrictionAttributeNames"] = r
        if opts.departure_time:
            form["startTime"] = _esri_epoch_ms(opts.departure_time)

        resp = self._post_form(_ROUTE_URL, form, opts.passthrough)
        if not ok_status(resp.status):
            raise _esri_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if jget(raw, "error") is not None:
            raise _esri_body_error(resp.status, raw)
        if raw is None:
            raise unknown_error(resp.status, None, "ESRI Routing returned non-JSON body")
        features = jlist(jget(jget(raw, "routes"), "features")) or []
        if not features:
            # Esri's OBSERVED no-route path is the in-body error envelope (an
            # unlocated stop), not an empty featureset. This branch is a shape Esri
            # has not been seen to produce — classify it with the same code as the
            # other five so a consumer has one "provider answered, no route" case.
            raise classified_error(
                ProviderCode.NO_ROUTE, resp.status, raw, "ESRI Routing returned no routes"
            )
        feature = features[0]
        attrs = jget(feature, "attributes") or {}

        # Legs and totals share one source, so they always reconcile.
        cumulative = _esri_cumulative_stop_costs(jlist(jget(jget(raw, "stops"), "features")) or [])
        if cumulative is not None:
            total_dist, total_dur, legs = cumulative
        else:
            # A stop that failed to locate carries no cumulative cost, and a service
            # configured without the accumulate attributes returns none. The route's
            # own totals are named after the active impedance.
            total_dist = _esri_route_total_distance_meters(attrs)
            total_dur = _esri_route_total_duration_seconds(attrs)
            legs = _esri_even_split_legs(len(wps), total_dist, total_dur)
        pts: List[LatLng] = []
        for path in (jget(jget(feature, "geometry"), "paths") or []):
            for point in path:
                if isinstance(point, list) and len(point) >= 2:
                    pts.append(LatLng(point[1], point[0]))
        # Optimized routes only. Stops are always fetched now, so without this gate an
        # unoptimized route would report a useless identity permutation.
        waypoint_order = (
            _esri_waypoint_order(jlist(jget(jget(raw, "stops"), "features")) or [], len(wps))
            if opts.optimize
            else None
        )

        return RoutingResult(
            legs=legs,
            total_distance_meters=total_dist,
            total_duration_seconds=total_dur,
            polyline=encode_polyline(pts),
            waypoint_order=waypoint_order,
            raw=raw,
        )


class EsriMatrixConnector(_EsriBase):
    def matrix(self, opts: MatrixOptions) -> MatrixResult:
        if not opts.origins or not opts.destinations:
            raise invalid_request("ESRI Matrix requires at least one origin and one destination")
        for o in opts.origins:
            assert_finite(o, "ESRI matrix origin")
        for d in opts.destinations:
            assert_finite(d, "ESRI matrix destination")
        token = resolve_esri_bearer_token(self.cfg)
        form: dict[str, Any] = {
            "f": "json",
            "token": token,
            "origins": _esri_point_feature_set(opts.origins),
            "destinations": _esri_point_feature_set(opts.destinations),
            "outputType": "esriNAODOutputSparseMatrix",
            "impedanceAttributeName": "TravelTime",
            "accumulateAttributeNames": "Kilometers",
            "outSR": "4326",
        }
        tm = _esri_travel_mode(opts.travel_mode, "ESRI Matrix")
        if tm:
            form["travelMode"] = tm
        if opts.avoid_tolls:
            form["restrictionAttributeNames"] = "Avoid Toll Roads"
        if opts.departure_time:
            form["startTime"] = _esri_epoch_ms(opts.departure_time)

        resp = self._post_form(_MATRIX_URL, form, opts.passthrough)
        if not ok_status(resp.status):
            raise _esri_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if jget(raw, "error") is not None:
            raise _esri_body_error(resp.status, raw)
        if raw is None:
            raise unknown_error(resp.status, None, "ESRI Matrix returned non-JSON body")

        no, nd = len(opts.origins), len(opts.destinations)
        odm = jget(raw, "odCostMatrix")
        if isinstance(odm, dict):
            attr_order = jlist(jget(odm, "costAttributeNames")) or ["TravelTime", "Kilometers"]
            # The impedance column is named after the active travel mode: driving
            # reports TravelTime, walking reports WalkTime (the WALK travelMode
            # object overrides the requested impedanceAttributeName). Locate it by
            # the known time-impedance names rather than assuming TravelTime, else a
            # walking matrix silently decodes every duration as 0.
            time_idx = -1
            for name in _ESRI_TIME_ATTRIBUTE_NAMES:
                if name in attr_order:
                    time_idx = attr_order.index(name)
                    break
            dist_idx = attr_order.index("Kilometers") if "Kilometers" in attr_order else -1
            cells: List[MatrixCell] = []
            for origin_key, dest_map in odm.items():
                if origin_key == "costAttributeNames":
                    continue
                oid = _esri_matrix_oid(origin_key)
                if oid is None or not (1 <= oid <= no) or not isinstance(dest_map, dict):
                    raise unknown_error(resp.status, raw, "ESRI Matrix sparse response returned an out-of-range or non-numeric origin OID")
                for dest_key, cell in dest_map.items():
                    did = _esri_matrix_oid(dest_key)
                    if did is None or not (1 <= did <= nd):
                        raise unknown_error(resp.status, raw, "ESRI Matrix sparse response returned an out-of-range or non-numeric destination OID")
                    t, d = _esri_decode_cost_cell(cell, time_idx, dist_idx)
                    cells.append(MatrixCell(oid - 1, did - 1, d * _KM_TO_M, t * _MIN_TO_SEC))
            # A sparse result (fewer routable cells than the requested grid) is
            # normal: an unroutable origin×destination pair is OMITTED, not an error
            # for the whole matrix — parity with the other providers' cell omission.
            return MatrixResult(cells=cells, raw=raw)

        odl = jget(raw, "odLines")
        if isinstance(odl, dict):
            cells = []
            for f in jlist(jget(odl, "features")) or []:
                a = jget(f, "attributes") or {}
                oid, did = jnum(a.get("OriginID")), jnum(a.get("DestinationID"))
                if oid < 1 or did < 1:
                    raise unknown_error(resp.status, raw, "ESRI Matrix odLines returned a non-positive or non-finite OID; cannot map to a 0-based cell index")
                cells.append(MatrixCell(int(oid) - 1, int(did) - 1, jnum(a.get("Total_Kilometers")) * _KM_TO_M, jnum(a.get("Total_TravelTime")) * _MIN_TO_SEC))
            if len(cells) < no * nd:
                raise unknown_error(resp.status, raw, "ESRI Matrix returned a matrix that does not match the requested dimensions")
            return MatrixResult(cells=cells, raw=raw)

        raise unknown_error(resp.status, raw, "ESRI Matrix response missing odCostMatrix and odLines payload")


class EsriGeocodingConnector(_EsriBase):
    def geocode(self, opts: GeocodeOptions) -> GeocodeResult:
        token = resolve_esri_bearer_token(self.cfg)
        query: dict[str, Any] = {"f": "json", "token": token, "singleLine": opts.address, "outFields": "*"}
        if opts.country_filter:
            query["countryCode"] = ",".join(opts.country_filter)
        if opts.language:
            query["langCode"] = opts.language
        raw = self._dispatch_get(_GEOCODE_URL, query, opts.passthrough, "ESRI geocoding failed")
        cands: List[GeocodeCandidate] = []
        for ci in jlist(jget(raw, "candidates")) or []:
            loc = jget(ci, "location") or {}
            # Skip a candidate without real coordinates rather than emitting a
            # fabricated (0,0) "Null Island" position.
            y, x = jnum_opt(loc.get("y")), jnum_opt(loc.get("x"))
            if y is None or x is None:
                continue
            viewport = None
            ext = jget(ci, "extent")
            if isinstance(ext, dict):
                viewport = Viewport(LatLng(jnum(ext.get("ymin")), jnum(ext.get("xmin"))), LatLng(jnum(ext.get("ymax")), jnum(ext.get("xmax"))))
            cands.append(GeocodeCandidate(formatted_address=jstr(jget(ci, "address")), location=LatLng(y, x), viewport=viewport))
        return GeocodeResult(candidates=cands, raw=raw)

    def reverse_geocode(self, opts: ReverseGeocodeOptions) -> ReverseGeocodeResult:
        assert_finite(opts.location, "ESRI reverseGeocode")
        token = resolve_esri_bearer_token(self.cfg)
        query: dict[str, Any] = {"f": "json", "token": token, "location": to_lng_lat_string(opts.location)}
        if opts.language:
            query["langCode"] = opts.language
        raw = self._dispatch_get(_REVGEOCODE_URL, query, opts.passthrough, "ESRI reverse geocoding failed")
        addr, loc = jget(raw, "address"), jget(raw, "location")
        if addr is None or loc is None:
            return ReverseGeocodeResult(candidates=[], raw=raw)
        formatted = jstr(jget(addr, "LongLabel")) or jstr(jget(addr, "Match_addr"))
        if not formatted:
            return ReverseGeocodeResult(candidates=[], raw=raw)
        # No coordinates means no usable candidate — never a fabricated (0,0).
        y, x = jnum_opt(jget(loc, "y")), jnum_opt(jget(loc, "x"))
        if y is None or x is None:
            return ReverseGeocodeResult(candidates=[], raw=raw)
        return ReverseGeocodeResult(candidates=[GeocodeCandidate(formatted_address=formatted, location=LatLng(y, x))], raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        token = resolve_esri_bearer_token(self.cfg)
        query: dict[str, Any] = {"f": "json", "token": token, "text": opts.input}
        if opts.location is not None:
            assert_finite(opts.location, "ESRI autocomplete location")
            query["location"] = to_lng_lat_string(opts.location)
        # `country_filter` → `countryCode` (comma-joined alpha-2; ESRI uses alpha-2
        # directly), same translation as forward geocode.
        if opts.country_filter:
            query["countryCode"] = ",".join(opts.country_filter)
        raw = self._dispatch_get(_SUGGEST_URL, query, opts.passthrough, "ESRI autocomplete failed")
        preds = [AutocompletePrediction(description=jstr(jget(s, "text")), place_id=jstr(jget(s, "magicKey")) or None) for s in (jlist(jget(raw, "suggestions")) or [])]
        return AutocompleteResult(predictions=preds, raw=raw)

    def _dispatch_get(self, url, base_query, pt, label):
        m_body, m_headers, m_query = merge_passthrough(base_query, {}, pt, None)
        final_query = {k: _esri_stringify(v) for k, v in m_body.items() if v is not None}
        final_query.update(m_query)
        resp = self.send_get(url, m_headers, final_query)
        if not ok_status(resp.status):
            raise _esri_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if raw is None:
            raise unknown_error(resp.status, None, f"{label}: non-JSON body")
        if jget(raw, "error") is not None:
            raise _esri_body_error(resp.status, raw)
        return raw

    def place_details(self, opts: PlaceDetailsOptions) -> PlaceDetailsResult:
        """Resolve an Esri ``magicKey`` (from ``autocomplete()``) to a full candidate.

        ``GET .../findAddressCandidates?magicKey=`` — the same endpoint as forward
        geocode, so the same normalizer applies.

        **Live-verified that ``magicKey`` alone is sufficient.** Esri's docs pair it
        with the original ``SingleLine`` text, and the plan here originally did too;
        probing showed the key on its own resolves to the byte-identical candidate.
        That is why ``place_id`` needs no companion field — our ``place_id`` IS the
        magicKey.
        """
        query = {
            "f": "json",
            "token": resolve_esri_bearer_token(self.cfg),
            "magicKey": opts.place_id,
            "outFields": "*",
        }
        if opts.language:
            query["langCode"] = opts.language

        raw = self._dispatch_get(_GEOCODE_URL, query, opts.passthrough, "ESRI place details failed")

        cands = jlist(jget(raw, "candidates")) or []
        first = cands[0] if cands else None
        if first is None:
            raise classified_error(
                ProviderCode.NO_ROUTE, None, raw, "ESRI Place Details returned no candidate"
            )

        loc = jget(first, "location") or {}
        y, x = jnum_opt(loc.get("y")), jnum_opt(loc.get("x"))
        if y is None or x is None:
            raise classified_error(
                ProviderCode.NO_ROUTE, None, raw, "ESRI Place Details returned no location"
            )

        viewport = None
        ext = jget(first, "extent")
        if isinstance(ext, dict):
            corners = [
                jnum_opt(ext.get("ymin")), jnum_opt(ext.get("xmin")),
                jnum_opt(ext.get("ymax")), jnum_opt(ext.get("xmax")),
            ]
            if all(c is not None for c in corners):
                ymin, xmin, ymax, xmax = corners
                viewport = Viewport(LatLng(ymin, xmin), LatLng(ymax, xmax))

        candidate = GeocodeCandidate(
            formatted_address=jstr(jget(first, "address")),
            location=LatLng(y, x),
            viewport=viewport,
        )

        # Esri returns only an address — there is no separate display name to
        # surface, so `name` stays None even when requested.
        return PlaceDetailsResult(candidate=candidate, raw=raw)


class EsriIsochroneConnector(_EsriBase):
    def isochrone(self, opts: IsochroneOptions) -> IsochroneResult:
        from .isochrone_validate import validate_isochrone_cap

        validate_isochrone_cap(opts.values)
        assert_finite(opts.center, "ESRI isochrone center")
        if opts.travel_mode == TravelMode.CYCLING:
            raise ConnectorError(ProviderCode.UNSUPPORTED_TRAVEL_MODE, message="ESRI isochrone does not support cycling", provider_message="ESRI isochrone does not support cycling")
        token = resolve_esri_bearer_token(self.cfg)

        if opts.type == IsochroneType.TIME:
            # ESRI accepts FRACTIONAL minutes, so convert seconds losslessly rather
            # than rounding to whole minutes (which corrupted sub-minute breaks: 30s
            # → 1 min → 60s, and 20s → 0). `:g` on a 6-decimal round strips float noise
            # and trailing zeros (5.0 → "5", 0.5 → "0.5", 20s → "0.333333").
            breaks = ",".join(f"{round(v / 60, 6):g}" for v in opts.values)
            break_units = "esriDriveTimeUnitsMinutes"
        else:
            breaks = ",".join(fmt_coord(v) for v in opts.values)
            break_units = "esriDriveDistanceUnitsMeters"
        form: dict[str, Any] = {
            "f": "json",
            "token": token,
            "facilities": _esri_point_feature_set([opts.center]),
            "defaultBreaks": breaks,
            "breakUnits": break_units,
            "outputPolygons": "esriNAOutputPolygonDetailed",
            "returnFacilities": "false",
            "travelDirection": "esriNATravelDirectionFromFacility",
            "outSR": "4326",
        }
        if opts.travel_mode == TravelMode.WALKING:
            # ArcGIS requires a full travel-mode JSON object, not a name string.
            form["travelMode"] = _esri_walking_travel_mode_json()
        if opts.departure_time:
            try:
                t = _dt.datetime.fromisoformat(opts.departure_time.replace("Z", "+00:00"))
            except ValueError as exc:
                # An unparseable departure_time was silently dropped — surface it
                # rather than quietly ignoring the caller's request.
                raise invalid_request(
                    f"Esri isochrone departure_time is not a valid ISO-8601 datetime: {opts.departure_time!r}"
                ) from exc
            form["timeOfDay"] = _esri_epoch_ms(t)

        resp = self._post_form(_SERVICE_AREA_URL, form, opts.passthrough)
        if not ok_status(resp.status):
            raise _esri_http_error(resp.status, resp.headers, resp.body)
        raw = decode_json(resp.body)
        if raw is None:
            raise unknown_error(resp.status, None, "ESRI Isochrone returned non-JSON body")
        if jget(raw, "error") is not None:
            raise _esri_body_error(resp.status, raw)
        contours: List[IsochroneContour] = []
        for f in jlist(jget(jget(raw, "saPolygons"), "features")) or []:
            rings = jget(jget(f, "geometry"), "rings") or []
            outer = rings[0] if rings else []
            to_break = jnum(jget(jget(f, "attributes"), "ToBreak"))
            value = to_break * _MIN_TO_SEC if opts.type == IsochroneType.TIME else to_break
            contours.append(IsochroneContour(value=value, geometry=Polygon(type="Polygon", coordinates=[outer])))
        contours.sort(key=lambda c: c.value)
        return IsochroneResult(contours=contours, raw=raw)


# ---- shared esri helpers ----

def _esri_point_feature_set(points) -> str:
    features = [{"geometry": {"x": p.lng, "y": p.lat, "spatialReference": {"wkid": 4326}}} for p in points]
    return compact_json({"features": features}).decode("utf-8")


# Time-impedance column names ESRI reports for the travel modes this wrapper
# requests (driving -> TravelTime, walking -> WalkTime). The OD Cost Matrix names
# its cost columns after the active impedance, so the matrix decoder locates the
# time column by trying these in order rather than assuming "TravelTime".
_ESRI_TIME_ATTRIBUTE_NAMES = ("TravelTime", "WalkTime")


def _esri_walking_travel_mode() -> dict:
    """Canonical ArcGIS World "Walking Time" travel-mode object.

    ArcGIS Network Analyst services require ``travelMode`` to be a full JSON
    object, not a name string — a bare ``"Walking"`` is ignored and the service
    stays on driving. Setting this object makes the service override its impedance
    to ``WalkTime`` (route summaries carry ``Total_WalkTime``; the OD matrix
    reports ``costAttributeNames`` ``["WalkTime", ...]``, read
    travel-mode-independently by the connectors). Verified live against
    ``route-api.arcgis.com`` on 2026-07-21.
    """
    return {
        "attributeParameterValues": [
            {"parameterName": "Restriction Usage", "attributeName": "Walking", "value": "PROHIBITED"},
            {"parameterName": "Restriction Usage", "attributeName": "Preferred for Pedestrians", "value": "PREFER_LOW"},
            {"parameterName": "Walking Speed (km/h)", "attributeName": "WalkTime", "value": 5},
        ],
        "description": "Follows paths and roads that allow pedestrian traffic and finds solutions that optimize travel time. The walking speed is set to 5 kilometers per hour.",
        "impedanceAttributeName": "WalkTime",
        "simplificationToleranceUnits": "esriMeters",
        "uturnAtJunctions": "esriNFSBAllowBacktrack",
        "restrictionAttributeNames": [
            "Avoid Private Roads",
            "Avoid Roads Unsuitable for Pedestrians",
            "Preferred for Pedestrians",
            "Walking",
        ],
        "useHierarchy": False,
        "simplificationTolerance": 2,
        "timeAttributeName": "WalkTime",
        "distanceAttributeName": "Kilometers",
        "type": "WALK",
        "id": "caFAgoThrvUpkFBW",
        "name": "Walking Time",
    }


def _esri_walking_travel_mode_json() -> str:
    return json.dumps(_esri_walking_travel_mode(), separators=(",", ":"), ensure_ascii=False)


def _esri_travel_mode(m: TravelMode, label: str) -> str:
    if m == TravelMode.WALKING:
        return _esri_walking_travel_mode_json()
    if m == TravelMode.CYCLING:
        msg = f'{label} does not support travelMode "cycling"'
        raise ConnectorError(ProviderCode.UNSUPPORTED_TRAVEL_MODE, message=msg, provider_message=msg)
    return ""


def _esri_routing_restrictions(opts: RoutingOptions) -> str:
    r = []
    if opts.avoid_tolls:
        r.append("Avoid Toll Roads")
    if opts.avoid_ferries:
        r.append("Avoid Ferries")
    if opts.avoid_highways:
        r.append("Avoid Limited Access Roads")
    return ",".join(r)


def _esri_stringify(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return fmt_coord(float(v)) if isinstance(v, float) else str(v)
    if v is None:
        return ""
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _esri_cumulative_stop_costs(features: List[Any]):
    """Per-leg distances/durations and the route totals, from the per-stop cumulative costs.

    ``Cumul_<attribute>`` is the cost from the origin *to and including* that stop, so a
    leg is the difference between consecutive stops and the total is the last value —
    which is why legs always sum to the totals here.

    Two things are easy to get wrong:

    1. Stops arrive in INPUT order while cumulative costs run along the route, so they
       must be sorted by ``Sequence``. Without it an optimized route yields negative legs.
    2. The field name carries the active impedance — ``Cumul_TravelTime`` driving,
       ``Cumul_WalkTime`` walking — so the keys are discovered, not hardcoded.

    Returns ``None`` when the values are unusable: fewer than two stops, a stop that
    failed to locate (``Status != 0`` carries no cumulative cost), a non-monotonic
    sequence, or a service configured without the accumulate attributes.
    """
    if len(features) < 2:
        return None

    rows = []
    for f in features:
        a = jget(f, "attributes") or {}
        if jnum_opt(a.get("Sequence")) is None:
            return None
        rows.append(a)

    # Sequence is 1-based; sorting by it puts the stops in route order.
    rows.sort(key=lambda a: jnum(a.get("Sequence")))

    cumul_keys = [k for k in rows[0] if isinstance(k, str) and k.startswith("Cumul_")]
    distance_key = next((k for k in cumul_keys if "Kilometers" in k or k.endswith("Miles")), None)
    time_key = next((k for k in cumul_keys if k != distance_key), None)
    if distance_key is None or time_key is None:
        return None

    to_meters = _METERS_PER_MILE if distance_key.endswith("Miles") else _KM_TO_M

    distances: List[float] = []
    times: List[float] = []
    for a in rows:
        d = jnum_opt(a.get(distance_key))
        t = jnum_opt(a.get(time_key))
        if d is None or t is None:
            return None
        # Not located / not reached: no cumulative cost, so later diffs are wrong.
        status = jnum_opt(a.get("Status"))
        if status is not None and status != 0:
            return None
        distances.append(d * to_meters)
        times.append(t * _MIN_TO_SEC)

    legs: List[RoutingLeg] = []
    for i in range(1, len(rows)):
        dm = distances[i] - distances[i - 1]
        ds = times[i] - times[i - 1]
        # Cumulative costs never decrease, so a negative diff means wrong order.
        if dm < 0 or ds < 0:
            return None
        legs.append(RoutingLeg(dm, ds))

    return distances[-1], times[-1], legs


def _esri_route_total_distance_meters(attrs: Any) -> float:
    """The route's total distance in meters, matched by shape: the attribute is suffixed
    with the active distance attribute and this service emits no ``Total_Length``."""
    if jnum_opt(attrs.get("Total_Length")) is not None:
        return jnum(attrs.get("Total_Length"))
    if jnum_opt(attrs.get("Total_Kilometers")) is not None:
        return jnum(attrs.get("Total_Kilometers")) * _KM_TO_M
    if jnum_opt(attrs.get("Total_Miles")) is not None:
        return jnum(attrs.get("Total_Miles")) * _METERS_PER_MILE
    return 0.0


def _esri_route_total_duration_seconds(attrs: Any) -> float:
    """The route's total duration in seconds. Same shape-based match: any ``Total_*``
    that is not a distance attribute is the time one, in minutes."""
    if jnum_opt(attrs.get("Total_Time")) is not None:
        return jnum(attrs.get("Total_Time")) * _MIN_TO_SEC
    for key, value in attrs.items():
        name = str(key)
        if not name.startswith("Total_"):
            continue
        if "Kilometers" in name or "Miles" in name or "Length" in name:
            continue
        if jnum_opt(value) is not None:
            return jnum(value) * _MIN_TO_SEC
    return 0.0


def _esri_even_split_legs(waypoint_count: int, total_dist: float, total_dur: float) -> List[RoutingLeg]:
    """Even split of the totals, used when per-stop cumulative costs are unavailable."""
    num_legs = max(1, waypoint_count - 1)
    return [RoutingLeg(total_dist / num_legs, total_dur / num_legs) for _ in range(num_legs)]


def _esri_time_attribute_for(mode: Optional[TravelMode]) -> str:
    """The time attribute to accumulate, which determines the ``Cumul_<attr>`` field name.

    Must mirror :func:`_esri_travel_mode`: requesting an attribute the active impedance
    does not use yields no cumulative field at all, silently.
    """
    return "WalkTime" if mode == TravelMode.WALKING else "TravelTime"


def _esri_waypoint_order(stops: List[Any], total_stops: int) -> Optional[List[int]]:
    """Derive the optimized visiting sequence from the `stops` FeatureSet.

    Stops are returned in INPUT order, each carrying a 1-based ``Sequence`` =
    its position in the optimized route. Invert to the canonical
    ``waypoint_order`` = full visiting sequence of INPUT indices
    (``order[Sequence - 1] = input_index``). Returns ``None`` when the sequence
    data is absent, incomplete, or malformed.
    """
    # Esri Sequence is 1-based; the shared helper expects 0-based visit
    # positions. Non-integer values are forwarded as None so the helper rejects
    # them (it also enforces length, range, and no-duplicates).
    positions: List[Any] = []
    for feature in stops:
        seq = jget(jget(feature, "attributes"), "Sequence")
        positions.append(seq - 1 if isinstance(seq, int) and not isinstance(seq, bool) else None)
    return invert_waypoint_positions(positions, total_stops)


def _esri_matrix_oid(key: Any) -> Optional[int]:
    """Parse a 1-based OID from a sparse-matrix key; None if not an integer."""
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key
    if isinstance(key, str):
        try:
            return int(key)
        except ValueError:
            return None
    return None


def _esri_decode_cost_cell(cell, time_idx, dist_idx):
    if isinstance(cell, list):
        t = cell[time_idx] if 0 <= time_idx < len(cell) else 0
        d = cell[dist_idx] if 0 <= dist_idx < len(cell) else 0
        return jnum(t), jnum(d)
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return float(cell), 0.0
    return 0.0, 0.0


def _esri_body_error_code(body: Any) -> Optional[int]:
    e = jget(body, "error")
    if e is None:
        return None
    c = jget(e, "code")
    if isinstance(c, bool):
        return None
    if isinstance(c, (int, float)):
        return int(c)
    if isinstance(c, str):
        try:
            return int(c)
        except ValueError:
            return None
    return None


def _esri_has_unlocated_stop(body: Any) -> bool:
    """Whether an Esri error body names a stop it could not locate on the network.

    Live-verified shape: HTTP 200 with
    ``{"error": {"code": 400, "message": "Unable to complete operation.",
    "details": ['Location "Location 1" in "Stops" is unlocated. …']}}``.
    The ``details[]`` array is the only place the cause appears.
    """
    details = jlist(jget(jget(body, "error"), "details"))
    if not details:
        return False
    return any(isinstance(d, str) and "unlocated" in d.lower() for d in details)


def _esri_map_vendor_error(status: int, body: Any) -> ProviderCode:
    code = _esri_body_error_code(body)
    if status == 429 or code == 429:
        return ProviderCode.RATE_LIMITED
    if code is not None:
        if code in (498, 499, 403):
            return ProviderCode.AUTH_FAILED
        if code in (400, 404):
            # Esri has no distinct code for "no route": an unroutable stop comes
            # back as HTTP 200 with error.code: 400 and a details[] entry naming the
            # stop as **unlocated** (live-verified). `unlocated` is Esri's own term
            # for a stop it could not snap to the network, so matching it reads a
            # stated condition rather than inferring one — the same bar the OSRM
            # `profile not found` match already meets.
            return ProviderCode.NO_ROUTE if _esri_has_unlocated_stop(body) else ProviderCode.INVALID_REQUEST
        if code == 500:
            return ProviderCode.PROVIDER_UNAVAILABLE
        return ProviderCode.UNKNOWN
    if status in (401, 403):
        return ProviderCode.AUTH_FAILED
    if status == 400:
        return ProviderCode.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderCode.PROVIDER_UNAVAILABLE
    return ProviderCode.UNKNOWN


def _esri_error_message(body: Any) -> str:
    e = jget(body, "error")
    em = jstr(jget(e, "message"))
    if em:
        return em
    c = jget(e, "code")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return str(int(c))
    if isinstance(c, str) and c:
        return c
    return jstr(jget(body, "message")) or jstr(jget(body, "error"))


def _esri_http_error(status: int, headers, data: bytes) -> ConnectorError:
    raw = decode_json(data)
    return provider_error(status, headers, raw, _esri_map_vendor_error(status, raw), _esri_error_message(raw))


def _esri_body_error(status: int, body: Any) -> ConnectorError:
    return classified_error(_esri_map_vendor_error(status, body), status, jget(body, "error"), _esri_error_message(body))
