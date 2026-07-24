# OSRM Connectors (Python)

[OSRM](https://project-osrm.org/) (Open Source Routing Machine) connectors for routing and distance matrix. **Self-hosted** — no API key, no managed service.

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `OsrmConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, OsrmConfig

routing = Routing(OsrmConfig(base_url="http://localhost:5000"))
matrix  = Matrix(OsrmConfig(base_url="http://localhost:5000"))
```

`Geocoding(OsrmConfig(...))` and `Isochrone(OsrmConfig(...))` raise `ValueError` — OSRM is wired only for routing and matrix.

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `base_url` | `str` | yes | OSRM server URL (e.g. `http://localhost:5000` or your hosted instance) |

Inject a custom transport with `Routing(OsrmConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

The connector pre-flight-validates that `base_url` is present and raises `ConnectorError` (`provider_code` `invalid_request`) before any HTTP call when it is empty. The public demo server is never used as a default.

## Auth setup

**None.** OSRM is self-hosted. Front it with a reverse proxy if you need authentication or rate limiting — 401/429 responses from the proxy are surfaced as `auth_failed` / `rate_limited` ConnectorErrors.

## Vendor docs

- OSRM Route service: https://project-osrm.org/docs/v5.24.0/api/#route-service
- OSRM Trip service: https://project-osrm.org/docs/v5.24.0/api/#trip-service
- OSRM Table service: https://project-osrm.org/docs/v5.24.0/api/#table-service

---

## Routing

### Endpoints

- Routing: `GET {base_url}/route/v1/{profile}/{coordinates}`
- Optimization (TSP): `GET {base_url}/trip/v1/{profile}/{coordinates}`

### Input

Pre-flight validation raises typed errors before any HTTP call:

| Unsupported field | `ProviderCode` |
|---|---|
| `departure_time` (no live-traffic on stock OSRM) | `unsupported_field` |
| `avoid_tolls` | `unsupported_option` |
| `avoid_ferries` | `unsupported_option` |
| `avoid_highways` | `unsupported_option` |

If the requested `travel_mode` doesn't have a compiled profile on the server, OSRM returns HTTP 400 with a profile-missing body which the connector maps to `provider_code` `profile_not_configured`.

### Retry-After

**Not surfaced.** OSRM has no documented rate-limit; any 429 surfaces from your reverse-proxy layer, and `Retry-After` (if set by the proxy) is forwarded as `cause["retryAfter"]` in best-effort mode.

---

## Matrix

### Endpoint

`GET {base_url}/table/v1/{profile}/{coordinates}?annotations=duration,distance&sources={…}&destinations={…}`

### Input

Pre-flight validation (Routing's table applies, except `avoid_ferries` / `avoid_highways` don't exist on `MatrixOptions`):

| Unsupported field | `ProviderCode` |
|---|---|
| `departure_time` | `unsupported_field` |
| `avoid_tolls` | `unsupported_option` |

The connector flattens OSRM's 2D arrays to a list of `MatrixCell`.

### Retry-After

**Not surfaced** (same rationale as Routing).
