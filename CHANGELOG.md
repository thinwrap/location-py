# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Python port of the Thinwrap `location` scope — the Python sibling of
  `@thinwrap/location` (npm), `thinwrap/location` (Packagist), and
  `thinwrap/location-go`.
- Four operation facades (`Routing`, `Matrix`, `Geocoding`, `Isochrone`)
  dispatching to **21 connectors** across Google, Mapbox, HERE, ESRI, TomTom,
  and OSRM.
- Normalized surface: meters / seconds / `LatLng` (lat-first) / Google
  precision-5 polyline geometry, and a single typed `ConnectorError` with the
  cross-language 11-value `ProviderCode`.
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
