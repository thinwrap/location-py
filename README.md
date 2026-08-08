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

## Routing options that cost money

Three inputs exist because the cheap thing and the correct thing are not always the
same request. All three default to the lean option; you opt up explicitly.

| Option | Default | What it changes |
|---|---|---|
| `polyline_quality` | `PolylineQuality.SIMPLIFIED` | Geometry fidelity. `DETAILED` returned a **30x larger** polyline on Mapbox and **31x** on OSRM in measurement, with identical distances and durations. Honoured by Google/Mapbox/OSRM; silently ignored by HERE/TomTom/Esri. |
| `traffic_mode` | `TrafficMode.NONE` | Whether to route against live traffic. `LIVE` selects a **Pro-tier SKU** on Google, so it is never enabled implicitly — not even by passing `departure_time`. |
| `include` | `()` | Which optional output fields to fetch. Each token maps 1:1 onto one optional result field. |

```python
from thinwrap.location import PolylineQuality, RoutingInclude, RoutingOptions, TrafficMode

result = routing.route(RoutingOptions(
    waypoints=waypoints,
    traffic_mode=TrafficMode.LIVE,                             # traffic-aware routing
    polyline_quality=PolylineQuality.DETAILED,                 # full geometry
    include=[RoutingInclude.DURATION_WITHOUT_TRAFFIC],         # the extra output field
))

# Present only when requested AND returned natively — never synthesized, so None
# tells you this provider did not supply it.
congestion = (
    result.total_duration_seconds - result.total_duration_without_traffic_seconds
    if result.total_duration_without_traffic_seconds is not None
    else None
)
```

`DURATION_WITHOUT_TRAFFIC` is native on Google (`staticDuration`), HERE
(`baseDuration`) and TomTom (`noTrafficTravelTimeInSeconds`); Mapbox, OSRM and Esri do
not return it, so the field stays `None` there rather than being faked.

### Making OSRM's avoid-flags work

Whether OSRM accepts `exclude=toll` is a property of **your server**, not of OSRM. The
same request was verified live against two builds with opposite results: the public demo
build rejects it with `InvalidValue`, while a self-hosted instance honoured it and
genuinely rerouted (138075 m / 5890 s via the toll road → 130421 m / 6513 s without).

Stock OSRM compiles no exclude classes, so the flags are rejected up front by default.
If your profile was built with them, declare it:

```python
routing = Routing(OsrmConfig(
    base_url="https://routing.internal",
    supported_exclude_classes=("toll", "ferry"),
))
```

## Autocomplete → place details

`autocomplete()` returns predictions; `place_details()` resolves one into a full
candidate. "Place details" and "geocode by place id" are the same vendor call on all
five providers, so this is one operation, not two — and it returns an ordinary
`GeocodeCandidate`.

```python
geocoding = Geocoding(GoogleConfig(api_key=key))

predictions = geocoding.autocomplete(AutocompleteOptions(input="blue bottle")).predictions

# Render the usual two-line suggestion without splitting `description` on a comma.
for p in predictions:
    sf = p.structured_format
    print(sf.main_text if sf else p.description)
    print(sf.secondary_text if sf and sf.secondary_text else "")

details = geocoding.place_details(PlaceDetailsOptions(place_id=predictions[0].place_id))
```

`place_id` values are **provider-scoped** — a Google place id is meaningless to Mapbox.

### Two things that cost money here

**Google's Place Details SKU is driven by the field mask**, so `name` (`displayName`) is
a Pro-tier field and only requested behind an opt-in:

```python
geocoding.place_details(PlaceDetailsOptions(
    place_id=place_id,
    include=[PlaceDetailsInclude.NAME],
))
```

Note this is the *opposite* of Compute Routes, whose SKU is driven by request
*features* — check per API rather than generalizing.

**Mapbox Search Box bills per session, not per request.** A `suggest` and the
`retrieve` that follows count as one billable session only when they carry the same
token:

```python
token = uuid.uuid4().hex   # one per user interaction

mapbox.autocomplete(AutocompleteOptions(
    input=text, passthrough=Passthrough(query={"session_token": token})
))
mapbox.place_details(PlaceDetailsOptions(place_id=place_id, session_token=token))
```

The wrapper cannot generate or remember that token — it holds no state.

### `structured_format` support

| Provider | `main_text` / `secondary_text` |
|---|---|
| Google | `structuredFormat.mainText` / `.secondaryText` — default-on, free |
| Mapbox | `name` / `place_formatted` |
| HERE | `title` / `address.label` — `secondary_text` None for *query*-type suggestions, which carry no address |
| TomTom | `poi.name` / `address.freeformAddress` — **None for street results**, which have no `poi.name` |
| Esri | not supported — returns a single flat `text` |

It is **never synthesized**: a `None` `structured_format` means the provider gave no
distinct main part, and `description` remains the thing to render.

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

**Contract: a non-2xx must be RETURNED, not raised.** Each connector's status mapping
(429 → `RATE_LIMITED`, 401 → `AUTH_FAILED`, …) reads the response, and the bundled
`urllib` transport honours this. A transport calling `requests`/`httpx`
`raise_for_status()` violates it, so that case is handled defensively — the response is
rebuilt from the exception and classification still runs — but returning it is correct.

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
`provider_unavailable`, `invalid_request`, `unknown`) plus 7 location-extended
(`unsupported_field`, `unsupported_option`, `unsupported_travel_mode`,
`profile_not_configured`, `matrix_polling_timeout`, `no_route`, `timeout`) —
byte-identical to the TS, PHP, and Go siblings.

### `no_route` — "there is no route", normalized

The providers agree on nothing here. Google answers HTTP **200** with the `routes` key
absent; HERE 200 with `routes: []` plus a `notices[].code`; Mapbox `code: "NoRoute"` on
either 200 or 422; OSRM the same codes on a **400**; TomTom a 400 with
`detailedError.code`; Esri a 200 whose in-body `error.code: 400` names an **unlocated**
stop in `details[]`. Branching on "no usable route" used to mean reimplementing all six.

In practice it almost always means *a waypoint could not be matched to the road
network* rather than *the road network is disconnected*: every provider tested happily
routes Reykjavik→Oslo via ferry.

### `timeout`

Separated from `provider_unavailable` because it is the one transport failure a caller
acts on differently — back off and retry, versus treat the provider as down.

The default `Transport` carries a 30-second timeout; a `TimeoutError` (including one
wrapped by `urllib` as `URLError(TimeoutError(...))`) classifies as `TIMEOUT`.

There is **no** top-level `retry_after_seconds`: the raw
`Retry-After` header rides in `cause["retryAfter"]`, with its parsed seconds woven
into `provider_message`.

## Security

Report vulnerabilities **privately** — please do not open a public issue. Preferred: a
[private security advisory](https://github.com/thinwrap/location-py/security/advisories/new)
on this repository. Alternatively, email **security@thinwrap.dev**. Include the affected
versions and a minimal reproduction if you have one.

A vulnerability in a *provider's* own API or service belongs to that vendor rather than
to this wrapper — please report those upstream.

MIT © Dmitry Polyanovsky
