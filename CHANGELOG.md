# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] — 2026-07-28

One release carrying both the initial Python port and the 1.2.0 scope, so *Changed* and *Fixed*
are framed against the **TypeScript and PHP 1.1.0 behaviour** — there is no earlier Python
release to diff against. Per-provider detail lives in `docs/providers/`.

### Added

- `country_filter` on `autocomplete()` — all five geocoders. Google's ccTLD codes (`GB` → `uk`)
  and 15-code cap are handled; it also suppresses Google's *query* predictions.
- `place_details()` on the `Geocoding` facade and all five geocoding connectors — resolves an
  `autocomplete()` `place_id` to a `GeocodeCandidate`. `PlaceDetailsInclude.NAME` gates Google's
  `displayName`, whose field mask drives the SKU tier.
- `session_token` on Google `autocomplete()`/`place_details()` and Mapbox `place_details()` —
  both vendors bill per **session** with one and per request without, so a keystroke-driven UI
  without a token is billed once per character typed.
- `structured_format` on `AutocompletePrediction` (`main_text`/`secondary_text`). Never
  synthesized, so `None` for TomTom street results and for Esri entirely.
- `ProviderCode.NO_ROUTE` — the provider answered but no route exists, normalizing six different
  vendor signals (200/400/422, in-body codes, empty arrays).
- `ProviderCode.TIMEOUT` — the default `Transport` already carried a 30-second timeout; a
  failure that is or wraps a `TimeoutError` is now classified as `TIMEOUT`. The `__cause__`
  walk matters because `urllib` wraps a read timeout in a `URLError`.
- `RoutingOptions.polyline_quality` (`PolylineQuality.SIMPLIFIED`/`.DETAILED`), default
  simplified — ~30x smaller on Mapbox and OSRM with distances and durations byte-identical. No
  such control exists on HERE/TomTom/Esri.
- `traffic_mode` (`TrafficMode.NONE`/`.LIVE`) on routing and matrix, default none — see
  *Changed*.
- `include: list[RoutingInclude]`, default empty; first token `DURATION_WITHOUT_TRAFFIC`
  populates `duration_without_traffic_seconds` on `RoutingLeg` and
  `total_duration_without_traffic_seconds` on `RoutingResult`. Native on Google/HERE/TomTom,
  never synthesized.
- `OsrmConfig.supported_exclude_classes` — declare what your build was compiled with to enable
  `avoid_tolls`/`avoid_ferries`/`avoid_highways`.

### Changed

- **Google no longer sends `routingPreference: TRAFFIC_AWARE` for a bare `departure_time`**, on
  routing and matrix. It is a Pro-tier SKU feature and matrix bills per element, so a 10x10
  request moved 100 billed elements. Now driven by `TrafficMode.LIVE`.
- **TomTom now sends `traffic=false` explicitly** — its default was ON. Results differ unless
  `TrafficMode.LIVE` is passed.
- Mapbox and OSRM routing default to `overview=simplified` (was `full`) and no longer send
  `steps` or `annotations`.
- HERE `findsequence2` honours `avoid_tolls` and takes traffic from `traffic_mode`. Previously
  the optimizer ordered waypoints as if tolls were acceptable while the follow-up `/routes`
  call avoided them.

### Fixed

- `waypoint_order` could be silently corrupt on Google, TomTom, OSRM and Mapbox. All four now
  validate against the **input** waypoint count and omit the field unless it is a complete
  permutation of `[0..N-1]`.
- Geocoding no longer emits a fabricated `(0,0)` candidate for absent or non-numeric coordinates
  across Google, HERE, TomTom and Esri — a `None` coerced to `0.0` produced a plausible-looking
  point off the coast of Africa.
- HERE `autocomplete()` with no `location` sent a request Autosuggest rejects; it now raises
  `invalid_request`, and a `passthrough.query["at"]` or `["in"]` you supply counts as the
  context.
- ESRI legs now come from the stops output (`returnStops` + `Cumul_*` differences) instead of
  the superseded `directions` FeatureSet, so legs reconcile to the totals by construction.
  `raw` no longer carries `directions`, and `waypoint_order` is emitted only when optimizing.
- Mapbox `geometries` is no longer decoupled from the decoder — overriding it to `polyline`
  divided every coordinate by 10 silently. `geometries=geojson` now works.
- OSRM `base_url` requires an `http(s)://` scheme; without one the transport raised `URLError`
  and reported `provider_unavailable`, indistinguishable from an outage. Path prefixes are
  supported.
- `no_route` now covers OSRM/Mapbox `NoRoute`/`NoTrips`/`NoSegment`, an empty `legs[]`, and OSRM
  `Ok` with an empty `routes[]`. Esri requires `unlocated` in `error["details"]` — other in-body
  400s stay `invalid_request`.

### Documentation

- Per-geocoder "Country filter" sections, and match-highlighting offsets documented on Google
  and HERE with their absence stated on Mapbox, TomTom and Esri. Not normalized: only 2 of 5
  return them and the shapes are incompatible. The offsets count Unicode code points, which is
  what Python's string indices count — the one language here where the obvious slice is correct.
- Per-provider "Turn-by-turn instructions" sections. TBT is off by default and not normalized,
  and Google's `X-Goog-FieldMask` and HERE's `return` are **replaced** rather than merged — so
  a partial override silently zeroes every normalized distance, duration and polyline. The
  shipped Google example had exactly that bug; fixed.

### Internal

- `place_details` went straight onto each geocoding connector — there is no exported protocol
  for consumers to implement, so the TS sibling's optional method and the PHP sibling's separate
  interface have no Python counterpart. Nor do their OSRM call-time-validation and BYO-timeout
  fixes: Python already validated inside `route()`/`matrix()` and already classified on the
  exception itself. New optional dataclass fields are appended last, after `passthrough`.

### Initial port

- Initial Python port of the Thinwrap `location` scope — the Python sibling of
  `@thinwrap/location` (npm), `thinwrap/location` (Packagist), and
  `thinwrap/location-go`.
- Four operation facades (`Routing`, `Matrix`, `Geocoding`, `Isochrone`)
  dispatching to **21 connectors** across Google, Mapbox, HERE, ESRI, TomTom,
  and OSRM.
- Normalized surface: meters / seconds / `LatLng` (lat-first) / Google
  precision-5 polyline geometry, and a single typed `ConnectorError` with the
  cross-language `ProviderCode` (11 values as ported; the two under *Added* take it
  to 13).
- Four polyline utilities (`encode_polyline`, `decode_polyline`,
  `decode_flex_polyline`, `encode_esri_paths`) verified against the shared
  cross-language parity vectors.
- Synchronous API with a bring-your-own `Transport` protocol; default transport
  is standard-library `urllib` (never follows redirects). Zero runtime
  dependencies.
- Google routing/matrix classify an invalid or restricted API key as
  `auth_failed` via the `google.rpc.ErrorInfo` `reason` in `error.details[]`
  (Google returns HTTP 400 for bad keys), falling back to the HTTP-status
  mapping when no `ErrorInfo` is present.
- Per-provider documentation under `docs/providers/` (one page per provider),
  mirroring the TypeScript and PHP sibling READMEs.
