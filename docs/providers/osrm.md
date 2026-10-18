# OSRM Connectors (Python)

[OSRM](https://project-osrm.org/) (Open Source Routing Machine) connectors for routing and distance matrix. **Self-hosted** — no API key, no managed service.

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `OsrmConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, OsrmConfig

routing = Routing(OsrmConfig(base_url="https://osrm.example.com"))
matrix  = Matrix(OsrmConfig(base_url="https://osrm.example.com"))
```

The default transport is **HTTPS-only**. A cleartext endpoint such as
`http://localhost:5000` needs your own `Transport` (any object with
`send(HttpRequest) -> HttpResponse`; see the package README):

```python
routing = Routing(OsrmConfig(base_url="http://localhost:5000"), transport=my_http_transport)
```

Handed an `http://` URL, the default transport raises `ConnectorError`
(`invalid_request`) saying exactly that, before any network I/O.

`Geocoding(OsrmConfig(...))` and `Isochrone(OsrmConfig(...))` raise `ValueError` — OSRM is wired only for routing and matrix.

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `base_url` | `str` | yes | OSRM server URL (e.g. `https://osrm.example.com`; `http://` needs an injected transport) |
| `supported_exclude_classes` | list of strings | no | Exclude classes YOUR build was compiled with — see below |

Inject a custom transport with `Routing(OsrmConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

The connector pre-flight-validates that `base_url` is present and raises `ConnectorError` (`provider_code` `invalid_request`) before any HTTP call when it is empty. The public demo server is never used as a default.

`base_url` must include an **`http://` or `https://` scheme**; a bare host
(`router.example.com`) is rejected with the same typed error, so a config typo is not
reported as an outage.

Credentials inline in the URL (`https://user:pass@host`) are never echoed: error messages
and `repr(OsrmConfig(...))` show `https://[REDACTED]@host`. Note the default `urllib`
transport does not turn URL userinfo into an `Authorization` header — a basic-auth proxy
needs an injected transport that does.

A **path prefix is supported** — `https://maps.example.com/osrm` works, for reverse-proxied
instances — and trailing slashes are stripped, so `https://host/` and `https://host` behave
identically in `f"{base_url}/route/v1/…"`.

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
| `avoid_tolls` | `unsupported_option` |
| `avoid_ferries` | `unsupported_option` |
| `avoid_highways` | `unsupported_option` |

`traffic_mode` is **ignored**: OSRM has no time parameter, and traffic needs the graph rebuilt
with edge-weight updates. `departure_time` is ignored with it — under `NONE` it has no effect
by definition. Durations always come from the profile's static speeds.

### Optimization (`/trip`) endpoints

Any of `optimize`, `optimize_fixed_origin`, `optimize_fixed_destination` or `is_round_trip`
selects the Trip service. With `roundtrip=false` the flags are sent as asked:

| Flags | `source` / `destination` |
|---|---|
| `optimize` only | `first` / `last` (both ends kept) |
| `optimize_fixed_origin` | `first` / `any` |
| `optimize_fixed_destination` | `any` / `last` |
| both fixed flags | `first` / `last` |

OSRM supports `first/any` and `any/last` since v5.27 (osrm-backend PR #6050); the only open
combination it rejects is `any/any`, which is why plain `optimize` pins both ends. A server
older than v5.27 answers a single fixed endpoint with `NotImplemented`, surfaced as
`unsupported_option`.

If the requested `travel_mode` doesn't have a compiled profile on the server, OSRM returns HTTP 400 with a profile-missing body which the connector maps to `provider_code` `profile_not_configured`.

### Coordinates outside your extract are snapped, not rejected

If your instance is built from a regional extract, OSRM snaps each input coordinate to the
nearest road **in that extract** — however far away it is. A request whose waypoint falls
outside the extract therefore returns **HTTP 200 with `code: "Ok"`** and a
plausible-looking route between the wrong places. There is no error and no missing field, so
no wrapper-level check can catch it.

The signal is in the raw body: each `waypoints[i].distance` is the metres from your input to
the snapped road position. Read it from the raw result and apply whatever threshold suits
your application.

The library deliberately does not pick a threshold: the acceptable snap distance is
application policy (a few hundred metres is normal for a rural pickup, and disqualifying for
a city address), so it stays with you rather than being guessed here.

### Retry-After

**Not surfaced.** OSRM has no documented rate-limit; any 429 surfaces from your reverse-proxy layer, and `Retry-After` (if set by the proxy) is forwarded as `cause["retryAfter"]` in best-effort mode.

### Turn-by-turn instructions

Off by default and **not normalized** — `RoutingResult` has no `steps` attribute. Ask for `steps`
and read them from `res.raw`:

```python
res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(query={"steps": "true"}),
))
```

Steps land at `routes[].legs[].steps[]` — or under **`trips[]`** when `optimize=True`, since that
path calls `/trip/v1`. `steps` is its own parameter, so this merges additively.

> **OSRM returns no instruction text.** A step carries `name`, `maneuver.type`,
> `maneuver.modifier`, bearings and `intersections[]` — there is no human-readable string anywhere
> in the payload. Text is generated client-side from those fields, so an OSRM navigation UI needs a
> rendering layer that the other five providers do not.

`maneuver.type` is open-ended by design: OSRM's docs state new identifiers may be introduced
without an API change, so treat an unrecognized value as a fallback rather than an error.

---

## Matrix

### Endpoint

`GET {base_url}/table/v1/{profile}/{coordinates}?annotations=duration,distance&sources={…}&destinations={…}`

### Input

Pre-flight validation (Routing's table applies, except `avoid_ferries` / `avoid_highways` don't exist on `MatrixOptions`):

| Unsupported field | `ProviderCode` |
|---|---|
| `avoid_tolls` | `unsupported_option`, unless `"toll"` is in `supported_exclude_classes` — then sent as `exclude=toll`, exactly as on routing |

The connector flattens OSRM's 2D arrays to a list of `MatrixCell`; a `null` or non-numeric
entry is omitted rather than reported as 0. Envelope codes map the same on both services and
on either status (OSRM serves them with a 4xx or in a 200): `NoRoute` / `NoSegment` (and
`NoTrips` on `/trip`) → `no_route`, `InvalidQuery` / `InvalidOptions` / `InvalidValue` /
`TooBig` (and `NoTable` on `/table`) → `invalid_request`.

### Retry-After

**Not surfaced** (same rationale as Routing).

### Making the avoid-flags work: `OsrmConfig.supported_exclude_classes`

Whether OSRM accepts `exclude=toll` is a property of **your server**, not of OSRM. The
same request was verified live against two builds with opposite results: the public demo
build rejects it with `InvalidValue`, while a self-hosted instance honoured it and
genuinely rerouted (138075 m / 5890 s via the toll road → 130421 m / 6513 s without it).

Stock OSRM compiles no exclude classes, so the avoid-flags are rejected up front with
`unsupported_option` by default — better than sending a request the server will bounce
with an opaque error. If your profile was built with them, declare it:

```python
routing = Routing(OsrmConfig(
    base_url="https://routing.internal",
    supported_exclude_classes=("toll", "ferry"),
))
```

Declared rather than probed because there is no way to ask an OSRM server what it
supports without issuing a request that fails, and the wrapper holds no state to cache
such a probe in.

### Coordinates outside your extract are snapped, not rejected

If your instance is built from a regional extract, OSRM snaps each input coordinate to
the nearest road **in that extract** — however far away it is. A request whose waypoint
falls outside the extract therefore returns **HTTP 200 with `code: "Ok"`** and a
plausible-looking route between the wrong places. There is no error and no missing
field, so no wrapper-level check can catch it.

The signal is in the raw body: each `waypoints[i].distance` is the metres from your input
to the snapped road position. Read it from the raw result and apply whatever threshold
suits your application.

The library deliberately does not pick a threshold: the acceptable snap distance is
application policy (a few hundred metres is normal for a rural pickup, and disqualifying
for a city address), so it stays with you rather than being guessed here.

---

## Replacing a default list

Passthrough **replaces** a list-valued parameter the connector sets; it does not merge with it.
That is deliberate — a merge would leave you no way to remove a value. The published default is
the same tuple the connector builds its request from:

| Operation | Parameter | Constant |
|---|---|---|
| Matrix | `annotations` query (comma-joined) | `OSRM_TABLE_ANNOTATIONS` = `("duration", "distance")` |

OSRM documents only `duration` (its own default) and `distance` for this parameter, so the
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
