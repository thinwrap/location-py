"""The published default lists are exactly what the connectors send, and
passthrough REPLACES them rather than merging.

Replace (not append) is deliberate: an append-only merge would leave a caller no
way to drop a default field. The constants exist so appending stays a one-liner.
"""

from __future__ import annotations

import json

import pytest
from helpers import FakeTransport, body_form, body_json, no_sleep, qget, resp

import thinwrap.location as loc
from thinwrap.location import (
    ConnectorError,
    ESRI_MATRIX_ACCUMULATE_ATTRIBUTES,
    ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING,
    ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING,
    EsriConfig,
    HERE_MATRIX_ATTRIBUTES,
    MAPBOX_MATRIX_ANNOTATIONS,
    MapboxConfig,
    OSRM_TABLE_ANNOTATIONS,
    OsrmConfig,
    ProviderCode,
    TravelMode,
    GOOGLE_MATRIX_FIELD_MASK,
    GOOGLE_PLACE_DETAILS_FIELD_MASK,
    GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME,
    GOOGLE_ROUTES_FIELD_MASK,
    GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC,
    GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER,
    HERE_ROUTING_RETURN,
    Geocoding,
    GoogleConfig,
    HereConfig,
    LatLng,
    Matrix,
    MatrixOptions,
    Passthrough,
    PlaceDetailsInclude,
    PlaceDetailsOptions,
    Routing,
    RoutingInclude,
    RoutingOptions,
)

TWO = [LatLng(0, 0), LatLng(1, 1)]
THREE = [LatLng(0, 0), LatLng(1, 1), LatLng(2, 2)]

GOOGLE_ROUTE_BODY = json.dumps({
    "routes": [{
        "legs": [{"distanceMeters": 5000, "duration": "300s"}],
        "distanceMeters": 5000,
        "duration": "300s",
        "polyline": {"encodedPolyline": "abc"},
    }]
})
GOOGLE_MATRIX_BODY = '{"originIndex":0,"destinationIndex":0,"distanceMeters":1000,"duration":"600s","condition":"ROUTE_EXISTS"}'
GOOGLE_PLACE_BODY = json.dumps({
    "id": "p1",
    "formattedAddress": "somewhere",
    "location": {"latitude": 1, "longitude": 2},
})
HERE_BODY = json.dumps({
    "routes": [{"sections": [{
        "polyline": "BGwl_lgDo-6-T",
        "summary": {"length": 5000, "duration": 300},
    }]}]
})

ALL_NAMES = [
    "GOOGLE_ROUTES_FIELD_MASK",
    "GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC",
    "GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER",
    "GOOGLE_MATRIX_FIELD_MASK",
    "GOOGLE_PLACE_DETAILS_FIELD_MASK",
    "GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME",
    "HERE_ROUTING_RETURN",
    "HERE_MATRIX_ATTRIBUTES",
    "MAPBOX_MATRIX_ANNOTATIONS",
    "OSRM_TABLE_ANNOTATIONS",
    "ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING",
    "ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING",
    "ESRI_MATRIX_ACCUMULATE_ATTRIBUTES",
]


@pytest.mark.parametrize("name", ALL_NAMES)
def test_constants_are_exported_immutable_tuples_of_strings(name):
    assert name in loc.__all__
    value = getattr(loc, name)
    assert isinstance(value, tuple) and value
    assert all(isinstance(f, str) and f for f in value)


def test_constant_values():
    assert GOOGLE_ROUTES_FIELD_MASK == (
        "routes.legs.distanceMeters", "routes.legs.duration",
        "routes.distanceMeters", "routes.duration", "routes.polyline.encodedPolyline",
    )
    assert GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC == (
        "routes.legs.staticDuration", "routes.staticDuration",
    )
    assert GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER == ("routes.optimizedIntermediateWaypointIndex",)
    assert GOOGLE_MATRIX_FIELD_MASK == (
        "originIndex", "destinationIndex", "distanceMeters", "duration", "status", "condition",
    )
    assert GOOGLE_PLACE_DETAILS_FIELD_MASK == ("id", "formattedAddress", "location", "viewport")
    assert GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME == ("displayName",)
    assert HERE_ROUTING_RETURN == ("polyline", "summary")
    assert HERE_MATRIX_ATTRIBUTES == ("travelTimes", "distances")
    assert MAPBOX_MATRIX_ANNOTATIONS == ("duration", "distance")
    assert OSRM_TABLE_ANNOTATIONS == ("duration", "distance")
    assert ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING == ("TravelTime", "Kilometers")
    assert ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING == ("WalkTime", "Kilometers")
    assert ESRI_MATRIX_ACCUMULATE_ATTRIBUTES == ("Kilometers",)


# ---------------------------------------------------------------------------
# (a) the default wire value is built from the constants
# ---------------------------------------------------------------------------


def _google_route_mask(**kw) -> str:
    fake = FakeTransport(resp(200, GOOGLE_ROUTE_BODY))
    Routing(GoogleConfig("k"), transport=fake).route(RoutingOptions(**kw))
    return fake.last.headers["X-Goog-FieldMask"]


@pytest.mark.parametrize("static", [False, True])
@pytest.mark.parametrize("reorder", [
    None, "optimize", "optimize_fixed_origin", "optimize_fixed_destination", "is_round_trip",
])
def test_google_routes_mask_is_built_from_the_constants(static, reorder):
    kw = {"waypoints": THREE}
    if static:
        kw["include"] = [RoutingInclude.DURATION_WITHOUT_TRAFFIC]
    if reorder:
        kw[reorder] = True
    expected = GOOGLE_ROUTES_FIELD_MASK
    if static:
        expected += GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC
    if reorder:
        expected += GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER
    assert _google_route_mask(**kw) == ",".join(expected)


def test_google_matrix_mask_is_the_constant():
    fake = FakeTransport(resp(200, GOOGLE_MATRIX_BODY))
    Matrix(GoogleConfig("k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(0, 0)], destinations=[LatLng(1, 1)])
    )
    assert fake.last.headers["X-Goog-FieldMask"] == ",".join(GOOGLE_MATRIX_FIELD_MASK)


@pytest.mark.parametrize("wants_name", [False, True])
def test_google_place_details_mask_is_built_from_the_constants(wants_name):
    fake = FakeTransport(resp(200, GOOGLE_PLACE_BODY))
    include = [PlaceDetailsInclude.NAME] if wants_name else []
    Geocoding(GoogleConfig("k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="p1", include=include)
    )
    expected = GOOGLE_PLACE_DETAILS_FIELD_MASK
    if wants_name:
        expected += GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME
    assert fake.last.headers["X-Goog-FieldMask"] == ",".join(expected)


def test_here_routing_return_is_the_constant():
    fake = FakeTransport(resp(200, HERE_BODY))
    Routing(HereConfig("k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert qget(fake.last, "return") == ",".join(HERE_ROUTING_RETURN)


# ---------------------------------------------------------------------------
# (b) constant + one extra field sends exactly that list
# ---------------------------------------------------------------------------


def test_google_routes_append_via_constant():
    mask = ",".join(GOOGLE_ROUTES_FIELD_MASK + ("routes.travelAdvisory",))
    fake = FakeTransport(resp(200, GOOGLE_ROUTE_BODY))
    res = Routing(GoogleConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, passthrough=Passthrough(headers={"X-Goog-FieldMask": mask}))
    )
    assert fake.last.headers["X-Goog-FieldMask"] == mask
    assert res.total_distance_meters == 5000 and res.polyline == "abc"


def test_google_matrix_append_via_constant():
    mask = ",".join(GOOGLE_MATRIX_FIELD_MASK + ("travelAdvisory",))
    fake = FakeTransport(resp(200, GOOGLE_MATRIX_BODY))
    Matrix(GoogleConfig("k"), transport=fake).matrix(MatrixOptions(
        origins=[LatLng(0, 0)], destinations=[LatLng(1, 1)],
        passthrough=Passthrough(headers={"X-Goog-FieldMask": mask}),
    ))
    assert fake.last.headers["X-Goog-FieldMask"] == mask


def test_google_place_details_append_via_constant():
    mask = ",".join(GOOGLE_PLACE_DETAILS_FIELD_MASK + ("types",))
    fake = FakeTransport(resp(200, GOOGLE_PLACE_BODY))
    Geocoding(GoogleConfig("k"), transport=fake).place_details(PlaceDetailsOptions(
        place_id="p1", passthrough=Passthrough(headers={"X-Goog-FieldMask": mask}),
    ))
    assert fake.last.headers["X-Goog-FieldMask"] == mask


def test_here_routing_append_via_constant():
    ret = ",".join(HERE_ROUTING_RETURN + ("turnByTurnActions",))
    fake = FakeTransport(resp(200, HERE_BODY))
    res = Routing(HereConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, passthrough=Passthrough(query={"return": ret}))
    )
    assert qget(fake.last, "return") == ret
    assert res.total_distance_meters == 5000


# ---------------------------------------------------------------------------
# (c) a caller list fully replaces the default — deliberate, pinned
# ---------------------------------------------------------------------------


def test_google_routes_caller_mask_replaces_the_default():
    fake = FakeTransport(resp(200, GOOGLE_ROUTE_BODY))
    Routing(GoogleConfig("k"), transport=fake).route(RoutingOptions(
        waypoints=THREE, optimize=True, include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC],
        passthrough=Passthrough(headers={"X-Goog-FieldMask": "routes.duration"}),
    ))
    assert fake.last.headers["X-Goog-FieldMask"] == "routes.duration"


def test_google_matrix_caller_mask_replaces_the_default():
    fake = FakeTransport(resp(200, GOOGLE_MATRIX_BODY))
    Matrix(GoogleConfig("k"), transport=fake).matrix(MatrixOptions(
        origins=[LatLng(0, 0)], destinations=[LatLng(1, 1)],
        passthrough=Passthrough(headers={"X-Goog-FieldMask": "originIndex,destinationIndex,duration"}),
    ))
    assert fake.last.headers["X-Goog-FieldMask"] == "originIndex,destinationIndex,duration"


def test_google_place_details_caller_mask_replaces_the_default():
    fake = FakeTransport(resp(200, GOOGLE_PLACE_BODY))
    Geocoding(GoogleConfig("k"), transport=fake).place_details(PlaceDetailsOptions(
        place_id="p1", include=[PlaceDetailsInclude.NAME],
        passthrough=Passthrough(headers={"X-Goog-FieldMask": "location"}),
    ))
    assert fake.last.headers["X-Goog-FieldMask"] == "location"


def test_here_routing_caller_return_replaces_the_default():
    fake = FakeTransport(resp(200, HERE_BODY))
    Routing(HereConfig("k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, passthrough=Passthrough(query={"return": "summary"}))
    )
    assert qget(fake.last, "return") == "summary"


# ---------------------------------------------------------------------------
# matrix attribute / annotation lists (HERE, Mapbox, OSRM)
# ---------------------------------------------------------------------------

ORIGINS = [LatLng(1, 1), LatLng(2, 2)]
DESTS = [LatLng(3, 3), LatLng(4, 4)]
TABLE_BODY = '{"code":"Ok","durations":[[0,60],[60,0]],"distances":[[0,1000],[1000,0]]}'
TABLE_BODY_DURATIONS_ONLY = '{"code":"Ok","durations":[[0,60],[60,0]]}'

_HERE_RESULT_URL = "https://aws-eu-west-1.matrix.router.hereapi.com/v8/matrix/mid/result"


def _here_matrix(pt=None, result='{"travelTimes":[0,60,60,0],"distances":[0,1000,1000,0]}'):
    submit = resp(200, '{"matrixId":"mid","statusUrl":"https://matrix.router.hereapi.com/v8/matrix/mid/status"}')
    poll = resp(200, '{"matrixId":"mid","status":"completed","resultUrl":"' + _HERE_RESULT_URL + '"}')
    matrix = json.loads(result)
    matrix.update({"numOrigins": 2, "numDestinations": 2})
    retrieve = resp(200, json.dumps({"matrixId": "mid", "matrix": matrix}))
    fake = FakeTransport(submit, poll, retrieve)
    call = lambda: Matrix(HereConfig("k"), transport=fake, sleep=no_sleep).matrix(
        MatrixOptions(origins=ORIGINS, destinations=DESTS, passthrough=pt)
    )
    return fake, call


def test_here_matrix_attributes_are_the_constant():
    fake, call = _here_matrix()
    call()
    assert body_json(fake.calls[0])["matrixAttributes"] == list(HERE_MATRIX_ATTRIBUTES)


def test_here_matrix_caller_attributes_replace_the_default():
    fake, call = _here_matrix(
        Passthrough(body={"matrixAttributes": ["travelTimes"]}), '{"travelTimes":[0,60,60,0]}'
    )
    # Replaced, and the decoder needs distances too: an error, never zero-distance cells.
    with pytest.raises(ConnectorError) as ei:
        call()
    assert body_json(fake.calls[0])["matrixAttributes"] == ["travelTimes"]
    assert ei.value.provider_code == ProviderCode.UNKNOWN


@pytest.mark.parametrize("cfg,constant", [
    (MapboxConfig("tok"), MAPBOX_MATRIX_ANNOTATIONS),
    (OsrmConfig("http://x"), OSRM_TABLE_ANNOTATIONS),
])
def test_matrix_annotations_are_the_constant(cfg, constant):
    fake = FakeTransport(resp(200, TABLE_BODY))
    Matrix(cfg, transport=fake).matrix(MatrixOptions(origins=ORIGINS, destinations=DESTS))
    assert qget(fake.last, "annotations") == ",".join(constant)


# Mapbox and OSRM document only `duration` and `distance`, so there is nothing to
# append; replacement is what passthrough offers here.
@pytest.mark.parametrize("cfg", [MapboxConfig("tok"), OsrmConfig("http://x")])
def test_matrix_caller_annotations_replace_the_default(cfg):
    fake = FakeTransport(resp(200, TABLE_BODY_DURATIONS_ONLY))
    with pytest.raises(ConnectorError) as ei:
        Matrix(cfg, transport=fake).matrix(MatrixOptions(
            origins=ORIGINS, destinations=DESTS,
            passthrough=Passthrough(query={"annotations": "duration"}),
        ))
    assert qget(fake.last, "annotations") == "duration"
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# ---------------------------------------------------------------------------
# Esri accumulateAttributeNames
# ---------------------------------------------------------------------------


def _esri_route_body(time_attr="TravelTime", before=(), after=(), stops=True):
    """Stops carry Cumul_<time_attr>=10 min and Cumul_Kilometers=5 on the last stop.
    Extra attributes carry deliberately different values, placed ahead of (``before``)
    or behind (``after``) the real ones, so a shape-based pick would misread them."""
    def attrs(prefix, t, km):
        a = {}
        for name in before:
            a[f"{prefix}{name}"] = t * 7 + 1
        a[f"{prefix}{time_attr}"] = t
        a[f"{prefix}Kilometers"] = km
        for name in after:
            a[f"{prefix}{name}"] = t * 3 + 2
        return a

    body = {"routes": {"features": [{
        "attributes": attrs("Total_", 10, 5),
        "geometry": {"paths": [[[0, 0], [1, 1]]]},
    }]}}
    if stops:
        body["stops"] = {"features": [
            {"attributes": {"Sequence": 1, "Status": 0, **attrs("Cumul_", 0, 0)}},
            {"attributes": {"Sequence": 2, "Status": 0, **attrs("Cumul_", 10, 5)}},
        ]}
    return json.dumps(body)


@pytest.mark.parametrize("mode,time_attr,constant", [
    (TravelMode.DRIVING, "TravelTime", ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING),
    (TravelMode.WALKING, "WalkTime", ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING),
])
def test_esri_routing_accumulate_attributes_are_the_constant(mode, time_attr, constant):
    fake = FakeTransport(resp(200, _esri_route_body(time_attr)))
    Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO, travel_mode=mode))
    assert body_form(fake.last)["accumulateAttributeNames"] == ",".join(constant)


def test_esri_routing_append_via_constant():
    names = ",".join(ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING + ("Miles",))
    fake = FakeTransport(resp(200, _esri_route_body(after=("Miles",))))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(
        waypoints=TWO, passthrough=Passthrough(body={"accumulateAttributeNames": names}),
    ))
    assert body_form(fake.last)["accumulateAttributeNames"] == names
    assert res.legs[0].duration_seconds == 600 and res.legs[0].distance_meters == 5000


# Which fields feed the legs is fixed by the mode, not by where a caller's extra
# accumulate attributes land in the response.
@pytest.mark.parametrize("mode,time_attr,extra", [
    (TravelMode.DRIVING, "TravelTime", ("TruckTravelTime", "Miles")),
    (TravelMode.DRIVING, "TravelTime", ("Miles", "WalkTime")),
    (TravelMode.WALKING, "WalkTime", ("TravelTime", "Miles")),
])
@pytest.mark.parametrize("placement", ["before", "after"])
def test_esri_routing_reads_the_requested_fields_whatever_else_is_accumulated(mode, time_attr, extra, placement):
    body = _esri_route_body(time_attr, **{placement: extra})
    fake = FakeTransport(resp(200, body))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, travel_mode=mode)
    )
    assert res.legs[0].duration_seconds == 600 and res.legs[0].distance_meters == 5000
    assert res.total_duration_seconds == 600 and res.total_distance_meters == 5000


@pytest.mark.parametrize("mode,time_attr", [
    (TravelMode.DRIVING, "TravelTime"),
    (TravelMode.WALKING, "WalkTime"),
])
def test_esri_route_totals_fallback_reads_the_requested_time_attribute(mode, time_attr):
    # No stops -> the even-split fallback over the route's Total_* values.
    body = _esri_route_body(time_attr, before=("TruckTravelTime",), stops=False)
    fake = FakeTransport(resp(200, body))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, travel_mode=mode)
    )
    assert res.total_duration_seconds == 600 and res.total_distance_meters == 5000


def test_esri_routing_caller_list_replaces_the_default():
    fake = FakeTransport(resp(200, _esri_route_body()))
    Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(
        waypoints=TWO, passthrough=Passthrough(body={"accumulateAttributeNames": "Kilometers"}),
    ))
    assert body_form(fake.last)["accumulateAttributeNames"] == "Kilometers"


ESRI_MATRIX_BODY = (
    '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],'
    '"1":{"1":[10,20]}}}'
)


def test_esri_matrix_accumulate_attributes_are_the_constant():
    fake = FakeTransport(resp(200, ESRI_MATRIX_BODY))
    Matrix(EsriConfig(api_key="k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
    )
    assert body_form(fake.last)["accumulateAttributeNames"] == ",".join(ESRI_MATRIX_ACCUMULATE_ATTRIBUTES)


def test_esri_matrix_append_via_constant():
    names = ",".join(ESRI_MATRIX_ACCUMULATE_ATTRIBUTES + ("Miles",))
    body = (
        '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers","Miles"],'
        '"1":{"1":[10,20,12.4]}}}'
    )
    fake = FakeTransport(resp(200, body))
    res = Matrix(EsriConfig(api_key="k"), transport=fake).matrix(MatrixOptions(
        origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)],
        passthrough=Passthrough(body={"accumulateAttributeNames": names}),
    ))
    assert body_form(fake.last)["accumulateAttributeNames"] == names
    assert res.cells[0].distance_meters == 20000 and res.cells[0].duration_seconds == 600


def test_esri_matrix_caller_list_replaces_the_default():
    body = '{"odCostMatrix":{"costAttributeNames":["TravelTime"],"1":{"1":[10]}}}'
    fake = FakeTransport(resp(200, body))
    with pytest.raises(ConnectorError) as ei:
        Matrix(EsriConfig(api_key="k"), transport=fake).matrix(MatrixOptions(
            origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)],
            passthrough=Passthrough(body={"accumulateAttributeNames": "TravelTime"}),
        ))
    assert body_form(fake.last)["accumulateAttributeNames"] == "TravelTime"
    # No Kilometers column: an error, never 0 m.
    assert ei.value.provider_code == ProviderCode.UNKNOWN
