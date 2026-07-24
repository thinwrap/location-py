# TomTom Connectors (Python)

TomTom Maps connectors for routing, distance matrix, geocoding, and isochrone (reachable range) via direct HTTP calls.

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `TomTomConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, Geocoding, Isochrone, TomTomConfig

routing = Routing(TomTomConfig(api_key=os.environ["TOMTOM_KEY"]))
matrix  = Matrix(TomTomConfig(api_key=key))
geo     = Geocoding(TomTomConfig(api_key=key))
iso     = Isochrone(TomTomConfig(api_key=key))
```

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `api_key` | `str` | yes | TomTom API key — works across Routing v1, Matrix v2, Search v2, Reachable Range v1 |

Inject a custom transport with `Routing(TomTomConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

## Auth setup

Create a key at https://developer.tomtom.com/user/me/apps. Sent as `key=` query param on every request. Static — no refresh.

## Vendor docs

- Routing: https://developer.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route
- Matrix Routing: https://developer.tomtom.com/matrix-routing-v2-api/documentation/synchronous-matrix
- Geocoding: https://developer.tomtom.com/geocoding-api/documentation/geocode
- Reachable Range: https://developer.tomtom.com/routing-api/documentation/tomtom-maps/calculate-reachable-range
- Rate limits: https://developer.tomtom.com/

---

## Routing

### Endpoint

`GET https://api.tomtom.com/routing/1/calculateRoute/{locations}/json`

### Input

Standard `RoutingOptions`. `optimize=True` maps to `computeBestOrder=true`.

### Error mapping

| Vendor HTTP | `ProviderCode` |
|---|---|
| 400 | `invalid_request` |
| 401 / 403 | `auth_failed` |
| 429 (respects `Retry-After`) | `rate_limited` |
| 5xx | `provider_unavailable` |

### Retry-After

On HTTP 429, `ConnectorError.cause["retryAfter"]` carries the raw header; parsed seconds in `provider_message`.

---

## Matrix

### Endpoint

`POST https://api.tomtom.com/routing/matrix/2`

### Input

Standard `MatrixOptions`. Cycling travel mode raises `ConnectorError` with `provider_code` `unsupported_travel_mode` if TomTom rejects the request.

---

## Geocoding

### Endpoints

- Forward: `GET https://api.tomtom.com/search/2/geocode/{query}.json`
- Reverse: `GET https://api.tomtom.com/search/2/reverseGeocode/{lat},{lng}.json`
- Autocomplete (Fuzzy Search): `GET https://api.tomtom.com/search/2/search/{query}.json`

---

## Isochrone

### Endpoint

`GET https://api.tomtom.com/routing/1/calculateReachableRange/{lat},{lng}/json`

### Input

Standard `IsochroneOptions`. `type` `IsochroneType.TIME` ⇒ `timeBudgetInSec=`; `type` `IsochroneType.DISTANCE` ⇒ `distanceBudgetInMeters=`. Multi-value calls fan out via one request per value.
