from __future__ import annotations

import json

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
    TravelMode,
)

TWO = [LatLng(1, 1), LatLng(2, 2)]


def test_routing():
    # `stops` FeatureSet in INPUT order; Sequence is the 1-based visiting position.
    body = '{"routes":{"features":[{"attributes":{"Total_Length":1000,"Total_Time":10},"geometry":{"paths":[[[-74,40],[-73,41]]]}}]},"stops":{"features":[{"attributes":{"Sequence":1}},{"attributes":{"Sequence":2}}]},"directions":[{"features":[{"attributes":{"maneuverType":"esriDMTStop","length":0,"time":0}},{"attributes":{"length":1000,"time":10}},{"attributes":{"maneuverType":"esriDMTStop","length":0,"time":0}}]}]}'
    fake = FakeTransport(resp(200, body))
    r = Routing(EsriConfig(api_key="apikey"), transport=fake)
    res = r.route(RoutingOptions(waypoints=[LatLng(40, -74), LatLng(41, -73)]))
    assert res.total_distance_meters == 1000 and res.total_duration_seconds == 600
    assert len(res.legs) == 1 and res.legs[0].distance_meters == 1000 and res.legs[0].duration_seconds == 600
    assert res.polyline != "" and res.waypoint_order == [0, 1]
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
        '"directions":[{"summary":{"totalLength":1091.226960340165,"totalTime":13.094903051108146,'
        '"totalDriveTime":13.094903051108146},'
        '"features":[{"attributes":{"maneuverType":"esriDMTStop","length":0,"time":0}},'
        '{"attributes":{"length":1091.226960340165,"time":13.094903051108146}},'
        '{"attributes":{"maneuverType":"esriDMTStop","length":0,"time":0}}]}]}'
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
    body = '{"saPolygons":{"features":[]}}'
    fake = FakeTransport(resp(200, body))
    iso = Isochrone(EsriConfig(api_key="k"), transport=fake)
    iso.isochrone(IsochroneOptions(center=LatLng(40, -74), type=IsochroneType.TIME, values=[600], travel_mode=TravelMode.WALKING))
    # ArcGIS requires a full travel-mode JSON object, not a name string.
    travel_mode = json.loads(body_form(fake.last)["travelMode"])
    assert travel_mode["type"] == "WALK"
    assert travel_mode["impedanceAttributeName"] == "WalkTime"
    assert travel_mode["name"] == "Walking Time"
