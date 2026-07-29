# Mapbox Connectors (Python)

Mapbox connectors for routing, distance matrix, geocoding, and isochrone via direct HTTP calls (standard library only — no vendor SDK).

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `MapboxConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, Geocoding, Isochrone, MapboxConfig

routing = Routing(MapboxConfig(access_token=os.environ["MAPBOX_TOKEN"]))
matrix  = Matrix(MapboxConfig(access_token=token))
geo     = Geocoding(MapboxConfig(access_token=token))
iso     = Isochrone(MapboxConfig(access_token=token))
```

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `access_token` | `str` | yes | Mapbox public or secret access token (must include `directions:read`, `matrix:read`, `geocoding:read`, `isochrone:read` scopes) |

Inject a custom transport with `Routing(MapboxConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

## Auth setup

Create a token at https://account.mapbox.com/access-tokens/. Sent as `access_token=` query param on every request. Static — no refresh.

## Vendor docs

- Directions: https://docs.mapbox.com/api/navigation/directions/
- Matrix: https://docs.mapbox.com/api/navigation/matrix/
- Geocoding v6: https://docs.mapbox.com/api/search/geocoding/
- Isochrone: https://docs.mapbox.com/api/navigation/isochrone/
- Rate limits: https://docs.mapbox.com/api/overview/#rate-limits

---

## Routing

### Endpoint

- Directions: `GET https://api.mapbox.com/directions/v5/mapbox/{profile}/{coordinates}`
- Optimized trips: `GET https://api.mapbox.com/optimized-trips/v1/mapbox/{profile}/{coordinates}`

### Input

Standard `RoutingOptions`. Travel mode is encoded into the URL path. Polyline returned in standard Google precision-5 format.

### Overriding `geometries` via passthrough

The connector requests `geometries=polyline6` and re-encodes to precision-5. If you override
`geometries` through `passthrough.query`, the decoder **follows your value**:

| `geometries` | Handling |
|---|---|
| `polyline6` (default) | decoded at precision 6, re-encoded at precision 5 |
| `polyline` | already precision-5 — emitted verbatim |
| `geojson` | `[lng, lat]` pairs encoded at precision 5 |

This matters because the two encodings are indistinguishable as strings: decoding a
precision-5 polyline with a precision-6 decoder divides every coordinate by 10, which lands
your route in the wrong hemisphere with no error raised. An unparseable geometry yields an
empty polyline rather than raising — the leg distances and durations are still valid.

### Error mapping

| Vendor HTTP | Vendor signal | `ProviderCode` |
|---|---|---|
| 401 | (any) | `auth_failed` |
| 403 | (any) | `auth_failed` |
| 422 | invalid coordinates | `invalid_request` |
| 429 | (respects `Retry-After`) | `rate_limited` |
| 5xx | (any) | `provider_unavailable` |

### Retry-After

On HTTP 429, `ConnectorError.cause["retryAfter"]` carries the raw header; parsed seconds appear in `provider_message`.

### `passthrough` example

```python
from thinwrap.location import RoutingOptions, Passthrough

res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(query={"annotations": "duration,distance,speed", "overview": "full"}),
))
```

### Turn-by-turn instructions

Off by default and **not normalized** — `RoutingResult` has no `steps` attribute. Ask for `steps`
and read them from `res.raw`:

```python
res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(query={"steps": "true"}),
))
```

Instruction text is at `routes[].legs[].steps[].maneuver.instruction`, alongside `type`,
`modifier`, `bearing_before` / `bearing_after` and `location`. With `optimize=True` the connector
calls `/optimized-trips/v1`, which returns the same objects under **`trips[]`** rather than
`routes[]`.

`steps` is its own parameter, so this merges additively — nothing the connector sends is
displaced. `banner_instructions` and `voice_instructions` (SSML) are separate opt-ins, and both
require `steps=true`.

Steps are the single largest part of a Mapbox routing response, which is why the connector does
not request them by default.

---

## Matrix

### Endpoint

`GET https://api.mapbox.com/directions-matrix/v1/mapbox/{profile}/{coordinates}`

### Error mapping

Same as routing. Retry-After surfacing identical.

---

## Geocoding

### Endpoints

- Forward: `GET https://api.mapbox.com/search/geocode/v6/forward`
- Reverse: `GET https://api.mapbox.com/search/geocode/v6/reverse`
- Autocomplete (Searchbox): `GET https://api.mapbox.com/search/searchbox/v1/suggest`

### Input

Standard `GeocodeOptions` / `ReverseGeocodeOptions` / `AutocompleteOptions`. Other
Geocoding/Searchbox-specific fields go via `passthrough.query`.

### Country filter

`country_filter` (ISO 3166-1 alpha-2) is translated to lowercased CSV `country=us,ca` on
**forward geocode and autocomplete alike**.

```python
res = geo.autocomplete(AutocompleteOptions(input="coffee", country_filter=["IL", "PS"]))
# → ...&country=il,ps
```

### No match-highlighting offsets

Search Box `/suggest` returns no match offsets — no `matches`, `highlights` or equivalent.
Of the five geocoders only Google and HERE return them, so a UI that bolds the matched
substring cannot get those offsets from Mapbox; it has to match client-side against
`description` / `structured_format`.

---

## Isochrone

### Endpoint

`GET https://api.mapbox.com/isochrone/v1/mapbox/{profile}/{lng},{lat}`

### Input

Standard `IsochroneOptions`. `type` (`IsochroneType.TIME` / `IsochroneType.DISTANCE`) toggles between the `contours_minutes` and `contours_meters` query params. Mapbox accepts up to 4 contour values per call.
