# HERE Connectors (Python)

HERE Location Services connectors for routing, distance matrix, geocoding, and isochrone via direct HTTP calls.

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `HereConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, Geocoding, Isochrone, HereConfig

routing = Routing(HereConfig(api_key=os.environ["HERE_KEY"]))
matrix  = Matrix(HereConfig(api_key=key))
geo     = Geocoding(HereConfig(api_key=key))
iso     = Isochrone(HereConfig(api_key=key))
```

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `api_key` | `str` | yes | HERE API key (REST) — single key works across Router v8, Matrix v8, Geocode/Revgeocode/Autocomplete, Isolines v8 |

Inject a custom transport with `Routing(HereConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

## Auth setup

Provision a project at https://platform.here.com/ and create a REST API key. Sent as `apiKey=` query param on every request. Static — no refresh.

## Vendor docs

- Routing v8: https://docs.here.com/routing/docs/routing-v8-intro
- Matrix Routing v8: https://docs.here.com/routing/reference/postmatrix
- Geocoding & Search: https://docs.here.com/geocoding-and-search/reference/
- Isoline Routing v8: https://docs.here.com/routing/docs/isoline-v8-intro
- Pricing & rate limits: https://www.here.com/pricing

---

## Routing

### Endpoints

- Standard routing: `GET https://router.hereapi.com/v8/routes`
- Waypoint sequence: `GET https://wps.hereapi.com/v8/findsequence2`

### Input

`optimize=True` triggers the two-step `findsequence2` → `routes` flow. Travel mode maps to HERE `transportMode`. Intermediate waypoints are added as `via=lat,lng` query parameters.

### Error mapping

| Vendor HTTP | Vendor signal | `ProviderCode` |
|---|---|---|
| 401 | (any) | `auth_failed` |
| 403 | (any) | `auth_failed` |
| 400 | (any) | `invalid_request` |
| 429 | (respects `Retry-After`) | `rate_limited` |
| 5xx | (any) | `provider_unavailable` |

### Retry-After

On HTTP 429, `ConnectorError.cause["retryAfter"]` carries the raw header; parsed seconds in `provider_message`.

### Turn-by-turn instructions

Off by default and **not normalized** — `RoutingResult` has no `steps` attribute. Request
`actions,instructions` and read them from `res.raw`:

```python
res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(query={"return": "polyline,summary,actions,instructions"}),
))
```

Actions land at `routes[].sections[].actions[]`: `action` (the maneuver), `instruction` (localized
text), `offset` (an index into that section's polyline), plus `duration` and `length`.

> **`return` is replaced, not merged.** The connector sends `polyline,summary`; keep both in your
> list or legs, totals and `polyline` all come back empty with nothing raised. HERE also rejects
> `instructions` unless `actions` is requested alongside it.

For a full navigation payload HERE offers `turnByTurnActions`, which requires `polyline` in the
same `return` list.

---

## Matrix

### Endpoint

`POST https://matrix.router.hereapi.com/v8/matrix?async=true` → poll status → retrieve.

### Input

`transport_mode` (a `HereTransportMode`: `CAR` | `TRUCK` | `PEDESTRIAN` | `BICYCLE` | `SCOOTER`) overrides the base `travel_mode` mapping. Polling parameters surfaced via `passthrough.body["timeoutMs"]`.

---

## Geocoding

### Endpoints

- Forward: `GET https://geocode.search.hereapi.com/v1/geocode`
- Reverse: `GET https://revgeocode.search.hereapi.com/v1/revgeocode`
- Autocomplete: `GET https://autosuggest.search.hereapi.com/v1/autosuggest`

### Autosuggest requires a search context

HERE documents exactly one of `at`, `in=circle` or `in=bbox` as **mandatory** on
Autosuggest, and rejects a request carrying none of them. Pass `location` (optionally with
`radius`) and the connector supplies it:

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen", location=LatLng(32.08, 34.78)))
```

`location` alone becomes `at=`; `location` + `radius` becomes `in=circle:…;r=…`. If you
need a bounding box instead, supply it yourself via `passthrough.query["in"]` — the
connector accepts that as the context. Calling `autocomplete()` with no context at all
raises `invalid_request` locally rather than relaying a vendor 400.

### Country filter

`country_filter` is ISO 3166-1 alpha-2 throughout this library; HERE expects **alpha-3**,
so the connector translates it. A code with no ISO alpha-3 mapping raises
`invalid_request` and points you at `passthrough.query["in"]` for anything non-standard
that HERE nonetheless accepts.

```python
res = geo.autocomplete(AutocompleteOptions(
    input="Dizen",
    location=LatLng(32.08, 34.78),
    country_filter=["IL", "PS"],
))
# → ...&at=32.08,34.78&in=countryCode:ISR,PSE
```

> **HERE spells both the country filter and the spatial filter `in`.** Its docs require
> the country filter to *accompany* one of `at` / `in=circle` / `in=bbox`, so when you
> combine `country_filter` with `radius` the connector emits `in` **twice** — once for the
> circle and once for the country codes — rather than letting one overwrite the other.
> Nothing is silently dropped, and `radius` still applies.

### Match-highlighting offsets

**Not normalized** — `AutocompletePrediction` has no `matches` attribute. Only 2 of the 5
geocoders return offsets at all (HERE and Google), and they disagree on both the encoding
and which string the offsets index, so this stays vendor-native in `raw`:

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen", location=LatLng(32.08, 34.78)))
highlights = res.raw["items"][0].get("highlights", {})
# → {"title": [{"start": int, "end": int}], "address": {"label": [...], "city": [...]}}
```

Note the shape difference from Google, which is why neither can be normalized into one
attribute: HERE anchors offsets to `title` and to individual **address components**
(`label`, `city`, `street`, `houseNumber`), and names the bounds `start`/`end`. Google
anchors to its own `mainText`/`secondaryText` and names them `startOffset`/`endOffset`.

---

## Isochrone

### Endpoint

`GET https://isoline.router.hereapi.com/v8/isolines`

### Input

Standard `IsochroneOptions`. `type` (`IsochroneType.TIME` / `IsochroneType.DISTANCE`) maps to HERE `range[type]=time|distance`.
