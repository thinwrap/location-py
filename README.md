# thinwrap-location (Python)

Unified, **SDK-free**, **zero-runtime-dependency** Python wrapper for routing,
distance matrix, geocoding, and isochrone across **Google, Mapbox, HERE, ESRI,
TomTom, and OSRM**. Switch vendor by changing the config type; the input and
output shapes stay identical. Synchronous, stateless, and standard-library-only —
bring your own HTTP transport if you want `httpx`/`requests`.

```bash
pip install thinwrap-location
```

Requires Python ≥ 3.10. No runtime dependencies.

## Two-minute route

```python
from thinwrap.location import Routing, RoutingOptions, GoogleConfig, LatLng, ConnectorError

r = Routing(GoogleConfig(api_key="..."))
try:
    res = r.route(RoutingOptions(waypoints=[
        LatLng(40.7128, -74.0060),  # New York
        LatLng(41.4173, -73.0001),  # Bridgeport
    ]))
    print(f"{res.total_distance_meters / 1000:.1f} km, {res.total_duration_seconds / 60:.0f} min")
except ConnectorError as e:
    print(e.provider_code.value, e.provider_message)
```

## Operations & providers

The **config type selects the provider**. An operation a provider does not
support raises `ValueError` at facade construction (e.g. `Geocoding(OsrmConfig(...))`).

| Facade | Method(s) | Providers |
|---|---|---|
| `Routing` | `route` | Google, Mapbox, HERE, ESRI, OSRM, TomTom |
| `Matrix` | `matrix` (flat cells) | Google, Mapbox, HERE, ESRI, OSRM, TomTom |
| `Geocoding` | `geocode`, `reverse_geocode`, `autocomplete` | 5 (no OSRM) |
| `Isochrone` | `isochrone` | Mapbox, HERE, ESRI, TomTom |

Configs: `GoogleConfig(api_key=...)`, `MapboxConfig(access_token=...)`,
`HereConfig(api_key=...)`, `EsriConfig(api_key=... | arcgis_token=...)` (exactly
one), `OsrmConfig(base_url=...)` (required), `TomTomConfig(api_key=...)`.

Per-provider details (endpoints, auth, error mapping, passthrough) live in
[`docs/providers/`](docs/providers/) — one page per provider.

## Switching providers

```python
from thinwrap.location import Routing, MapboxConfig, HereConfig, OsrmConfig

# Same .route(opts) call, same RoutingResult — only the config changes.
mapbox = Routing(MapboxConfig(access_token=token))
here   = Routing(HereConfig(api_key=key))
osrm   = Routing(OsrmConfig(base_url="http://localhost:5000"))
```

## Normalized surface

Distances are **meters**, durations are **seconds**, coordinates are `LatLng(lat, lng)`
(lat-first), and route geometry is a **Google precision-5 polyline** string. Every
result carries a `raw` escape hatch holding the decoded vendor body. Isochrone
contour geometry is a GeoJSON `Polygon`.

## Sync + bring your own HTTP client

The default transport is standard-library `urllib` (never follows redirects). To
use `httpx`/`requests`/a traced client, implement the tiny `Transport` protocol
and inject it — the library never imports a third-party HTTP client, so it stays
zero-dependency:

```python
import httpx
from thinwrap.location import Routing, GoogleConfig
from thinwrap.location.transport import HttpRequest, HttpResponse

_client = httpx.Client()

class HttpxTransport:
    def send(self, req: HttpRequest) -> HttpResponse:
        r = _client.request(req.method, req.url, headers=dict(req.headers), content=req.body)
        return HttpResponse(status=r.status_code, headers={k.lower(): v for k, v in r.headers.items()}, body=r.content)

r = Routing(GoogleConfig(api_key=key), transport=HttpxTransport())
```

The wrapper holds no state — no caching, retries, idempotency keys, or telemetry.
(The HERE/TomTom async-matrix submit/poll/retrieve cycle is transient, within a
single `matrix` call.)

## `_passthrough` escape hatch

Forward vendor-specific fields the normalized input doesn't expose via
`Passthrough`. `body` is deep-merged into the request body; `headers`/`query` are
shallow-merged. Consumer values win (including over connector-set values).

```python
from thinwrap.location import Passthrough
r.route(RoutingOptions(waypoints=wps, passthrough=Passthrough(query={"alternatives": "true"})))
```

## Polyline utilities

Four locked, cross-language-parity helpers (stdlib-only):

```python
from thinwrap.location import encode_polyline, decode_polyline, decode_flex_polyline, encode_esri_paths
```

## Error handling

Every failure is a `ConnectorError` carrying a typed `ProviderCode`: the 6
canonical values (`invalid_recipient`, `rate_limited`, `auth_failed`,
`provider_unavailable`, `invalid_request`, `unknown`) plus 5 location-extended
(`unsupported_field`, `unsupported_option`, `unsupported_travel_mode`,
`profile_not_configured`, `matrix_polling_timeout`) — byte-identical to the TS,
PHP, and Go siblings. There is **no** top-level `retry_after_seconds`: the raw
`Retry-After` header rides in `cause["retryAfter"]`, with its parsed seconds woven
into `provider_message`.

MIT © Dmitry Polyanovsky
