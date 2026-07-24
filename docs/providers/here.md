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

---

## Isochrone

### Endpoint

`GET https://isoline.router.hereapi.com/v8/isolines`

### Input

Standard `IsochroneOptions`. `type` (`IsochroneType.TIME` / `IsochroneType.DISTANCE`) maps to HERE `range[type]=time|distance`.
