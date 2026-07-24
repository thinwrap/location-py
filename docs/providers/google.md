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
from thinwrap.location import RoutingOptions, Passthrough

res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(
        body={"languageCode": "fr", "units": "IMPERIAL"},
        headers={"X-Goog-FieldMask": "routes.legs.distanceMeters,routes.duration,routes.warnings"},
    ),
))
```

---

## Matrix

### Endpoint

`POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix`

### Input

Standard `MatrixOptions` (`origins`, `destinations`, `travel_mode`, `departure_time`). The connector flattens the response into a list of `MatrixCell` with `origin_index` + `destination_index`.

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
| `OVER_QUERY_LIMIT` | `rate_limited` |
| `INVALID_REQUEST` | `invalid_request` |
| `UNKNOWN_ERROR` | `provider_unavailable` |

### `passthrough` example

```python
from thinwrap.location import GeocodeOptions, Passthrough

res = geo.geocode(GeocodeOptions(
    address="1600 Amphitheatre Parkway",
    passthrough=Passthrough(query={"region": "us", "language": "en", "components": "country:US"}),
))
```
