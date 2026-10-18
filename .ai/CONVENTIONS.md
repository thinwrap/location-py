# Conventions — thinwrap-location (Python)

## Layout (`src/thinwrap/location/`)

- **Shared concern per module**: `enums.py`, `errors.py` (`ConnectorError` +
  `ProviderCode` + `provider_error`/`invalid_request`/`unknown_error`),
  `latlng.py`, `coordinate.py`, `passthrough.py`, `polyline.py`,
  `isochrone_validate.py`, `transport.py`, `base.py`, `_util.py`, `_jsonpath.py`,
  `poll.py`, `_coerce.py` (options-dataclass vocabulary checks), `defaults.py`
  (published connector default lists).
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
  the config dataclasses, the four polyline functions, the transport types, and
  the `defaults.py` default-list tuples.
- Everything else is private (leading underscore modules / helpers). Connectors
  are not exported — the facade is the public API.
- `snake_case` fields (`total_distance_meters`, `provider_code`).

## Error mapping

Each provider has a private `_<provider>_map_vendor_error(status, body)` →
`ProviderCode`. Baseline: 400/404/422→invalid_request, 401/403→auth_failed (or
rate_limited on a quota signal), 429→rate_limited, 5xx→provider_unavailable,
transport→provider_unavailable (or `timeout`; status None), unparseable→unknown. Build the
error with `provider_error(status, headers, body, code, provider_message)` so the
Retry-After surfacing (raw → `cause["retryAfter"]`, parsed seconds →
`provider_message`) stays uniform. Pre-flight and 200-with-error-body cases raise
`ConnectorError(...)` directly.

## Two kinds of failure

1. **Operation failures are `ConnectorError`.** Vendor errors, transport failures
   and timeouts; **request data the library rejects before sending** — non-finite
   coordinates or radius, too few waypoints, empty origins/destinations, bad
   isochrone breaks, a malformed or unmappable country code, an unparseable date
   string, a bad `timeoutMs`, a missing or schemeless OSRM `base_url`, Esri
   credentials (neither → `auth_failed`, both → `invalid_request`); and
   **capability rejections** (`unsupported_*`). Raised by the operation call, never
   at construction, with `status_code` `None` and before any network I/O.
2. **API misuse is a bug in the calling code** and raises the native exception as
   early as possible — never a `ConnectorError`:
   - `TypeError`: a non-config (`None` included) passed to a facade, a `transport`
     without a callable `send`, a non-callable `sleep`, a wrong argument type (a
     non-string enum field, a bare-string `include`, a `departure_time` that is
     neither `datetime` nor `str` — or a `str` where only Esri parses one — a
     non-numeric `radius`).
   - `ValueError`: a provider that lacks the operation (facade construction); a
     string outside a closed vocabulary (`TravelMode`, `TrafficMode` —
     `none`/`traffic`/`live` —, `PolylineQuality`, `RoutingInclude` /
     `PlaceDetailsInclude` tokens, `IsochroneType`), raised by the options
     dataclass's `__post_init__` via `_coerce.py`. Valid raw strings are accepted
     and normalized to the member, so connectors compare against members only.

**Tie-breaker — the substitution test.** A pre-flight check exists to save a
round-trip, so it must never change the *type* of error the caller would have got
by sending anyway, only improve its code and message. If sending would have ended
in a vendor 4xx or a transport failure (both `ConnectorError`), the check raises
`ConnectorError`. If the library could not even form the request — it translates
the vocabulary itself, so no vendor ever sees `"flying"` — the check is native.
HERE's `transport_mode` is forwarded verbatim, so it stays vendor-side.

A consumer feeding end-user input into a closed vocabulary validates it at its own
boundary (`TravelMode(value)` in a `try`): the enums are the library's API, not
request data. `tests/test_error_contract.py` pins every row of this rule, each
native row with a negative "not a `ConnectorError`" assertion — extend it with any
new pre-flight check.

## `_passthrough`

Connectors build the request body (`dict`), headers, query, then call
`merge_passthrough(body, headers, pt, query)`. Body deep-merges (nested dicts
recurse; everything else replaces); headers/query shallow-merge; consumer wins.
Header names match case-insensitively (`merge_headers`), so a consumer header
replaces the connector's regardless of spelling instead of being sent twice.
Connector defaults (Mapbox `polygons=true`, matrix `annotations=duration,distance`)
go into the base dicts **before** the merge so a consumer can override them.

A list-valued default (a Google field mask, HERE `return`) is therefore replaced
wholesale by a consumer value — deliberately, so a default can be removed. Keep
every such list as a tuple in `defaults.py`, export it, and build the request
from that tuple, so the published list cannot drift from the wire.

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
