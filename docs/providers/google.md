# Google Maps Platform Connectors (Python)

Google Maps Platform connectors for routing, distance matrix, and geocoding via direct HTTP calls (standard library only — no vendor SDK).

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `GoogleConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, Geocoding, GoogleConfig

routing = Routing(GoogleConfig(api_key=os.environ["GOOGLE_MAPS_API_KEY"]))
matrix  = Matrix(GoogleConfig(api_key=key))
geo     = Geocoding(GoogleConfig(api_key=key))
```

`Isochrone(GoogleConfig(...))` raises `ValueError` — Google is not wired for isochrone.

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `api_key` | `str` | yes | Google Maps Platform API key (single key works across Routes + Geocoding + Places) |

Inject a custom transport with `Routing(GoogleConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

## Auth setup

Generate a key at https://console.cloud.google.com/google/maps-apis/credentials with the **Routes API**, **Geocoding API**, and **Places API** enabled. Sent as `X-Goog-Api-Key` header (Routes v2 / Matrix v2 / Places Autocomplete NEW) or `key=` query param (Geocoding). Static key — no refresh, no rotation.

## Vendor docs

- Routes API v2: https://developers.google.com/maps/documentation/routes
- Geocoding API: https://developers.google.com/maps/documentation/geocoding
- Places Autocomplete (NEW): https://developers.google.com/maps/documentation/places/web-service/place-autocomplete
- Rate limits: https://developers.google.com/maps/documentation/routes/usage-and-billing

---

## Routing

### Endpoint

`POST https://routes.googleapis.com/directions/v2:computeRoutes`

### Input

The standard `RoutingOptions` shape applies as-is: `waypoints`, `travel_mode`, `optimize`, `departure_time`, `avoid_tolls`, `avoid_ferries`, `avoid_highways`. Provider-specific Routes v2 features (lane guidance, route modifiers) go via `passthrough.body`.

`traffic_mode` sets `routingPreference` for DRIVE and TWO_WHEELER (Google rejects it for WALK
and BICYCLE): `NONE` ⇒ `TRAFFIC_UNAWARE` (time-independent average speeds, Essentials SKU),
`TRAFFIC`/`LIVE` ⇒ `TRAFFIC_AWARE` (**Pro SKU**). `departure_time` is sent only with traffic:
Google rejects a timestamp on `TRAFFIC_UNAWARE` ("Timestamp cannot be set for
TRAFFIC_UNAWARE routing mode.", HTTP 400), so under `NONE` it is dropped.

### `waypoint_order` and Google's `[-1]` answer

Google reports the optimized order as `routes.optimizedIntermediateWaypointIndex`, over the
INTERMEDIATE waypoints only. The connector projects that onto the canonical `waypoint_order`
(all input indices, in visit order, origin/destination inclusive).

Google does **not** always return real indices: when it declines to optimize it answers
`[-1]`. The connector validates the projection and **omits `waypoint_order` entirely** unless it
is a complete permutation of `[0..N-1]` — so a `None` `waypoint_order` means "no usable ordering", never
a silently corrupt one. The rest of the result (legs, totals, polyline, raw) is returned as
normal, and the raw body still carries the vendor's own field if you need to inspect it.

### Error mapping

| Vendor HTTP | Vendor signal | `ProviderCode` |
|---|---|---|
| 401 | (any) | `auth_failed` |
| 403 | `error.status == 'QUOTA_EXCEEDED'` | `rate_limited` |
| 403 | (other) | `auth_failed` |
| 400 | `error.details[]` `ErrorInfo.reason` (e.g. `API_KEY_INVALID`) | `auth_failed` |
| 400 | (other) | `invalid_request` |
| 429 | (any; respects `Retry-After`) | `rate_limited` |
| 5xx | (any) | `provider_unavailable` |
| network failure | — | `provider_unavailable` |

### Retry-After

On HTTP 429, the raw `Retry-After` header rides in `ConnectorError.cause["retryAfter"]`; the parsed seconds count is woven into `ConnectorError.provider_message` as `…; retry after N seconds`.

### `passthrough` example

```python
from thinwrap.location import GOOGLE_ROUTES_FIELD_MASK, Passthrough, RoutingOptions

res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(
        body={"languageCode": "fr", "units": "IMPERIAL"},
        # The mask REPLACES the connector's own, so start from the published default.
        headers={"X-Goog-FieldMask": ",".join(GOOGLE_ROUTES_FIELD_MASK + ("routes.warnings",))},
    ),
))
```

### Turn-by-turn instructions

Off by default and **not normalized** — `RoutingResult` has no `steps` attribute. Add the step
fields to the mask and read them from `res.raw`, which carries Google's response verbatim:

```python
res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(headers={
        "X-Goog-FieldMask": ",".join(
            GOOGLE_ROUTES_FIELD_MASK + ("routes.legs.steps.navigationInstruction",)
        ),
    }),
))
```

Instruction text is at `routes[].legs[].steps[].navigationInstruction.instructions`; the icon
enum is at `.maneuver`. The mask you pass replaces the connector's own — see
[Replacing a default list](#replacing-a-default-list) for why and what to keep.

Requesting steps does not change the SKU: the Compute Routes SKU is selected by request
*features* (`TRAFFIC_AWARE`, two-wheel routing), not by requested fields. It does enlarge the
response considerably.

---

## Matrix

### Endpoint

`POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix`

### Input

Standard `MatrixOptions` (`origins`, `destinations`, `travel_mode`, `departure_time`). `traffic_mode` and `departure_time` follow the [routing rule](#input); Route Matrix bills per element, so `TRAFFIC` moves every element onto the Pro SKU. The connector flattens the response into a list of `MatrixCell` with `origin_index` + `destination_index`.

### Error mapping

Same table as routing (Routes API shares the error surface, including the `ErrorInfo.reason` auth mapping). Retry-After surfacing identical.

### `passthrough` example

```python
from thinwrap.location import MatrixOptions, Passthrough

res = matrix.matrix(MatrixOptions(
    origins=origins,
    destinations=destinations,
    passthrough=Passthrough(body={"routingPreference": "TRAFFIC_AWARE_OPTIMAL"}),
))
```

---

## Geocoding

### Endpoint

- Forward / reverse: `GET https://maps.googleapis.com/maps/api/geocode/json` (`key=` query auth)
- Autocomplete: `POST https://places.googleapis.com/v1/places:autocomplete` (Places Autocomplete NEW; `X-Goog-Api-Key` header auth + JSON body)

### Input

Standard `GeocodeOptions` / `ReverseGeocodeOptions` / `AutocompleteOptions`. Provider-specific Places fields (`sessiontoken`, `radius`, `strictbounds`) go via `passthrough.query`.

### Error mapping

Google returns HTTP 200 with a `status` field on geocoding errors. The connector maps:

| Google `status` | `ProviderCode` |
|---|---|
| `OK` / `ZERO_RESULTS` | (no error) |
| `REQUEST_DENIED` | `auth_failed` |
| `OVER_DAILY_LIMIT` | `auth_failed` (invalid key, billing not enabled, a self-imposed usage cap, or an invalid payment method — an account fix, not a retry) |
| `OVER_QUERY_LIMIT` | `rate_limited` |
| `INVALID_REQUEST` | `invalid_request` |
| `UNKNOWN_ERROR` | `provider_unavailable` |

Autocomplete (Places API New) answers in the `google.rpc` error shape instead, and uses the
routing table above: an invalid key is HTTP 400 `INVALID_ARGUMENT` whose `ErrorInfo.reason`
is `API_KEY_INVALID`, surfaced as `auth_failed` — as are the other key/credential reasons
(`API_KEY_SERVICE_BLOCKED`, `API_KEY_HTTP_REFERRER_BLOCKED`, …).

### `passthrough` example

```python
from thinwrap.location import GeocodeOptions, Passthrough

res = geo.geocode(GeocodeOptions(
    address="1600 Amphitheatre Parkway",
    passthrough=Passthrough(query={"region": "us", "language": "en", "components": "country:US"}),
))
```

### Country filter

`country_filter` is ISO 3166-1 alpha-2 throughout this library, and each operation
translates it into the parameter that operation's endpoint actually takes:

| Operation | Parameter |
|---|---|
| `geocode()` | `components=country:XX` — **one country only** |
| `autocomplete()` | `includedRegionCodes` (JSON body) |

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen", country_filter=["IL", "PS"]))
# → body["includedRegionCodes"] == ["il", "ps"]
```

Two things the translation handles for you, because both fail quietly otherwise:

- **Autocomplete takes ccTLD codes, not ISO**, and the two disagree on the United
  Kingdom — ISO `GB` is ccTLD `uk`. Passing `GB` through unchanged returns no UK
  predictions rather than an error, so the connector rewrites it.
- **Google caps the list at 15** and rejects the whole request over that. The connector
  raises `invalid_request` before the round-trip.
- **`geocode()` honours a single country.** `components=country:US|country:CA` is not an
  OR: Google applies the first country and pads the results with unrelated matches ("Windsor"
  with `US|CA` → only Windsor, CT; `CA|US` → only Windsor, ON). More than one entry raises
  `unsupported_option` before any request; autocomplete's `includedRegionCodes` does take a
  list.

> **Setting a country filter also suppresses Google's *query* predictions.** It changes
> which *kinds* of suggestion come back, not only how many — a query-type row (a search
> term rather than a place) will no longer appear at all.

### Match-highlighting offsets

**Not normalized** — `AutocompletePrediction` has no `matches` attribute. Only 2 of the 5
geocoders return offsets at all (Google and HERE), and they disagree on both the encoding
and which string the offsets index, so this stays vendor-native in `raw`:

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen"))
matches = res.raw["suggestions"][0]["placePrediction"]["structuredFormat"]["mainText"]["matches"]
# → [{"startOffset": int, "endOffset": int}]
```

Available on `text`, `structuredFormat.mainText` and `structuredFormat.secondaryText`, and
present by default — no extra request field and no extra cost.

> These are Unicode code-point offsets. Python string indices are code points too, so
> `text[start:end]` is correct here — Python is the one language in this family where the
> obvious slice needs no conversion (JavaScript is UTF-16, PHP `substr` and Go slicing are
> byte-based).

---

## Replacing a default list

Every Google endpoint here requires an `X-Goog-FieldMask`, and the connector sends its own.
`Passthrough(headers={"X-Goog-FieldMask": ...})` **replaces** that mask; it does not merge
with it. That is deliberate — a merge would leave you no way to remove a field. To add one,
start from the published default, which is the same tuple the connector builds its request
from:

```python
from thinwrap.location import GOOGLE_ROUTES_FIELD_MASK, Passthrough, RoutingOptions

routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(headers={
        "X-Goog-FieldMask": ",".join(GOOGLE_ROUTES_FIELD_MASK + ("routes.travelAdvisory",)),
    }),
))
```

| Operation | Always sent | Added by the connector when… |
|---|---|---|
| Routing | `GOOGLE_ROUTES_FIELD_MASK` | `GOOGLE_ROUTES_FIELD_MASK_DURATION_WITHOUT_TRAFFIC` — `include` has `DURATION_WITHOUT_TRAFFIC`; `GOOGLE_ROUTES_FIELD_MASK_WAYPOINT_ORDER` — any `optimize*` flag or `is_round_trip` |
| Matrix | `GOOGLE_MATRIX_FIELD_MASK` | — |
| Place details | `GOOGLE_PLACE_DETAILS_FIELD_MASK` | `GOOGLE_PLACE_DETAILS_FIELD_MASK_NAME` — `include` has `PlaceDetailsInclude.NAME` |

Your mask replaces the conditional groups too, so re-add any group your request would have
triggered.

> **Omitting a field the normalizer reads empties that output — mostly without raising.**
> Absent values are coalesced, so the result looks like a real zero-length route.
>
> - Routing: `routes.legs.distanceMeters` / `routes.legs.duration` → each leg's distance /
>   duration (`0`); `routes.distanceMeters` / `routes.duration` → the totals (`0`);
>   `routes.polyline.encodedPolyline` → `polyline` (`""`); the static-duration fields →
>   `duration_without_traffic_seconds` (`None`); `routes.optimizedIntermediateWaypointIndex`
>   → `waypoint_order` (`None`). Dropping every `routes.legs.*` field leaves the route with
>   no legs, which raises `no_route`.
> - Matrix: `distanceMeters` / `duration` → the cell values (`0`); `originIndex` /
>   `destinationIndex` → the cell position (every cell lands on `0`); `status` / `condition`
>   → reachability — an element carrying neither is treated as a failed cell, so dropping
>   both omits every cell.
> - Place details: `formattedAddress` → `formatted_address` (`""`); `id` → `place_id`
>   (`None`); `viewport` → `viewport` (`None`); `displayName` → `name` (`None`, and only read
>   when `NAME` is included). Dropping `location` returns `candidate=None`, since a candidate
>   is never given a fabricated coordinate.
>
> Each mask field also selects Google's billing tier on Place Details, so adding a field there
> can move the call onto a pricier SKU.
