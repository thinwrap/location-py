# Architecture — thinwrap-location (Python)

## Facade → dispatch → base

```
Routing(GoogleConfig(...))   →  Routing facade
   .route(opts)                    │  isinstance dispatch on config type
                                   ▼
                     GoogleRoutingConnector (subclasses BaseConnector)
                        .route(opts) → BaseConnector.send_post_json → Transport → Vendor
```

- **Per-operation facade.** `Routing`, `Matrix`, `Geocoding`, `Isochrone` each
  hold one connector and expose the operation method(s).
- **Config-as-selector.** There is no `provider_id` argument. The `<Provider>Config`
  dataclass type selects the provider; the facade's `isinstance` dispatch builds
  the matching connector. Anything that is not a config is a `TypeError`; an
  unsupported (provider, operation) pair falls through to a `ValueError` at
  construction — the dynamic-language analog of the siblings' compile-time
  provider unions. Both are misuse, outside the `ConnectorError` contract (see
  CONVENTIONS "Two kinds of failure").
- **`BaseConnector`** centralizes only the HTTP seam: the injectable `Transport`
  (default = non-redirect-following `UrllibTransport`), `send_get` /
  `send_post_json` / `send_post_form`, and transport-error normalization: a
  failure inside `Transport.send` → `provider_unavailable`, or `timeout` for a
  `TimeoutError` anywhere in its cause chain (status_code None, credential-redacted
  message string in `cause`); a raised non-2xx that carries a response
  (`raise_for_status()`, `HTTPError`) is rebuilt into an `HttpResponse` for the
  connector's own mapping. A transport without a callable `send` is a `TypeError`
  at construction, and `send` is resolved outside the broad handler, so a caller
  bug is never reported as an outage. No JSON parsing, no error mapping, no casing
  transforms.

## Location-distinctive invariants

1. **Normalization at the wire layer.** meters, seconds, `LatLng(lat, lng)`
   (lat-first), Google precision-5 polyline geometry. ESRI minutes→seconds (×60),
   km→m; HERE/Mapbox decode flex/precision-6 then re-encode precision-5.
2. **Four locked polyline utilities**, tested against
   `tests/data/parity-vectors.json`. Mapbox precision-6 decode stays private.
3. **Per-connector locality.** Vendor `map_vendor_error` + outliers live in
   `_<provider>.py`. Shared helpers (`provider_error` Retry-After surfacing,
   `merge_passthrough`, coordinate/JSON helpers) are building blocks connectors
   call, never global interception.
4. **`ProviderCode`** — 6 canonical + 7 location-extended, byte-identical string
   values. The raw `Retry-After` rides in `ConnectorError.cause["retryAfter"]`;
   parsed seconds are woven into `provider_message`. No `retry_after_seconds`.
5. **OSRM self-host invariants.** `base_url` required (validated by
   `_validate_base_url` at the top of each operation, before any HTTP). It
   enforces two rules: non-empty, and an `http://`/`https://` scheme, so a config
   typo is an `invalid_request` naming the config rather than an apparent outage.
   The default transport is HTTPS-only and refuses `http://` (and any other
   scheme) with its own `invalid_request`; a cleartext self-hosted endpoint needs
   an injected `Transport`. Neither message echoes a URL's userinfo, and
   `OsrmConfig.__repr__` masks it.
   A **path prefix is allowed** (`https://host/osrm` behind a reverse proxy is
   normal); trailing slashes are stripped so path concatenation cannot emit a
   double slash. `traffic_mode` and `departure_time` are ignored (no time
   parameter; traffic needs a rebuilt graph). Pre-flight typed errors:
   `unsupported_option` (avoid_* flags not declared in
   `supported_exclude_classes`), `invalid_request` (bad `base_url`). An open
   `/trip` pinning one endpoint is sent as-is (OSRM ≥ 5.27 supports first/any and
   any/last); only any/any is pinned to first/last, and an older server's
   `NotImplemented` maps to `unsupported_option`. Mapbox Optimization v1 is the one
   that refuses a single fixed endpoint up front. Profile mismatch →
   `profile_not_configured`.
6. **Async matrix outliers, transient within one `matrix` call.** HERE is always
   async (submit → poll → retrieve; validates the provider-returned
   `statusUrl`/`resultUrl` host is `*.hereapi.com` before attaching the key).
   TomTom is sync at ≤ 100 cells (the sync endpoint's default cap), else async. Backoff 1s → ×1.5 → capped 5s, 60s
   default deadline (`Passthrough.body["timeoutMs"]` override — a finite number > 0,
   else `invalid_request` — stripped from the wire). Deadline expiry → `matrix_polling_timeout` with `cause` carrying
   `matrixId`/`jobId`. The poll `sleep` is injectable (test seam).
7. **Grid-coverage assertions.** HERE/TomTom/OSRM/Mapbox/ESRI matrix connectors
   verify the vendor covered the full requested grid before flattening; a short
   payload → `unknown`. Google instead omits failed cells (kept in `raw`).

## Python-specific choices

- **Synchronous** API. The default transport is stdlib `urllib`; a consumer who
  wants `httpx`/`requests` injects a `Transport` — the library never imports a
  third-party HTTP client, keeping it zero-dependency.
- **Frozen dataclasses** for every DTO; `str`-valued `Enum`s for the typed
  vocabulary. Options dataclasses coerce their enum fields in `__post_init__`
  (`_coerce.py`): the member or its raw string is accepted and normalized to the
  member; any other string is a `ValueError`, a non-string a `TypeError` — never a
  silent fall-through to a connector's default branch.
- **`ConnectorError(Exception)`** raised (not returned) for operation failures;
  API misuse raises `TypeError`/`ValueError` instead.
