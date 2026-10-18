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

`traffic_mode=TrafficMode.TRAFFIC` (or the deprecated `LIVE`) with driving selects the
**`driving-traffic`** profile — Mapbox chooses traffic by profile, not by a flag. Walking and
cycling have no traffic variant, so traffic is ignored there; the optimized path also ignores
it and stays on `driving` (Optimization v1 has no `driving-traffic`).

`departure_time` becomes `depart_at` on **`driving-traffic`** only. Under `NONE` it is not
sent: on plain `driving`, `depart_at` makes the duration a prediction from historical travel
data (typical traffic), which `NONE` promises not to add. Walking and cycling durations do not
depend on time, so it is left off there too.

The avoid flags become `exclude` classes (`toll`, `ferry`, `motorway`). Walking and cycling
accept only a subset — `toll`/`motorway` there fail with 422 "exclude value must be one of:
ferry, cash_only_tolls, …" — so for those profiles only `ferry` is sent. Nothing is lost: tolls
do not apply to either mode, the cycling profile already avoids highways, and walking follows
sidewalks and trails.

Any of `optimize`, `optimize_fixed_origin`, `optimize_fixed_destination` or `is_round_trip`
selects Optimization v1, whose accepted query parameters are a fixed list (unrecognized ones fail
with `InvalidInput`). These are refused before any request:

| Input on the optimized path | `ProviderCode` |
|---|---|
| `avoid_tolls` / `avoid_ferries` / `avoid_highways` (v1 has no `exclude`) | `unsupported_option` |
| `departure_time` with `TRAFFIC`/`LIVE` on driving (v1 has no `depart_at`); under `NONE` or on walking/cycling it is dropped | `unsupported_field` |
| exactly one of `optimize_fixed_origin` / `optimize_fixed_destination` without `is_round_trip` (v1 supports an open trip only as `source=first` + `destination=last`; the others return `NotImplemented`) | `unsupported_option` |

A round trip always sends `source=first`, plus `destination=last` when
`optimize_fixed_destination` is set.

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
| 422 | `code` `NoRoute` / `NoSegment` / `NoTrips` | `no_route` |
| 422 | `code` `ProcessingError` | `unknown` |
| 422 | any other `code` (e.g. `InvalidInput`) | `invalid_request` |
| 429 | (respects `Retry-After`) | `rate_limited` |
| 5xx | (any) | `provider_unavailable` |
| 2xx | a route or leg without `distance` / `duration` | `unknown` (never a 0 m / 0 s route) |

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

### Input

Standard `MatrixOptions`. `traffic_mode=TrafficMode.TRAFFIC` (or `LIVE`) with driving selects
the `driving-traffic` profile. Under `NONE`, and on walking/cycling (no traffic variant), a
`departure_time` is dropped (it has no effect by definition). Refused before any request:

| Input | `ProviderCode` |
|---|---|
| `avoid_tolls` (the Matrix API has no `exclude`) | `unsupported_option` |
| `departure_time` with `TRAFFIC`/`LIVE` on driving (`depart_at` is a sign-up beta on Matrix) | `unsupported_field` |

A standard account gets 422 "Request too large for custom parameters" for either. An account
enrolled in the `depart_at` beta can send it through `passthrough.query`.

### Error mapping

Same as routing: the envelope `code` decides on a 422 and in a 200 body alike (`NoRoute` /
`NoSegment` → `no_route`). Retry-After surfacing identical. A `null` or non-numeric cell is
omitted rather than reported as 0.

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

Mapbox takes whole minutes and whole meters, in increasing order, so the connector sorts the
values and rounds them (seconds to the nearest minute, meters to the nearest meter). Each
returned contour's `value` is **your** value, not the rounded one — a 90 s break is sent as
`2` and comes back as `90`. Values Mapbox cannot express raise `invalid_request` before any
request: a time break under 60 s (it would round to 0), and two breaks that round to the same
minute or meter (Mapbox would return one contour for both).

`departure_time` ⇒ `depart_at=`. It must be an ISO-8601 date-time string (sent verbatim, so its
offset — or its lack of one — is preserved) or a `datetime` (sent as UTC). A string that is not an
ISO-8601 date-time raises `invalid_request` before any request is sent; any other type is a
`TypeError` when the options are constructed.
Isochrones have no `traffic_mode`, so a `departure_time` (sent as `depart_at`) makes the contour
traffic-aware for that time: Mapbox documents that "the isochrone contours will reflect traffic
conditions at the time provided". Driving uses the `driving` profile, which without `depart_at`
uses average conditions.

### Output

With `polygons=true` each feature is a GeoJSON `Polygon`; a `MultiPolygon` feature is accepted too
and returned as a `MultiPolygon`. Holes are kept, and rings are re-wound to RFC 7946 (exterior
counter-clockwise, holes clockwise). Malformed pieces are skipped one at a time: a part without a
usable exterior is dropped, as is an unusable hole, and the rest is kept. Only a contour with
nothing usable left raises `unknown`.

---

## Replacing a default list

Passthrough **replaces** a list-valued parameter the connector sets; it does not merge with it.
That is deliberate — a merge would leave you no way to remove a value. The published default is
the same tuple the connector builds its request from:

| Operation | Parameter | Constant |
|---|---|---|
| Matrix | `annotations` query (comma-joined) | `MAPBOX_MATRIX_ANNOTATIONS` = `("duration", "distance")` |

Mapbox documents only `duration` (its own default) and `distance` for this parameter, so the
connector already sends every value there is and there is nothing to append. A caller can still
replace it:

```python
from thinwrap.location import MatrixOptions, Passthrough

matrix.matrix(MatrixOptions(
    origins=origins,
    destinations=destinations,
    passthrough=Passthrough(query={"annotations": "duration"}),
))
```

> **Omitting a value the normalizer reads.** `durations` fills `duration_seconds` and `distances`
> fills `distance_meters`. Drop either and the call raises `unknown`, because the response no
> longer carries a matrix of the requested dimensions — you get an error, never zero-valued cells.
