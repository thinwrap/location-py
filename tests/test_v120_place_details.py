"""Cross-provider contract for ``place_details`` and ``structured_format``.

One operation, not two: "place details" and "geocode by place id" are the same
vendor call on all five providers, so the result is a full ``GeocodeCandidate``
rather than a new shape.

Every endpoint here was live-probed before implementation. The Esri case is the one
worth reading twice: the docs pair ``magicKey`` with the original search text, and
probing showed the key alone resolves to the byte-identical candidate — so
``place_id`` needs no companion field.
"""

from __future__ import annotations

import json

import pytest
from helpers import FakeTransport, qget, resp

from thinwrap.location import (
    AutocompleteOptions,
    ConnectorError,
    EsriConfig,
    Geocoding,
    GoogleConfig,
    HereConfig,
    LatLng,
    MapboxConfig,
    PlaceDetailsInclude,
    PlaceDetailsOptions,
    ProviderCode,
    TomTomConfig,
)

GOOGLE_BODY = json.dumps({
    "id": "ChIJ1",
    "formattedAddress": "66 Mint St, San Francisco",
    "location": {"latitude": 37.7825, "longitude": -122.4059},
    "viewport": {
        "low": {"latitude": 37.78, "longitude": -122.41},
        "high": {"latitude": 37.79, "longitude": -122.40},
    },
})
HERE_BODY = json.dumps({
    "title": "Brandenburger Tor",
    "id": "here:pds:place:1",
    "address": {"label": "Pariser Platz, Berlin"},
    "position": {"lat": 52.5163, "lng": 13.3777},
})
MAPBOX_BODY = json.dumps({"features": [{
    "geometry": {"coordinates": [-122.4059, 37.7825]},
    "properties": {"mapbox_id": "mb1", "full_address": "66 Mint St", "name": "Blue Bottle"},
}]})
TOMTOM_BODY = json.dumps({"results": [{
    "id": "tt1",
    "address": {"freeformAddress": "Museumstraat 1"},
    "position": {"lat": 52.36, "lon": 4.885},
}]})
ESRI_BODY = json.dumps({"candidates": [{
    "address": "Museumstraat 1",
    "location": {"x": 4.885, "y": 52.36},
}]})


# ---------------------------------------------------------------------------
# endpoints and normalization
# ---------------------------------------------------------------------------


def test_google_gets_places_by_id():
    fake = FakeTransport(resp(200, GOOGLE_BODY))
    result = Geocoding(GoogleConfig("k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="ChIJ1")
    )
    assert "/v1/places/ChIJ1" in fake.last.url
    assert result.candidate.location.lat == 37.7825
    assert result.candidate.location.lng == -122.4059
    assert result.candidate.place_id == "ChIJ1"
    assert result.candidate.viewport is not None
    assert result.candidate.viewport.southwest.lat == 37.78


def test_here_gets_lookup_by_id():
    fake = FakeTransport(resp(200, HERE_BODY))
    result = Geocoding(HereConfig("k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="here:pds:place:1")
    )
    assert "lookup.search.hereapi.com/v1/lookup" in fake.last.url
    assert qget(fake.last, "id") == "here:pds:place:1"
    assert result.candidate.location.lat == 52.5163


def test_mapbox_gets_searchbox_retrieve():
    fake = FakeTransport(resp(200, MAPBOX_BODY))
    result = Geocoding(MapboxConfig("pk"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="mb1")
    )
    assert "/search/searchbox/v1/retrieve/mb1" in fake.last.url
    assert result.candidate.location.lat == 37.7825


def test_tomtom_gets_place_by_entity_id():
    fake = FakeTransport(resp(200, TOMTOM_BODY))
    result = Geocoding(TomTomConfig("k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="tt1")
    )
    assert "/search/2/place.json" in fake.last.url
    assert qget(fake.last, "entityId") == "tt1"
    assert result.candidate.location.lat == 52.36


# Live-verified: magicKey ALONE resolves. The docs pair it with SingleLine; the probe
# showed that is unnecessary, which is why place_id needs no companion field.
def test_esri_sends_magic_key_alone():
    fake = FakeTransport(resp(200, ESRI_BODY))
    result = Geocoding(EsriConfig(api_key="k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="mk-abc")
    )
    assert qget(fake.last, "magicKey") == "mk-abc"
    assert qget(fake.last, "SingleLine") == ""
    assert result.candidate.location.lat == 52.36


# ---------------------------------------------------------------------------
# the `name` opt-in
# ---------------------------------------------------------------------------


# Google's Place Details SKU tier is driven by the FIELD MASK (displayName is Pro),
# the opposite of Compute Routes, whose SKU is feature-driven.
def test_google_omits_display_name_from_the_field_mask_by_default():
    body = json.dumps({
        "id": "p1",
        "formattedAddress": "somewhere",
        "location": {"latitude": 1, "longitude": 2},
        "displayName": {"text": "Blue Bottle"},
    })
    fake = FakeTransport(resp(200, body))
    result = Geocoding(GoogleConfig("k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="p1")
    )
    assert "displayName" not in fake.last.headers["X-Goog-FieldMask"]
    # Not surfaced even though this fixture carries it.
    assert result.name is None


def test_google_requests_and_surfaces_display_name_when_included():
    body = json.dumps({
        "id": "p1",
        "formattedAddress": "somewhere",
        "location": {"latitude": 1, "longitude": 2},
        "displayName": {"text": "Blue Bottle"},
    })
    fake = FakeTransport(resp(200, body))
    result = Geocoding(GoogleConfig("k"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="p1", include=[PlaceDetailsInclude.NAME])
    )
    assert "displayName" in fake.last.headers["X-Goog-FieldMask"]
    assert result.name == "Blue Bottle"


@pytest.mark.parametrize("cfg,body,expected", [
    (HereConfig("k"), HERE_BODY, "Brandenburger Tor"),
    (MapboxConfig("pk"), MAPBOX_BODY, "Blue Bottle"),
    (TomTomConfig("k"), json.dumps({"results": [{
        "id": "tt1",
        "poi": {"name": "Rijksmuseum"},
        "address": {"freeformAddress": "Amsterdam"},
        "position": {"lat": 1, "lon": 2},
    }]}), "Rijksmuseum"),
])
def test_other_providers_surface_name_when_included(cfg, body, expected):
    result = Geocoding(cfg, transport=FakeTransport(resp(200, body))).place_details(
        PlaceDetailsOptions(place_id="x", include=[PlaceDetailsInclude.NAME])
    )
    assert result.name == expected


# Esri returns only an address — there is no display name to surface, so `name` stays
# None even when asked for. Absence is information, not a bug.
def test_esri_leaves_name_none_even_when_included():
    result = Geocoding(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, ESRI_BODY))).place_details(
        PlaceDetailsOptions(place_id="mk1", include=[PlaceDetailsInclude.NAME])
    )
    assert result.name is None
    assert result.candidate.formatted_address == "Museumstraat 1"


# ---------------------------------------------------------------------------
# Mapbox session billing
# ---------------------------------------------------------------------------


# Search Box bills per SESSION: suggest + retrieve with the SAME token is one billable
# session, a missing or fresh token makes it two.
def test_mapbox_forwards_session_token():
    fake = FakeTransport(resp(200, MAPBOX_BODY))
    Geocoding(MapboxConfig("pk"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="mb1", session_token="sess-123")
    )
    assert qget(fake.last, "session_token") == "sess-123"


def test_mapbox_omits_session_token_when_none_is_given():
    fake = FakeTransport(resp(200, MAPBOX_BODY))
    Geocoding(MapboxConfig("pk"), transport=fake).place_details(
        PlaceDetailsOptions(place_id="mb1")
    )
    # Not sent as an empty string, which Mapbox would treat as a new session.
    assert qget(fake.last, "session_token") == ""


# ---------------------------------------------------------------------------
# no usable result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cfg,body", [
    (GoogleConfig("k"), json.dumps({"id": "p", "formattedAddress": "x"})),
    (HereConfig("k"), json.dumps({"title": "x", "id": "h"})),
    (MapboxConfig("pk"), json.dumps({"features": []})),
    (TomTomConfig("k"), json.dumps({"results": []})),
    (EsriConfig(api_key="k"), json.dumps({"candidates": []})),
])
def test_an_unusable_result_raises_no_route(cfg, body):
    with pytest.raises(ConnectorError) as ei:
        Geocoding(cfg, transport=FakeTransport(resp(200, body))).place_details(
            PlaceDetailsOptions(place_id="x")
        )
    assert ei.value.provider_code == ProviderCode.NO_ROUTE


# ---------------------------------------------------------------------------
# structured_format
# ---------------------------------------------------------------------------


def test_google_reads_structured_format():
    body = json.dumps({"suggestions": [{"placePrediction": {
        "placeId": "p1",
        "text": {"text": "Blue Bottle Coffee, 66 Mint St"},
        "structuredFormat": {
            "mainText": {"text": "Blue Bottle Coffee"},
            "secondaryText": {"text": "66 Mint St"},
        },
    }}]})
    result = Geocoding(GoogleConfig("k"), transport=FakeTransport(resp(200, body))).autocomplete(
        AutocompleteOptions(input="blue")
    )
    sf = result.predictions[0].structured_format
    assert sf is not None
    assert sf.main_text == "Blue Bottle Coffee"
    assert sf.secondary_text == "66 Mint St"
    # `description` is unchanged — the new field is additive.
    assert result.predictions[0].description == "Blue Bottle Coffee, 66 Mint St"


def test_google_omits_structured_format_when_the_vendor_sends_none():
    body = json.dumps({"suggestions": [{"placePrediction": {
        "placeId": "p1", "text": {"text": "Somewhere"},
    }}]})
    result = Geocoding(GoogleConfig("k"), transport=FakeTransport(resp(200, body))).autocomplete(
        AutocompleteOptions(input="some")
    )
    assert result.predictions[0].structured_format is None


# Live-verified: TomTom street/address results have no poi.name. Splitting
# freeformAddress on a comma to invent a main part would be a guess.
def test_tomtom_omits_structured_format_for_a_street_result():
    body = json.dumps({"results": [{
        "id": "tt2",
        "address": {"freeformAddress": "Museumstraat 1, Amsterdam"},
        "position": {"lat": 1, "lon": 2},
    }]})
    result = Geocoding(TomTomConfig("k"), transport=FakeTransport(resp(200, body))).autocomplete(
        AutocompleteOptions(input="museumstraat")
    )
    assert result.predictions[0].structured_format is None
    # `description` still carries the full text for rendering.
    assert result.predictions[0].description == "Museumstraat 1, Amsterdam"


# HERE's query-type suggestions have a title but no address at all.
def test_here_emits_main_text_only_when_the_item_has_no_address():
    body = json.dumps({"items": [{"title": "pizza", "id": "here:q:1"}]})
    result = Geocoding(HereConfig("k"), transport=FakeTransport(resp(200, body))).autocomplete(
        # Autosuggest mandates a search context; incidental to this assertion.
        AutocompleteOptions(input="pizza", location=LatLng(52.52, 13.405))
    )
    sf = result.predictions[0].structured_format
    assert sf is not None
    assert sf.main_text == "pizza"
    assert sf.secondary_text is None


def test_mapbox_reads_name_and_place_formatted():
    body = json.dumps({"suggestions": [{
        "name": "Blue Bottle Coffee",
        "place_formatted": "66 Mint St",
        "full_address": "Blue Bottle Coffee, 66 Mint St",
        "mapbox_id": "id1",
    }]})
    result = Geocoding(MapboxConfig("pk"), transport=FakeTransport(resp(200, body))).autocomplete(
        AutocompleteOptions(input="blue")
    )
    sf = result.predictions[0].structured_format
    assert sf is not None
    assert sf.main_text == "Blue Bottle Coffee"
    assert sf.secondary_text == "66 Mint St"


# Esri returns a single flat `text` field — the genuine gap. It must stay None rather
# than be faked by splitting that string.
def test_esri_never_emits_structured_format():
    body = json.dumps({"suggestions": [{
        "text": "Rijksmuseum, Museumstraat 1, Amsterdam",
        "magicKey": "mk1",
    }]})
    result = Geocoding(EsriConfig(api_key="k"), transport=FakeTransport(resp(200, body))).autocomplete(
        AutocompleteOptions(input="rijks")
    )
    assert len(result.predictions) == 1
    assert result.predictions[0].structured_format is None
    assert result.predictions[0].description == "Rijksmuseum, Museumstraat 1, Amsterdam"
