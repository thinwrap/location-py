"""``country_filter`` on ``autocomplete()``.

All five geocoders support a native country restriction, so it is a base field.
Each connector translates it into that vendor's own parameter — these tests pin the
translation per provider, plus HERE's mandatory search context.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

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
    Passthrough,
    ProviderCode,
    TomTomConfig,
)


def test_google_maps_country_filter_to_lowercased_included_region_codes():
    fake = FakeTransport(resp(200, '{"suggestions":[]}'))
    g = Geocoding(GoogleConfig("k"), transport=fake)

    # `GB` is the ccTLD outlier: this endpoint wants `uk`, and passing the ISO code
    # through would quietly return no UK predictions.
    g.autocomplete(AutocompleteOptions(input="cafe", country_filter=["IL", "PS", "GB"]))

    body = json.loads(fake.last.body)
    assert body["includedRegionCodes"] == ["il", "ps", "uk"]


def test_google_omits_included_region_codes_without_a_filter():
    fake = FakeTransport(resp(200, '{"suggestions":[]}'))
    Geocoding(GoogleConfig("k"), transport=fake).autocomplete(AutocompleteOptions(input="cafe"))

    assert "includedRegionCodes" not in json.loads(fake.last.body)


def test_google_rejects_more_than_fifteen_country_codes_without_a_round_trip():
    fake = FakeTransport(resp(200, '{"suggestions":[]}'))
    g = Geocoding(GoogleConfig("k"), transport=fake)
    sixteen = [chr(97 + i // 2) + chr(97 + i % 2) for i in range(16)]

    with pytest.raises(ConnectorError) as excinfo:
        g.autocomplete(AutocompleteOptions(input="cafe", country_filter=sixteen))

    assert excinfo.value.provider_code is ProviderCode.INVALID_REQUEST
    assert "at most 15" in str(excinfo.value)
    assert fake.calls == []


def test_mapbox_maps_country_filter_to_lowercased_csv():
    fake = FakeTransport(resp(200, '{"suggestions":[]}'))
    Geocoding(MapboxConfig("pk"), transport=fake).autocomplete(
        AutocompleteOptions(input="coffee", country_filter=["IL", "PS"])
    )

    assert qget(fake.last, "country") == "il,ps"


def test_tomtom_maps_country_filter_to_country_set():
    fake = FakeTransport(resp(200, '{"results":[]}'))
    Geocoding(TomTomConfig("k"), transport=fake).autocomplete(
        AutocompleteOptions(input="Empire", country_filter=["IL", "PS"])
    )

    assert qget(fake.last, "countrySet") == "IL,PS"


def test_esri_forwards_country_filter_as_country_code():
    fake = FakeTransport(resp(200, '{"suggestions":[]}'))
    Geocoding(EsriConfig(api_key="k"), transport=fake).autocomplete(
        AutocompleteOptions(input="New York", country_filter=["IL", "PS"])
    )

    assert qget(fake.last, "countryCode") == "IL,PS"


def test_here_translates_country_filter_to_alpha3():
    fake = FakeTransport(resp(200, '{"items":[]}'))
    Geocoding(HereConfig("k"), transport=fake).autocomplete(
        AutocompleteOptions(
            input="Tel",
            location=LatLng(32.08, 34.78),
            country_filter=["IL", "PS"],
        )
    )

    assert qget(fake.last, "in") == "countryCode:ISR,PSE"
    # The spatial context must survive alongside the country filter.
    assert qget(fake.last, "at") != ""


def test_here_emits_both_in_values_when_country_filter_meets_radius():
    """HERE requires the country filter to accompany the spatial filter, and spells
    both ``in`` — so the pair must survive as a repeated key rather than one
    overwriting the other."""
    fake = FakeTransport(resp(200, '{"items":[]}'))
    Geocoding(HereConfig("k"), transport=fake).autocomplete(
        AutocompleteOptions(
            input="Tel",
            location=LatLng(32.08, 34.78),
            radius=5000,
            country_filter=["IL"],
        )
    )

    values = parse_qs(urlsplit(fake.last.url).query)["in"]
    assert len(values) == 2
    assert any(v.startswith("circle:") for v in values)
    assert "countryCode:ISR" in values


def test_here_unmapped_country_code_raises_before_any_request():
    fake = FakeTransport(resp(200, '{"items":[]}'))
    g = Geocoding(HereConfig("k"), transport=fake)

    with pytest.raises(ConnectorError) as excinfo:
        g.autocomplete(
            AutocompleteOptions(input="Tel", location=LatLng(32.08, 34.78), country_filter=["ZZ"])
        )

    assert excinfo.value.provider_code is ProviderCode.INVALID_REQUEST
    assert fake.calls == []


def test_here_requires_a_search_context():
    """HERE documents exactly one of at / in=circle / in=bbox as mandatory on
    Autosuggest. Sending the request without one only earns a vendor 400."""
    fake = FakeTransport(resp(200, '{"items":[]}'))
    g = Geocoding(HereConfig("k"), transport=fake)

    with pytest.raises(ConnectorError) as excinfo:
        g.autocomplete(AutocompleteOptions(input="Tel"))

    assert excinfo.value.provider_code is ProviderCode.INVALID_REQUEST
    assert "search context" in str(excinfo.value)
    assert fake.calls == []


def test_here_accepts_a_passthrough_supplied_search_context():
    fake = FakeTransport(resp(200, '{"items":[]}'))
    Geocoding(HereConfig("k"), transport=fake).autocomplete(
        AutocompleteOptions(
            input="Tel",
            passthrough=Passthrough(query={"in": "bbox:34.0,31.0,35.0,33.0"}),
        )
    )

    assert qget(fake.last, "in").startswith("bbox:")


def test_here_country_code_filter_skips_blank_entries():
    """An all-blank filter must read as "no filter" rather than producing
    ``countryCode:`` with an empty value."""
    fake = FakeTransport(resp(200, '{"items":[]}'))
    Geocoding(HereConfig("k"), transport=fake).autocomplete(
        AutocompleteOptions(input="Tel", location=LatLng(32.08, 34.78), country_filter=[" ", ""])
    )

    assert qget(fake.last, "in") == ""
    assert qget(fake.last, "at") != ""
