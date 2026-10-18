from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest
from helpers import FakeTransport, body_form, qget, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    EsriConfig,
    Geocoding,
    GeocodeOptions,
    Isochrone,
    IsochroneOptions,
    IsochroneType,
    LatLng,
    Matrix,
    MatrixOptions,
    ProviderCode,
    Routing,
    RoutingOptions,
    ReverseGeocodeOptions,
    TrafficMode,
    TravelMode,
)

TWO = [LatLng(1, 1), LatLng(2, 2)]


# A minimal valid service-area answer, for tests that only inspect the request.
_SA_OK = '{"saPolygons":{"features":[{"attributes":{"ToBreak":10},"geometry":{"rings":[[[-74,40],[-73,40],[-74,40]]]}}]}}'


def test_routing():
    # `stops` FeatureSet in INPUT order; Sequence is the 1-based visiting position.
    # Legs come from the stops FeatureSet's cumulative costs; no directions output.
    body = json.dumps(
        {
            "routes": {"features": [{"attributes": {"Total_TravelTime": 10, "Total_Kilometers": 1}, "geometry": {"paths": [[[-74, 40], [-73, 41]]]}}]},
            "stops": {"features": [
                {"attributes": {"Sequence": 1, "Status": 0, "Cumul_TravelTime": 0, "Cumul_Kilometers": 0}},
                {"attributes": {"Sequence": 2, "Status": 0, "Cumul_TravelTime": 10, "Cumul_Kilometers": 1}},
            ]},
        }
    )
    fake = FakeTransport(resp(200, body))
    r = Routing(EsriConfig(api_key="apikey"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)]))
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 600
    assert len(res.legs) == 1 and res.legs[0].distance_meters == 1000 and res.legs[0].duration_seconds == 600
    # waypoint_order is surfaced for OPTIMIZED routes only: stops are always requested,
    # so an unoptimized route must report no ordering, not an identity permutation.
    assert res.polyline != "" and res.waypoint_order is None
    assert body_form(fake.last)["token"] == "apikey"


def test_routing_body_error():
    fake = FakeTransport(resp(200, '{"error":{"code":498,"message":"Invalid token"}}'))
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.AUTH_FAILED and ei.value.provider_message == "Invalid token"


def test_dual_auth():
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="a", arcgis_token="b"), transport=FakeTransport()).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    with pytest.raises(ConnectorError) as ei2:
        Routing(EsriConfig(), transport=FakeTransport()).route(RoutingOptions(waypoints=TWO))
    assert ei2.value.provider_code == ProviderCode.AUTH_FAILED


def test_routing_rejects_cycling():
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="k"), transport=FakeTransport()).route(RoutingOptions(waypoints=TWO, travel_mode=TravelMode.CYCLING))
    assert ei.value.provider_code == ProviderCode.UNSUPPORTED_TRAVEL_MODE


def test_routing_walking_sends_full_travel_mode_object():
    body = '{"routes":{"features":[{"attributes":{"Total_Length":1,"Total_Time":1},"geometry":{"paths":[[[-74,40],[-73,41]]]}}]}}'
    fake = FakeTransport(resp(200, body))
    Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)], travel_mode=TravelMode.WALKING)
    )
    # ArcGIS requires a full travel-mode JSON object, not a name string.
    travel_mode = json.loads(body_form(fake.last)["travelMode"])
    assert travel_mode["type"] == "WALK"
    assert travel_mode["impedanceAttributeName"] == "WalkTime"
    assert travel_mode["name"] == "Walking Time"


def test_routing_walking_totals_from_directions_summary():
    # Real ArcGIS walking response: no Total_Time / Total_Length; the impedance
    # attribute is Total_WalkTime, and reliable totals live in
    # directions[0].summary (meters + minutes). The pre-fix connector read
    # Total_Time first and reported duration 0. Live values (2026-07-21).
    body = (
        '{"routes":{"features":[{"attributes":{"Total_WalkTime":13.094903051108146,'
        '"Total_Kilometers":1.091226960340165,"Total_Miles":0.678},'
        '"geometry":{"paths":[[[-74,40],[-73,41]]]}}]},'
        '"stops":{"features":['
        '{"attributes":{"Sequence":1,"Status":0,"Cumul_WalkTime":0,"Cumul_Kilometers":0}},'
        '{"attributes":{"Sequence":2,"Status":0,"Cumul_WalkTime":13.094903051108146,"Cumul_Kilometers":1.091226960340165}}]}}'
    )
    fake = FakeTransport(resp(200, body))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)], travel_mode=TravelMode.WALKING)
    )
    assert abs(res.total_distance_meters - 1091.226960340165) < 0.1
    assert abs(res.total_duration_seconds - 13.094903051108146 * 60) < 0.001


# Real esriNAODOutputSparseMatrix shape: odCostMatrix keyed by 1-based origin OID,
# each mapping 1-based dest OID -> [values in costAttributeNames order].
# Cell (1,1) uses the LIVE-verified NYC->Bridgeport sample (TravelTime min, Kilometers km).
def test_matrix_sparse():
    body = (
        '{"requestID":"abc","odCostMatrix":{'
        '"costAttributeNames":["TravelTime","Kilometers"],'
        '"1":{"1":[93.25787017375364,98.94833503121721],"2":[10,20]},'
        '"2":{"1":[30,40],"2":[5,6]}},"messages":[]}'
    )
    fake = FakeTransport(resp(200, body))
    m = Matrix(EsriConfig(api_key="k"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert len(res.cells) == 4
    c00 = next(c for c in res.cells if c.origin_index == 0 and c.destination_index == 0)
    assert c00.distance_meters == 98.94833503121721 * 1000
    assert c00.duration_seconds == 93.25787017375364 * 60
    c11 = next(c for c in res.cells if c.origin_index == 1 and c.destination_index == 1)
    assert c11.distance_meters == 6000 and c11.duration_seconds == 300
    form = body_form(fake.last)
    assert form["impedanceAttributeName"] == "TravelTime"
    assert form["accumulateAttributeNames"] == "Kilometers"
    assert form["outputType"] == "esriNAODOutputSparseMatrix"


def test_matrix_walking_decodes_walktime_impedance():
    # A WALK travel mode makes ArcGIS override the impedance, so
    # costAttributeNames comes back as ["WalkTime","Kilometers"] — NOT
    # "TravelTime". The pre-fix decoder looked up "TravelTime" only and silently
    # reported every duration as 0. Live values (2026-07-21).
    body = '{"odCostMatrix":{"costAttributeNames":["WalkTime","Kilometers"],"1":{"1":[13.094903051108146,1.091226960340165]}}}'
    fake = FakeTransport(resp(200, body))
    res = Matrix(EsriConfig(api_key="k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(0, 0)], destinations=[LatLng(1, 1)], travel_mode=TravelMode.WALKING)
    )
    assert len(res.cells) == 1
    assert abs(res.cells[0].duration_seconds - 13.094903051108146 * 60) < 1e-6
    assert abs(res.cells[0].distance_meters - 1.091226960340165 * 1000) < 1e-6
    # request carries the full walking travel-mode object.
    travel_mode = json.loads(body_form(fake.last)["travelMode"])
    assert travel_mode["type"] == "WALK"


# Real esriNAODOutputStraightLines fallback: odLines.features[].attributes with
# OriginID/DestinationID + Total_TravelTime (min) / Total_Kilometers (km).
def test_matrix_odlines():
    body = (
        '{"odLines":{"features":['
        '{"attributes":{"ObjectID":1,"OriginID":1,"DestinationID":1,"DestinationRank":1,'
        '"Total_TravelTime":93.25787017375364,"Total_Kilometers":98.94833503121721,"Shape_Length":0.9}},'
        '{"attributes":{"ObjectID":2,"OriginID":1,"DestinationID":2,"Total_TravelTime":10,"Total_Kilometers":20}},'
        '{"attributes":{"ObjectID":3,"OriginID":2,"DestinationID":1,"Total_TravelTime":30,"Total_Kilometers":40}},'
        '{"attributes":{"ObjectID":4,"OriginID":2,"DestinationID":2,"Total_TravelTime":5,"Total_Kilometers":6}}'
        ']}}'
    )
    fake = FakeTransport(resp(200, body))
    m = Matrix(EsriConfig(api_key="k"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3), LatLng(4, 4)]))
    assert len(res.cells) == 4
    c00 = next(c for c in res.cells if c.origin_index == 0 and c.destination_index == 0)
    assert c00.distance_meters == 98.94833503121721 * 1000
    assert c00.duration_seconds == 93.25787017375364 * 60


def test_matrix_sparse_omits_unroutable_pairs():
    # Origin OID 2 is unroutable and omitted by Esri. A sparse result is returned
    # as-is (indexed cells), not an error for the whole matrix.
    body = '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],"1":{"1":[5,10]}}}'
    fake = FakeTransport(resp(200, body))
    m = Matrix(EsriConfig(api_key="k"), transport=fake)
    res = m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3)]))
    assert len(res.cells) == 1
    assert res.cells[0].origin_index == 0 and res.cells[0].destination_index == 0


def test_matrix_out_of_range_oid():
    # origin OID 3 with only 2 origins requested -> reject
    body = '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],"3":{"1":[5,10]}}}'
    fake = FakeTransport(resp(200, body))
    m = Matrix(EsriConfig(api_key="k"), transport=fake)
    with pytest.raises(ConnectorError) as ei:
        m.matrix(MatrixOptions(origins=[LatLng(1, 1), LatLng(2, 2)], destinations=[LatLng(3, 3)]))
    assert ei.value.provider_code == ProviderCode.UNKNOWN


def test_geocode():
    fake = FakeTransport(resp(200, '{"candidates":[{"address":"New York, NY","location":{"x":-74,"y":40},"extent":{"xmin":-74.3,"ymin":40.4,"xmax":-73.7,"ymax":40.9}}]}'))
    g = Geocoding(EsriConfig(api_key="apikey"), transport=fake)
    res = g.geocode(GeocodeOptions(address="NYC"))
    c = res.candidates[0]
    assert c.formatted_address == "New York, NY" and c.location.lat == 40 and c.location.lng == -74
    assert c.viewport.southwest.lat == 40.4 and c.viewport.northeast.lng == -73.7
    assert qget(fake.last, "token") == "apikey"


def test_reverse_geocode():
    fake = FakeTransport(resp(200, '{"address":{"LongLabel":"123 Main St, NY"},"location":{"x":-74,"y":40}}'))
    g = Geocoding(EsriConfig(api_key="k"), transport=fake)
    res = g.reverse_geocode(ReverseGeocodeOptions(location=LatLng(40, -74)))
    assert res.candidates[0].formatted_address == "123 Main St, NY"

    empty = Geocoding(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, "{}")))
    res2 = empty.reverse_geocode(ReverseGeocodeOptions(location=LatLng(1, 1)))
    assert res2.candidates == []


def test_autocomplete():
    fake = FakeTransport(resp(200, '{"suggestions":[{"text":"New York","magicKey":"mk1"}]}'))
    g = Geocoding(EsriConfig(api_key="k"), transport=fake)
    res = g.autocomplete(AutocompleteOptions(input="New"))
    assert res.predictions[0].description == "New York" and res.predictions[0].place_id == "mk1"


def test_isochrone():
    body = '{"saPolygons":{"features":[{"attributes":{"FromBreak":0,"ToBreak":10},"geometry":{"rings":[[[-74,40],[-73,40],[-73,41],[-74,40]]]}}]}}'
    fake = FakeTransport(resp(200, body))
    iso = Isochrone(EsriConfig(api_key="k"), transport=fake)
    res = iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600]))
    assert res.contours[0].value == 600
    assert len(res.contours[0].geometry.coordinates[0]) == 4
    assert body_form(fake.last)["defaultBreaks"] == "10"


def test_isochrone_walking_sends_full_travel_mode_object():
    body = _SA_OK
    fake = FakeTransport(resp(200, body))
    iso = Isochrone(EsriConfig(api_key="k"), transport=fake)
    iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600], travel_mode=TravelMode.WALKING))
    # ArcGIS requires a full travel-mode JSON object, not a name string.
    travel_mode = json.loads(body_form(fake.last)["travelMode"])
    assert travel_mode["type"] == "WALK"
    assert travel_mode["impedanceAttributeName"] == "WalkTime"
    assert travel_mode["name"] == "Walking Time"




def test_routing_derives_legs_and_totals_from_cumulative_stop_costs():
    """Legs AND totals from one source, so they cannot disagree — replaces parsing the
    legacy (superseded) directions output."""
    body = json.dumps(
        {
            "routes": {"features": [{"attributes": {"Total_TravelTime": 30, "Total_Kilometers": 12}, "geometry": {"paths": [[[0, 0], [1, 1]]]}}]},
            "stops": {"features": [
                {"attributes": {"Sequence": 1, "Status": 0, "Cumul_TravelTime": 0, "Cumul_Kilometers": 0}},
                {"attributes": {"Sequence": 2, "Status": 0, "Cumul_TravelTime": 10, "Cumul_Kilometers": 4}},
                {"attributes": {"Sequence": 3, "Status": 0, "Cumul_TravelTime": 30, "Cumul_Kilometers": 12}},
            ]},
        }
    )
    fake = FakeTransport(resp(200, body))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(0, 0), LatLng(1, 1), LatLng(2, 2)])
    )
    assert [(l.distance_meters, l.duration_seconds) for l in res.legs] == [(4000, 600), (8000, 1200)]
    assert res.total_distance_meters == 12000 and res.total_duration_seconds == 1800
    # The invariant that is now structural rather than coincidental.
    assert sum(l.distance_meters for l in res.legs) == res.total_distance_meters
    assert sum(l.duration_seconds for l in res.legs) == res.total_duration_seconds

    form = body_form(fake.last)
    assert form["returnStops"] == "true"
    # Explicit false is load-bearing: the SERVICE DEFAULT is true.
    assert form["returnDirections"] == "false"
    assert form["accumulateAttributeNames"] == "TravelTime,Kilometers"
    assert form["outputLines"] == "esriNAOutputLineTrueShape"
    assert "directionsOutputType" not in form


def test_routing_differences_stops_in_visit_order():
    """Stops come back in INPUT order while cumulative costs run along the route; with
    findBestSequence those differ, and skipping the sort yields negative legs."""
    body = json.dumps(
        {
            "routes": {"features": [{"attributes": {"Total_TravelTime": 30, "Total_Kilometers": 12}, "geometry": {"paths": [[[0, 0], [1, 1]]]}}]},
            "stops": {"features": [
                {"attributes": {"Sequence": 1, "Status": 0, "Cumul_TravelTime": 0, "Cumul_Kilometers": 0}},
                {"attributes": {"Sequence": 3, "Status": 0, "Cumul_TravelTime": 30, "Cumul_Kilometers": 12}},
                {"attributes": {"Sequence": 2, "Status": 0, "Cumul_TravelTime": 10, "Cumul_Kilometers": 4}},
            ]},
        }
    )
    fake = FakeTransport(resp(200, body))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(0, 0), LatLng(1, 1), LatLng(2, 2)], optimize=True)
    )
    assert [l.distance_meters for l in res.legs] == [4000, 8000]


def test_routing_falls_back_when_a_stop_failed_to_locate():
    """A non-zero Status carries no usable cumulative cost, so every later difference
    would be wrong; prefer the route's own totals plus an even split."""
    body = json.dumps(
        {
            "routes": {"features": [{"attributes": {"Total_TravelTime": 20, "Total_Kilometers": 1}, "geometry": {"paths": [[[0, 0], [1, 1]]]}}]},
            "stops": {"features": [
                {"attributes": {"Sequence": 1, "Status": 0, "Cumul_TravelTime": 0, "Cumul_Kilometers": 0}},
                {"attributes": {"Sequence": 2, "Status": 7, "Cumul_TravelTime": 20, "Cumul_Kilometers": 1}},
            ]},
        }
    )
    fake = FakeTransport(resp(200, body))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=[LatLng(0, 0), LatLng(1, 1)])
    )
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 1200
    assert len(res.legs) == 1 and res.legs[0].distance_meters == 1000


# ---- departure time (Route startTime / OD + ServiceArea timeOfDay) ----

_ROUTE_BODY = '{"routes":{"features":[{"attributes":{"Total_TravelTime":1,"Total_Kilometers":1},"geometry":{"paths":[[[0,0],[1,1]]]}}]}}'
_DEPARTURE = dt.datetime(2026, 8, 7, 3, 6, tzinfo=dt.timezone.utc)
_DEPARTURE_MS = "1786071960000"


def test_routing_start_time_is_flagged_as_utc():
    """startTimeIsUTC defaults to false, which reads startTime in the first stop's
    local time zone — wrong for an absolute epoch instant."""
    fake = FakeTransport(resp(200, _ROUTE_BODY))
    Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, departure_time=_DEPARTURE, traffic_mode=TrafficMode.TRAFFIC)
    )
    form = body_form(fake.last)
    assert form["startTime"] == _DEPARTURE_MS
    assert form["startTimeIsUTC"] == "true"


def test_routing_without_departure_sends_no_time_fields():
    fake = FakeTransport(resp(200, _ROUTE_BODY))
    Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    form = body_form(fake.last)
    assert "startTime" not in form and "startTimeIsUTC" not in form


def test_matrix_departure_is_time_of_day_not_start_time():
    """solveODCostMatrix has no startTime parameter; it takes timeOfDay +
    timeOfDayIsUTC (default false)."""
    body = '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],"1":{"1":[1,1]}}}'
    fake = FakeTransport(resp(200, body))
    Matrix(EsriConfig(api_key="k"), transport=fake).matrix(
        MatrixOptions(
            origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], departure_time=_DEPARTURE,
            traffic_mode=TrafficMode.TRAFFIC,
        )
    )
    form = body_form(fake.last)
    assert form["timeOfDay"] == _DEPARTURE_MS
    assert form["timeOfDayIsUTC"] == "true"
    assert "startTime" not in form


def test_isochrone_time_of_day_is_flagged_as_utc():
    fake = FakeTransport(resp(200, _SA_OK))
    Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600], departure_time="2026-08-07T03:06:00Z")
    )
    form = body_form(fake.last)
    assert form["timeOfDay"] == _DEPARTURE_MS
    assert form["timeOfDayIsUTC"] == "true"


@pytest.mark.parametrize("call", [
    lambda fake: Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO, departure_time="not-a-date")),
    lambda fake: Matrix(EsriConfig(api_key="k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], departure_time="next tuesday")
    ),
    lambda fake: Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=[600], departure_time="tomorrow")
    ),
])
def test_unparseable_departure_is_invalid_request_before_any_request(call):
    fake = FakeTransport()
    with pytest.raises(ConnectorError) as ei:
        call(fake)
    assert ei.value.provider_code == ProviderCode.INVALID_REQUEST
    assert fake.calls == []


# ---- unlocatable stops ----


def test_routing_fails_on_invalid_locations_instead_of_skipping_them():
    """ignoreInvalidLocations defaults to true ("unlocated will be ignored"), which
    returns a route that silently skips a requested stop."""
    fake = FakeTransport(resp(200, _ROUTE_BODY))
    Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert body_form(fake.last)["ignoreInvalidLocations"] == "false"


def test_routing_unlocated_stop_failure_is_no_route():
    body = '{"error":{"code":400,"message":"Unable to complete operation.","details":["Location \\"Location 2\\" in \\"Stops\\" is unlocated.","Need at least 2 valid stops."]}}'
    fake = FakeTransport(resp(200, body))
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.NO_ROUTE


def test_matrix_and_isochrone_do_not_send_ignore_invalid_locations():
    fake = FakeTransport(resp(200, '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],"1":{"1":[1,1]}}}'))
    Matrix(EsriConfig(api_key="k"), transport=fake).matrix(MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)]))
    assert "ignoreInvalidLocations" not in body_form(fake.last)
    fake2 = FakeTransport(resp(200, _SA_OK))
    Isochrone(EsriConfig(api_key="k"), transport=fake2).isochrone(
        IsochroneOptions(center=LatLng(1, 1), type=IsochroneType.TIME, values=[600])
    )
    assert "ignoreInvalidLocations" not in body_form(fake2.last)


# ---- service-area breaks: impedance units + polygon detail ----


def test_isochrone_time_breaks_are_minutes_under_the_default_impedance():
    fake = FakeTransport(resp(200, _SA_OK))
    Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[300, 600])
    )
    form = body_form(fake.last)
    assert form["defaultBreaks"] == "5,10"
    # solveServiceArea has no breakUnits parameter; breaks follow the impedance.
    assert "breakUnits" not in form
    assert "impedanceAttributeName" not in form


def test_isochrone_driving_distance_uses_kilometers_impedance():
    """defaultBreaks follow the impedance attribute (TravelTime minutes by
    default), so a distance isochrone switches to Kilometers and sends km."""
    body = '{"saPolygons":{"features":[{"attributes":{"FromBreak":0,"ToBreak":5},"geometry":{"rings":[[[-74,40],[-73,40],[-73,41],[-74,40]]]}},{"attributes":{"FromBreak":0,"ToBreak":0.3},"geometry":{"rings":[[[-74,40],[-73,40],[-74,40]]]}}]}}'
    fake = FakeTransport(resp(200, body))
    res = Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.DISTANCE, values=[5000, 300])
    )
    form = body_form(fake.last)
    assert form["defaultBreaks"] == "5,0.3"
    assert form["impedanceAttributeName"] == "Kilometers"
    assert "breakUnits" not in form and "travelMode" not in form
    assert [c.value for c in res.contours] == [300, 5000]


def test_isochrone_walking_distance_sets_kilometers_inside_the_travel_mode():
    """A travelMode overrides impedanceAttributeName, so the distance impedance must
    live inside the walking travel-mode object."""
    fake = FakeTransport(resp(200, _SA_OK))
    Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.DISTANCE, values=[2000], travel_mode=TravelMode.WALKING)
    )
    form = body_form(fake.last)
    travel_mode = json.loads(form["travelMode"])
    assert travel_mode["type"] == "WALK"
    assert travel_mode["impedanceAttributeName"] == "Kilometers"
    assert travel_mode["distanceAttributeName"] == "Kilometers"
    assert travel_mode["name"] == "Walking Distance"
    assert travel_mode["id"] == "yFuMFwIYblqKEefX"
    assert form["defaultBreaks"] == "2"
    assert "breakUnits" not in form


@pytest.mark.parametrize("kind,values,expected", [
    (IsochroneType.TIME, [600], "esriNAOutputPolygonDetailed"),
    (IsochroneType.TIME, [900], "esriNAOutputPolygonDetailed"),
    (IsochroneType.TIME, [600, 1800], "esriNAOutputPolygonSimplified"),
    (IsochroneType.TIME, [1800], "esriNAOutputPolygonSimplified"),
    (IsochroneType.DISTANCE, [24140], "esriNAOutputPolygonDetailed"),
    (IsochroneType.DISTANCE, [30000], "esriNAOutputPolygonSimplified"),
])
def test_isochrone_detailed_polygons_only_within_the_15_minute_15_mile_cap(kind, values, expected):
    fake = FakeTransport(resp(200, _SA_OK))
    Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=kind, values=values)
    )
    assert body_form(fake.last)["outputPolygons"] == expected


def _esri_route_body(sequences):
    """A solve response whose `stops` carry the given 1-based Sequence per INPUT stop."""
    n = len(sequences)
    return json.dumps(
        {
            "routes": {"features": [{"attributes": {"Total_TravelTime": n, "Total_Kilometers": n}, "geometry": {"paths": [[[0, 0], [1, 1]]]}}]},
            "stops": {"features": [
                {"attributes": {"Sequence": seq, "Status": 0, "Cumul_TravelTime": seq - 1, "Cumul_Kilometers": seq - 1}}
                for seq in sequences
            ]},
        }
    )


FOUR = [LatLng(0, 0), LatLng(1, 1), LatLng(2, 2), LatLng(3, 3)]


@pytest.mark.parametrize("flags,first,last", [
    # findBestSequence defaults BOTH preserve flags to true (live: order [0,1,2,3]
    # with defaults, [2,3,0,1] with both false), so each is sent explicitly.
    ({"optimize": True}, "true", "true"),
    ({"optimize": True, "optimize_fixed_origin": True}, "true", "false"),
    ({"optimize": True, "optimize_fixed_destination": True}, "false", "true"),
    ({"optimize": True, "optimize_fixed_origin": True, "optimize_fixed_destination": True}, "true", "true"),
    # A fixed-endpoint flag alone also means "reorder".
    ({"optimize_fixed_origin": True}, "true", "false"),
    ({"optimize_fixed_destination": True}, "false", "true"),
])
def test_optimize_flags_map_to_find_best_sequence_and_preserve_stops(flags, first, last):
    fake = FakeTransport(resp(200, _esri_route_body([1, 4, 2, 3])))
    res = Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=FOUR, **flags))
    form = body_form(fake.last)
    assert form["findBestSequence"] == "true"
    assert (form["preserveFirstStop"], form["preserveLastStop"]) == (first, last)
    assert res.waypoint_order == [0, 2, 3, 1]


def test_no_optimize_flag_sends_no_sequence_params():
    fake = FakeTransport(resp(200, _esri_route_body([1, 2])))
    Routing(EsriConfig(api_key="k"), transport=fake).route(RoutingOptions(waypoints=TWO))
    form = body_form(fake.last)
    assert "findBestSequence" not in form and "preserveFirstStop" not in form and "preserveLastStop" not in form


def test_walking_avoid_restrictions_go_inside_the_travel_mode():
    # Live: walking travelMode + a top-level restrictionAttributeNames=Avoid Ferries
    # → Esri reported only the mode's own "Avoid Private Roads" (ours ignored);
    # appended to the travel mode's own list → "Avoid Ferries" honoured.
    fake = FakeTransport(resp(200, _esri_route_body([1, 2])))
    Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, travel_mode=TravelMode.WALKING, avoid_ferries=True, avoid_tolls=True)
    )
    form = body_form(fake.last)
    assert "restrictionAttributeNames" not in form
    names = json.loads(form["travelMode"])["restrictionAttributeNames"]
    assert names == [
        "Avoid Private Roads",
        "Avoid Roads Unsuitable for Pedestrians",
        "Preferred for Pedestrians",
        "Walking",
        "Avoid Toll Roads",
        "Avoid Ferries",
    ]


def test_walking_travel_mode_restrictions_are_deduplicated_and_not_mutated():
    from thinwrap.location._esri import _esri_apply_travel_mode_and_restrictions, _esri_walking_travel_mode

    form: dict = {}
    _esri_apply_travel_mode_and_restrictions(form, TravelMode.WALKING, ["Walking", "Avoid Ferries"], "t")
    names = json.loads(form["travelMode"])["restrictionAttributeNames"]
    assert names.count("Walking") == 1 and names[-1] == "Avoid Ferries"
    assert "Avoid Ferries" not in _esri_walking_travel_mode()["restrictionAttributeNames"]


def test_driving_avoid_restrictions_use_the_top_level_parameter():
    # Driving sends no travelMode (service default), where the top-level parameter
    # is honoured (live: the route changes).
    fake = FakeTransport(resp(200, _esri_route_body([1, 2])))
    Routing(EsriConfig(api_key="k"), transport=fake).route(
        RoutingOptions(waypoints=TWO, avoid_tolls=True, avoid_ferries=True, avoid_highways=True)
    )
    form = body_form(fake.last)
    assert "travelMode" not in form
    assert form["restrictionAttributeNames"] == "Avoid Toll Roads,Avoid Ferries,Avoid Limited Access Roads"


def test_walking_matrix_avoid_tolls_goes_inside_the_travel_mode():
    body = '{"odCostMatrix":{"costAttributeNames":["WalkTime","Kilometers"],"1":{"1":[13.0,1.0]}}}'
    fake = FakeTransport(resp(200, body))
    Matrix(EsriConfig(api_key="k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], travel_mode=TravelMode.WALKING, avoid_tolls=True)
    )
    form = body_form(fake.last)
    assert "restrictionAttributeNames" not in form
    assert "Avoid Toll Roads" in json.loads(form["travelMode"])["restrictionAttributeNames"]


def test_driving_matrix_avoid_tolls_uses_the_top_level_parameter():
    body = '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],"1":{"1":[5,10]}}}'
    fake = FakeTransport(resp(200, body))
    Matrix(EsriConfig(api_key="k"), transport=fake).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], avoid_tolls=True)
    )
    assert body_form(fake.last)["restrictionAttributeNames"] == "Avoid Toll Roads"


@pytest.mark.parametrize("columns", [["BikeTime", "Kilometers"], ["TravelTime", "Miles"], ["Minutes"]])
def test_matrix_with_an_unrecognised_cost_column_is_unknown_not_zero(columns):
    body = json.dumps({"odCostMatrix": {"costAttributeNames": columns, "1": {"1": [5, 10]}}})
    with pytest.raises(ConnectorError) as ei:
        Matrix(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).matrix(
            MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN
    assert ei.value.cause == {"costAttributeNames": columns}


def test_config_repr_hides_credentials():
    assert "esri-key" not in repr(EsriConfig(api_key="esri-key"))
    assert "esri-token" not in repr(EsriConfig(arcgis_token="esri-token"))


# --- routing: nothing readable is an error, never a 0 m / 0 s route -------------

@pytest.mark.parametrize("attributes,paths", [
    ({"Name": "x"}, [[[0, 0], [1, 1]]]),
    ({"Total_TravelTime": 1, "Total_Kilometers": 1}, [[[0, None], [1, 1]]]),
    ({"Total_TravelTime": 1, "Total_Kilometers": 1}, [[[0]]]),
])
def test_routing_unreadable_totals_or_path_points_are_unknown(attributes, paths):
    body = json.dumps({"routes": {"features": [{"attributes": attributes, "geometry": {"paths": paths}}]}})
    with pytest.raises(ConnectorError) as ei:
        Routing(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).route(RoutingOptions(waypoints=TWO))
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# --- matrix: failed cells are omitted on both output shapes ---------------------

def test_sparse_matrix_cell_missing_a_cost_is_omitted():
    body = '{"odCostMatrix":{"costAttributeNames":["TravelTime","Kilometers"],"1":{"1":[5,2],"2":[null,3]}}}'
    res = Matrix(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2), LatLng(3, 3)])
    )
    assert [(c.destination_index, c.duration_seconds) for c in res.cells] == [(0, 300)]


def test_od_lines_walking_reads_total_walk_time():
    body = '{"odLines":{"features":[{"attributes":{"OriginID":1,"DestinationID":1,"Total_WalkTime":12,"Total_Kilometers":1}}]}}'
    res = Matrix(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).matrix(
        MatrixOptions(origins=[LatLng(1, 1)], destinations=[LatLng(2, 2)], travel_mode=TravelMode.WALKING)
    )
    assert res.cells[0].duration_seconds == 720


def test_od_lines_sparse_answer_omits_missing_pairs_and_costs():
    body = (
        '{"odLines":{"features":['
        '{"attributes":{"OriginID":1,"DestinationID":1,"Total_TravelTime":1,"Total_Kilometers":2}},'
        '{"attributes":{"OriginID":2,"DestinationID":1,"Total_Kilometers":4}}'
        "]}}"
    )
    res = Matrix(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).matrix(
        MatrixOptions(origins=[LatLng(1, 1), LatLng(1, 2)], destinations=[LatLng(2, 2), LatLng(3, 3)])
    )
    # (0,1) and (1,1) absent from the answer, (1,0) without a time: omitted, no error.
    assert [(c.origin_index, c.destination_index) for c in res.cells] == [(0, 0)]


@pytest.mark.parametrize("oid,did", [(3, 1), (1, 9), (1.5, 1), ("x", 1), (0, 1)])
def test_od_lines_out_of_range_or_non_integer_oid_is_unknown(oid, did):
    body = json.dumps({"odLines": {"features": [{"attributes": {"OriginID": oid, "DestinationID": did, "Total_TravelTime": 1, "Total_Kilometers": 1}}]}})
    with pytest.raises(ConnectorError) as ei:
        Matrix(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).matrix(
            MatrixOptions(origins=[LatLng(1, 1), LatLng(1, 2)], destinations=[LatLng(2, 2)])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN


# --- isochrone ------------------------------------------------------------------

def test_isochrone_contour_value_is_the_callers_not_the_minute_round_trip():
    body = '{"saPolygons":{"features":[{"attributes":{"ToBreak":0.116667},"geometry":{"rings":[[[-74,40],[-73,40],[-74,40]]]}}]}}'
    fake = FakeTransport(resp(200, body))
    res = Isochrone(EsriConfig(api_key="k"), transport=fake).isochrone(
        IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[7])
    )
    assert body_form(fake.last)["defaultBreaks"] == "0.116667"
    assert res.contours[0].value == 7


@pytest.mark.parametrize("body", [
    '{"saPolygons":{"features":[]}}',
    '{"saPolygons":{"features":[{"attributes":{},"geometry":{"rings":[[[-74,40],[-73,40],[-74,40]]]}}]}}',
    '{"saPolygons":{"features":[{"attributes":{"ToBreak":10},"geometry":{"rings":[]}}]}}',
    '{"saPolygons":{"features":[{"attributes":{"ToBreak":10},"geometry":{"rings":[[[-74]]]}}]}}',
])
def test_isochrone_without_a_usable_polygon_is_unknown(body):
    with pytest.raises(ConnectorError) as ei:
        Isochrone(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).isochrone(
            IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600])
        )
    assert ei.value.provider_code == ProviderCode.UNKNOWN
