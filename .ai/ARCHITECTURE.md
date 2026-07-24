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
  the matching connector. An unsupported (provider, operation) pair falls through
  to a `ValueError` at construction — the dynamic-language analog of the siblings'
  compile-time provider unions.
- **`BaseConnector`** centralizes only the HTTP seam: the injectable `Transport`
  (default = non-redirect-following `UrllibTransport`), `send_get` /
  `send_post_json` / `send_post_form`, and transport-error → `provider_unavailable`
  (status_code None, credential-redacted message, raw error in `cause`). No JSON
  parsing, no error mapping, no casing transforms.

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
4. **`ProviderCode`** — 6 canonical + 5 location-extended, byte-identical string
   values. The raw `Retry-After` rides in `ConnectorError.cause["retryAfter"]`;
   parsed seconds are woven into `provider_message`. No `retry_after_seconds`.
5. **OSRM self-host invariants.** `base_url` required (validated before any HTTP).
   Pre-flight typed errors: `unsupported_field` (departure_time),
   `unsupported_option` (avoid_* flags), `invalid_request` (illegal `/trip`
   combo). Profile mismatch → `profile_not_configured`.
6. **Async matrix outliers, transient within one `matrix` call.** HERE is always
   async (submit → poll → retrieve; validates the provider-returned
   `statusUrl`/`resultUrl` host is `*.hereapi.com` before attaching the key).
   TomTom is sync at ≤ 2500 cells, else async. Backoff 1s → ×1.5 → capped 5s, 60s
   default deadline (`Passthrough.body["timeoutMs"]` override, stripped from the
   wire). Deadline expiry → `matrix_polling_timeout` with `cause` carrying
   `matrixId`/`jobId`. The poll `sleep` is injectable (test seam).
7. **Grid-coverage assertions.** HERE/TomTom/OSRM/Mapbox/ESRI matrix connectors
   verify the vendor covered the full requested grid before flattening; a short
   payload → `unknown`. Google instead omits failed cells (kept in `raw`).

## Python-specific choices

- **Synchronous** API. The default transport is stdlib `urllib`; a consumer who
  wants `httpx`/`requests` injects a `Transport` — the library never imports a
  third-party HTTP client, keeping it zero-dependency.
- **Frozen dataclasses** for every DTO; `str`-valued `Enum`s for the typed
  vocabulary (values compare equal to their string, so passing the enum or its
  raw string both work).
- **`ConnectorError(Exception)`** raised (not returned).
