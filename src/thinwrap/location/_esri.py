"""ESRI/ArcGIS connectors: routing, matrix (OD cost matrix), geocoding,
isochrone (ServiceArea). Dual-auth, form POST, 200-with-error-body."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, List, Optional

from ._jsonpath import jget, jlist, jnum, jstr
from ._util import compact_json, decode_json, ok_status
from .base import BaseConnector
from .config import EsriConfig
from .coordinate import assert_finite, fmt_coord, to_lng_lat_string
from .enums import IsochroneType, TravelMode
from .errors import ConnectorError, ProviderCode, classified_error, invalid_request, provider_error, unknown_error
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
_STOP_MANEUVER = "esriDMTStop"
_MIN_TO_SEC = 60
_KM_TO_M = 1000


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
            "returnDirections": "true",
            "directionsLengthUnits": "esriNAUMeters",
            "directionsOutputType": "esriDOTComplete",
            "outputLines": "esriNAOutputLineTrueShapeWithMeasure",
            "outSR": "4326",
        }
        if opts.optimize:
            form["findBestSequence"] = "true"
            # Needed to recover the optimized visiting order: the `stops`
            # FeatureSet carries each stop's 1-based Sequence (there is no
            # `Stops` route attribute).
            form["returnStops"] = "true"
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
            raise unknown_error(resp.status, raw, "ESRI Routing returned no routes")
        feature = features[0]
        attrs = jget(feature, "attributes") or {}

        # Totals come from the directions summary, which is travel-mode-independent
        # (totalLength in meters via directionsLengthUnits=esriNAUMeters, totalTime
        # in minutes). The route feature's Total_* attributes are named after the
        # active impedance — driving reports Total_TravelTime, walking reports
        # Total_WalkTime, and neither Total_Length nor Total_Time is emitted at all
        # — so reading them directly silently yields 0 for any non-driving mode.
        # Fall back to the attributes only when the summary is absent (verified live
        # 2026-07-21).
        summary = _esri_directions_summary(raw)
        summary_len = summary.get("totalLength") if summary is not None else None
        summary_time = summary.get("totalTime") if summary is not None else None
        if summary_len is not None:
            total_dist = jnum(summary_len)
        elif attrs.get("Total_Length") is not None:
            total_dist = jnum(attrs.get("Total_Length"))
        elif attrs.get("Total_Kilometers") is not None:
            total_dist = jnum(attrs.get("Total_Kilometers")) * 1000
        else:
            total_dist = 0.0
        if summary_time is not None:
            total_min = jnum(summary_time)
        elif attrs.get("Total_Time") is not None:
            total_min = jnum(attrs.get("Total_Time"))
        elif attrs.get("Total_TravelTime") is not None:
            total_min = jnum(attrs.get("Total_TravelTime"))
        elif attrs.get("Total_WalkTime") is not None:
            total_min = jnum(attrs.get("Total_WalkTime"))
        else:
            total_min = 0.0
        total_dur = total_min * _MIN_TO_SEC

        legs = _esri_reconstruct_legs(jlist(jget(raw, "directions")) or [], len(wps), total_dist, total_dur)
        pts: List[LatLng] = []
        for path in (jget(jget(feature, "geometry"), "paths") or []):
            for point in path:
                if isinstance(point, list) and len(point) >= 2:
                    pts.append(LatLng(point[1], point[0]))
        return RoutingResult(
            legs=legs,
            total_distance_meters=total_dist,
            total_duration_seconds=total_dur,
            polyline=encode_polyline(pts),
            waypoint_order=_esri_waypoint_order(jlist(jget(jget(raw, "stops"), "features")) or [], len(wps)),
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
            viewport = None
            ext = jget(ci, "extent")
            if isinstance(ext, dict):
                viewport = Viewport(LatLng(jnum(ext.get("ymin")), jnum(ext.get("xmin"))), LatLng(jnum(ext.get("ymax")), jnum(ext.get("xmax"))))
            cands.append(GeocodeCandidate(formatted_address=jstr(jget(ci, "address")), location=LatLng(jnum(loc.get("y")), jnum(loc.get("x"))), viewport=viewport))
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
        return ReverseGeocodeResult(candidates=[GeocodeCandidate(formatted_address=formatted, location=LatLng(jnum(jget(loc, "y")), jnum(jget(loc, "x"))))], raw=raw)

    def autocomplete(self, opts: AutocompleteOptions) -> AutocompleteResult:
        token = resolve_esri_bearer_token(self.cfg)
        query: dict[str, Any] = {"f": "json", "token": token, "text": opts.input}
        if opts.location is not None:
            assert_finite(opts.location, "ESRI autocomplete location")
            query["location"] = to_lng_lat_string(opts.location)
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


def _esri_directions_summary(raw) -> Optional[dict]:
    """Return the route-level directions summary (travel-mode-independent totals)
    when present. ESRI returns ``directions`` as a list whose first entry carries
    the ``summary`` object."""
    dirs = jlist(jget(raw, "directions")) or []
    if dirs and isinstance(dirs[0], dict):
        s = jget(dirs[0], "summary")
        if isinstance(s, dict):
            return s
    return None


def _esri_reconstruct_legs(directions, num_waypoints, total_dist, total_dur) -> List[RoutingLeg]:
    num_legs = max(1, num_waypoints - 1)

    def even_split():
        return [RoutingLeg(total_dist / num_legs, total_dur / num_legs) for _ in range(num_legs)]

    if not directions or not (jlist(jget(directions[0], "features")) or []):
        return even_split()
    steps = jlist(jget(directions[0], "features")) or []
    legs: List[RoutingLeg] = []
    acc_dist = acc_time = 0.0
    passed_first = False
    for step in steps:
        a = jget(step, "attributes") or {}
        if jstr(a.get("maneuverType")) == _STOP_MANEUVER:
            if not passed_first:
                passed_first = True
                acc_dist = acc_time = 0.0
                continue
            legs.append(RoutingLeg(acc_dist, acc_time * _MIN_TO_SEC))
            acc_dist = acc_time = 0.0
            continue
        if not passed_first:
            continue
        acc_dist += jnum(a.get("length"))
        acc_time += jnum(a.get("time"))
    if acc_dist > 0 or acc_time > 0:
        legs.append(RoutingLeg(acc_dist, acc_time * _MIN_TO_SEC))
    return legs or even_split()


def _esri_waypoint_order(stops: List[Any], total_stops: int) -> Optional[List[int]]:
    """Derive the optimized visiting sequence from the `stops` FeatureSet.

    Stops are returned in INPUT order, each carrying a 1-based ``Sequence`` =
    its position in the optimized route. Invert to the canonical
    ``waypoint_order`` = full visiting sequence of INPUT indices
    (``order[Sequence - 1] = input_index``). Returns ``None`` when the sequence
    data is absent, incomplete, or malformed.
    """
    if len(stops) != total_stops:
        return None
    order: List[int] = [-1] * total_stops
    for input_idx, feature in enumerate(stops):
        seq = jget(jget(feature, "attributes"), "Sequence")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1 or seq > total_stops or order[seq - 1] != -1:
            return None
        order[seq - 1] = input_idx
    return order


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


def _esri_map_vendor_error(status: int, body: Any) -> ProviderCode:
    code = _esri_body_error_code(body)
    if status == 429 or code == 429:
        return ProviderCode.RATE_LIMITED
    if code is not None:
        if code in (498, 499, 403):
            return ProviderCode.AUTH_FAILED
        if code in (400, 404):
            return ProviderCode.INVALID_REQUEST
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
