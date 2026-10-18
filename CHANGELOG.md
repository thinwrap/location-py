# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-10-18

### Added

- `HereTransportMode.BUS` — a distinct mode from `PRIVATE_BUS`, not a synonym.
- Default-list constants (`GOOGLE_*_FIELD_MASK*`, `HERE_ROUTING_RETURN`, `HERE_MATRIX_ATTRIBUTES`, `MAPBOX_MATRIX_ANNOTATIONS`, `OSRM_TABLE_ANNOTATIONS`, `ESRI_*_ACCUMULATE_ATTRIBUTES*`) so a passthrough that replaces a list can extend it.
- `TrafficMode.TRAFFIC`: the traffic expected at `departure_time` (now when none is given).

### Changed

- `place_details()` returns `candidate=None` for an unknown place instead of raising `no_route` — callers must null-check.
- `passthrough.body["timeoutMs"]` must be a finite number > 0; anything else raises `invalid_request` instead of falling back.
- Isochrone `departure_time` must be an ISO-8601 string or `datetime` on every provider; an unparseable string raises `invalid_request` on Mapbox, HERE and TomTom like Esri.
- An unknown `travel_mode` / `traffic_mode` / `polyline_quality` / `include` token / isochrone `type` now raises `ValueError` when the options are built instead of silently defaulting; valid raw strings are normalized to the enum member.
- API misuse raises `TypeError`, never `ConnectorError`: a non-config facade argument (was `AttributeError`), a `transport` without `send()`, a non-string enum value, a `departure_time` of the wrong type (was `AttributeError`, or `invalid_request` on Esri and isochrones).
- `RoutingOptions.include` / `PlaceDetailsOptions.include` are normalized to a tuple; a bare string raises `TypeError` instead of being read per character.
- Mapbox isochrone breaks that round below 1 minute/meter or onto the same value raise `invalid_request`.
- Isochrone contour geometry may now be a `MultiPolygon` (callers reading `coordinates` must check `type`); holes are kept and rings follow RFC 7946 winding.
- With a `departure_time` and `TrafficMode.NONE`, Esri, Mapbox Directions and TomTom matrix now return time-independent durations instead of typical traffic for that time.
- OSRM, Mapbox Matrix and Mapbox Optimization no longer reject a `departure_time` under `TrafficMode.NONE` (nor, on Mapbox, for walking/cycling).

### Deprecated

- `TrafficMode.LIVE` — an alias of `TrafficMode.TRAFFIC` with identical behaviour; kept, never removed.

### Fixed

- The default transport sends a `thinwrap-location-python` User-Agent instead of urllib's, which Cloudflare-fronted APIs refuse with a 403.
- Google routing and matrix no longer fail with HTTP 400 when a `departure_time` is given under the default `TrafficMode.NONE`.
- TomTom matrix honours `traffic_mode` (`options.traffic`, plus `departAt: "now"` when live without a time); `LIVE` was ignored.
- OSRM matrix honours `avoid_tolls` when `"toll"` is a declared exclude class, as routing does.
- Google matrix raises `unknown` on an empty answer; an element with neither `status` nor `condition` is a failed cell, not 0 m / 0 s.
- TomTom matrix raises `unknown` on a body without a `data` array or a cell without valid indexes.
- TomTom async matrix follows the 302 to a large result's download URL (without the API key) instead of raising `unknown`.
- HERE optimized routing orders stops under all avoid flags (`motorway`, `boatFerry`/`railFerry`), not just tolls.
- Mapbox isochrone breaks are sent ascending and contours carry the caller's value (90 s no longer reads back as 120 s).
- Esri isochrone contours carry the caller's value, without the minutes round-trip drift.
- Google geocoding `OVER_DAILY_LIMIT` maps to `auth_failed` and `UNKNOWN_ERROR` to `provider_unavailable`.
- OSRM `/trip` `NoTrips` on an HTTP 400 maps to `no_route`, as in a 200 envelope.
- OSRM and Mapbox matrix map `NoRoute`/`NoSegment` to `no_route` on a 4xx and in a 200 body.
- In-body errors carry the HTTP status they arrived with.
- A 2xx body a connector cannot interpret raises `unknown` instead of an empty result or zero-valued data.
- JSON bodies with `NaN`/`Infinity` tokens are rejected as malformed.
- A partial viewport is omitted rather than 0-filled (HERE, TomTom, Mapbox, Esri).
- Isochrones without a usable polygon or contour value raise `unknown` instead of an empty ring.
- Esri reverse geocode "Unable to find address" returns no candidates instead of `invalid_request`.
- Esri `odLines` matrix reads the walking time column, bounds-checks ids and omits failed cells.
- Passthrough headers replace connector headers case-insensitively instead of being sent twice.
- A `transport` without a working `send` no longer reports every call as a retryable `provider_unavailable`.
- A NaN/inf autocomplete `radius` raises `invalid_request` instead of a bare `ValueError`/`OverflowError` (HERE, TomTom).
- Mapbox routing with fewer than two waypoints, and Google/HERE/TomTom matrix with no origins or destinations, raise `invalid_request` before sending.
- Esri routing legs read the mode's time attribute and `Kilometers` by name; extra accumulated attributes could be misread as minutes.
- HERE optimized routing carrying a `departure_time` failed outright (regression in 1.2.0);
  `findsequence2` rejects the millisecond `departure` it emitted.
- A BYO `Transport` that raises on a non-2xx no longer collapses every HTTP error into
  `provider_unavailable` with no status.
- HERE `findsequence2` error text is no longer discarded; its legacy envelope is parsed.
- Mapbox `depart_at` emitted a millisecond value Mapbox does not document. Now seconds.
- Esri departure times were read in the stop's local time zone; now flagged UTC, and matrix sends `timeOfDay`.
- Esri distance isochrones treated meters as minutes; they now use a Kilometers impedance.
- Esri isochrones over 15 min / 15 mi no longer request detailed polygons past Esri's cap.
- Esri routing no longer silently skips a stop it cannot locate; it raises `no_route`.
- TomTom matrix switches to async above 100 cells, the sync endpoint's standard limit.
- TomTom isochrone rejects walking/cycling up front; `calculateReachableRange` forbids them.
- Mapbox optimize with only one fixed endpoint on an open trip now raises `unsupported_option`.
- OSRM sends a single fixed trip endpoint as asked (supported since v5.27); `NotImplemented` maps to `unsupported_option`.
- Mapbox optimized routing refuses avoid flags, and `departure_time` with driving traffic, which Optimization v1 rejects.
- Mapbox Directions sends `depart_at` only on the `driving-traffic` profile.
- Mapbox autocomplete honours `session_token`; place details always sends one.
- HERE legs no longer split at ferry/shuttle sections; one leg per waypoint pair.
- TomTom routing with two or more avoid flags failed with 400; `avoid` is now a repeated param.
- Esri walking avoid flags were ignored; they now go inside the travel mode's restrictions.
- Esri `optimize_fixed_origin`/`_destination` now free the other end and trigger sequencing on their own.
- TomTom `optimize_fixed_origin`/`_destination` alone now request `computeBestOrder`.
- Mapbox walking/cycling avoid flags no longer 422; only `ferry` is sent for those profiles.
- Mapbox traffic (`TrafficMode.TRAFFIC` / `LIVE`) now selects the `driving-traffic` profile on Directions and Matrix.
- HERE routing and matrix no longer apply live traffic unless `traffic_mode=TrafficMode.TRAFFIC` (or `LIVE`), so routes bill at the basic rate instead of Time-Aware.
- Mapbox Matrix refuses `avoid_tolls`, and `departure_time` with driving traffic, instead of silently dropping them.
- Mapbox round trips send `destination=last` when `optimize_fixed_destination` is set.
- Mapbox routing 422 `InvalidInput` maps to `invalid_request`, not `unknown`.
- HERE `formatted_address` is the full `address.label`, not a POI's `title`.
- Google autocomplete with a bad API key raises `auth_failed`, not `invalid_request`.
- Google geocode with more than one `country_filter` entry raises `unsupported_option`; Google applies only the first.
- Esri matrix raises `unknown` on an unrecognised cost column instead of decoding zeros.
- OSRM `base_url` userinfo credentials are masked in error messages and `repr`.
- Config `repr()` no longer prints API keys or access tokens.
- The default transport refuses `http://` with an actionable `invalid_request`, not `provider_unavailable`.

### Documentation

- Esri docs no longer claim ArcGIS Enterprise is reachable via `passthrough`; the hosts are fixed.
- README and OSRM docs show an `https://` base URL and the injected-Transport route for `http://`.
- `TAXI` and `PRIVATE_BUS` were wrongly marked "routing only"; Matrix v8 accepts both.
- HERE docs: `PRIVATE_BUS` is incompatible with `optimize=True` (`findsequence2` 400s), plus
  `taxi[allowDriveThroughTaxiRoads]` and `networkRestrictedTruck` via `passthrough`.

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
