# Conventions — thinwrap-location (Python)

## Layout (`src/thinwrap/location/`)

- **Shared concern per module**: `enums.py`, `errors.py` (`ConnectorError` +
  `ProviderCode` + `provider_error`/`invalid_request`/`unknown_error`),
  `latlng.py`, `coordinate.py`, `passthrough.py`, `polyline.py`,
  `isochrone_validate.py`, `transport.py`, `base.py`, `_util.py`, `_jsonpath.py`,
  `poll.py`.
- **Operation surface per module**: `routing.py`, `matrix.py`, `geocoding.py`,
  `isochrone.py` (frozen-dataclass DTOs), plus `config.py` and `facades.py`.
- **Provider per module**: `_<provider>.py` (underscore-prefixed = private) holds
  all that provider's connectors + vendor helpers. Tests: `tests/test_<provider>.py`.
- `thinwrap` is a PEP 420 namespace package (no `__init__.py`); `thinwrap.location`
  has one that defines `__all__`.

## Naming

- Public (`__init__.__all__`): the 4 facades, `New`-free constructors (facades
  are classes), DTO dataclasses, `LatLng`, `Passthrough`, `ConnectorError`,
  `ProviderCode`, `ProviderID`/`TravelMode`/`IsochroneType`/`HereTransportMode`,
  the config dataclasses, the four polyline functions, and the transport types.
- Everything else is private (leading underscore modules / helpers). Connectors
  are not exported — the facade is the public API.
- `snake_case` fields (`total_distance_meters`, `provider_code`).

## Error mapping

Each provider has a private `_<provider>_map_vendor_error(status, body)` →
`ProviderCode`. Baseline: 400/404/422→invalid_request, 401/403→auth_failed (or
rate_limited on a quota signal), 429→rate_limited, 5xx→provider_unavailable,
transport→provider_unavailable (status None), unparseable→unknown. Build the
error with `provider_error(status, headers, body, code, provider_message)` so the
Retry-After surfacing (raw → `cause["retryAfter"]`, parsed seconds →
`provider_message`) stays uniform. Pre-flight and 200-with-error-body cases raise
`ConnectorError(...)` directly.

## `_passthrough`

Connectors build the request body (`dict`), headers, query, then call
`merge_passthrough(body, headers, pt, query)`. Body deep-merges (nested dicts
recurse; everything else replaces); headers/query shallow-merge; consumer wins.
Invariants a consumer must not override (Mapbox `polygons=true`, matrix
`annotations=duration,distance`) are re-stamped **after** the merge.

## Testing

- pytest, white-box (`from thinwrap.location import ...`).
- Inject `helpers.FakeTransport` via the facade `transport=`; it records requests
  and returns queued responses (`helpers.resp`). Assert the captured request
  (`path_of`/`qget`/`body_json`/`body_form`), the normalized result, and
  `ConnectorError` (`provider_code`, `status_code`, `provider_message`,
  `cause["retryAfter"]`). Never assert a `retry_after_seconds` attribute.
- Use `urlsplit` (not `urlparse`) to read request paths — `;` in a coord segment
  must not be parsed as URL params.
- Async-matrix tests pass `sleep=helpers.no_sleep`; the polling-timeout test uses
  the real sleep bounded by a 1 ms `timeoutMs`.
- The polyline parity test reads `tests/data/parity-vectors.json`.
