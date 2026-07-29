# ESRI ArcGIS Connectors (Python)

ESRI ArcGIS Location Services connectors for routing, distance matrix, geocoding, and isochrone (service areas) via direct HTTP calls.

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `EsriConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, Geocoding, Isochrone, EsriConfig

routing = Routing(EsriConfig(api_key=os.environ["ARCGIS_KEY"]))
matrix  = Matrix(EsriConfig(api_key=key))
geo     = Geocoding(EsriConfig(api_key=key))
iso     = Isochrone(EsriConfig(api_key=key))
```

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `api_key` | `str` | one of | ArcGIS API key (long-lived) — mutually exclusive with `arcgis_token` |
| `arcgis_token` | `str` | one of | OAuth-issued access token — mutually exclusive with `api_key` |

Provide exactly one of `api_key` or `arcgis_token`: supplying both raises `ConnectorError` (`provider_code` `invalid_request`); supplying neither raises `ConnectorError` (`provider_code` `auth_failed`).

Inject a custom transport with `Routing(EsriConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

## Auth setup

Create an API key at https://developers.arcgis.com/api-keys/. Sent as `token=` form field (NAServer endpoints) or query param (GeocodeServer). Token lifecycle: **refreshable** — long-lived API keys, but OAuth-issued tokens require client-side refresh.

ArcGIS Enterprise on-prem deployments are supported by overriding endpoints in `passthrough.headers`/`query` — point at your tenant's portal URL.

## Vendor docs

- Route service: https://developers.arcgis.com/rest/routing/route-service-direct/
- **Response fields (the one that is easy to miss):** https://doc.esri.com/en/arcgis-pro/latest/tool-reference/ready-to-use/output-findroutes.html
  — the REST page above documents **parameters only**, and its "Types" link is *input*
  data types. Every output FIELD (`Cumul_*`, `Status`, `DistanceToNetworkInMeters`,
  `DirectionPointType`) is defined here, in the ArcGIS Pro tool reference.
- OD Cost Matrix: https://developers.arcgis.com/rest/routing/travelCostMatrix-service-direct/
- Geocoding service: https://developers.arcgis.com/rest/geocode/
- Service Area: https://developers.arcgis.com/rest/routing/serviceArea-service-direct/

---

## Routing

### Endpoint

`POST https://route-api.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/solve` — `application/x-www-form-urlencoded`.

### Input

Standard `RoutingOptions`. `optimize=True` maps to `findBestSequence=true`. Path geometry returned as coordinate arrays `[[[lng,lat],...]]`; encoded to standard polyline.

### Per-leg values come from the stops FeatureSet, not from directions

Legs are differences of the per-stop **cumulative** costs
(`Cumul_TravelTime` / `Cumul_Kilometers`), so the connector sends `returnStops=true` +
`accumulateAttributeNames` and `returnDirections=false`. Esri documents the
turn-by-turn output as **superseded** — *"Legacy: This output type has been superseded
by the DirectionPoints and DirectionLines output classes, which should be used for all
new scripts and workflows"* — and its `esriDMT*` maneuver values are not enumerated in
the REST reference at all.

Three consequences:

- Legs sum to the totals exactly, since both come from the same cumulative series.
- **`res.raw` contains no `directions`.** For turn-by-turn text, request it through
  `_passthrough` (`returnDirections`, `directionsOutputType`) and prefer
  `esriDOTFeatureSets`, whose `DirectionPointType` is a documented integer enum —
  Arrive (50), Depart (51), Straight (52) … — over the legacy `esriDMT*` strings.
- The cumulative field name follows the active impedance (`Cumul_TravelTime` driving,
  `Cumul_WalkTime` walking). The connector discovers the key; this only matters if you
  read `res.raw` yourself.

### Out-of-network coordinates: read the snap distance

Each stop in `res.raw` carries `DistanceToNetworkInMeters` — how far the coordinate was
moved to reach a routable road — plus a `Status` code (`0` OK, `1` Not Located, `5` Not
Reached, `7` Not located on closest).

A coordinate far from any road still yields a well-formed route to wherever it snapped,
so that distance is the only signal it happened. The acceptable threshold is application
policy — a few hundred metres is normal for a rural pickup and disqualifying for a city
address — so the library does not pick one. This is the Esri analogue of OSRM's
`waypoints[].distance`.

### Error mapping

| Vendor signal | `ProviderCode` |
|---|---|
| HTTP 200 + body `error.code == 498`/`499` | `auth_failed` |
| HTTP 200 + body `error.code == 400` | `invalid_request` |
| HTTP 401 / 403 | `auth_failed` |
| HTTP 429 | `rate_limited` |
| HTTP 5xx | `provider_unavailable` |

### Retry-After

ESRI's API tier may or may not document `Retry-After` (depends on subscription). When present on HTTP 429, surfaced via `cause["retryAfter"]` + `provider_message`.

### Turn-by-turn instructions

**Not normalized** — `RoutingResult` has no `steps` attribute. Unlike the other five providers this
is a *re-enable* rather than an opt-in: the service default is `returnDirections=true`, and the
connector sends `false` explicitly so the payload is not shipped on every call.

```python
res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(body={"returnDirections": "true"}),
))
```

Directions land at `raw["directions"][]["features"][]["attributes"]` — `text`, `maneuverType`,
`length`, `time`. `returnDirections` is its own form field, so this merges additively (values are
stringified, so `True` works as well as `"true"`).

> **Esri documents this output as superseded**, in favour of the DirectionPoints and DirectionLines
> output classes, which it recommends "for all new scripts and workflows". Its `esriDMT*`
> `maneuverType` enumeration is not published in the REST reference at all — only in the Runtime
> SDK references and legacy JS 3.x docs. Legs and totals in `RoutingResult` come from the `stops`
> cumulative costs (`Cumul_*`) precisely so the normalized path does not depend on this surface.

---

## Matrix

### Endpoint

`POST .../OriginDestinationCostMatrix_World/solveODCostMatrix`

### Input

Standard `MatrixOptions`. `travel_mode` cycling raises `ConnectorError` with `provider_code` `unsupported_travel_mode` (ESRI's hosted World service doesn't ship a cycling network). Use `passthrough.body["travelMode"]` JSON to pass a custom-published travel mode object for ArcGIS Enterprise deployments that provide one.

---

## Geocoding

### Endpoints

- Forward: `GET .../GeocodeServer/findAddressCandidates`
- Reverse: `GET .../GeocodeServer/reverseGeocode`
- Suggest: `GET .../GeocodeServer/suggest`

### Country filter

`country_filter` (ISO 3166-1 alpha-2 — ESRI uses alpha-2 directly) is translated to
`countryCode=<comma-csv>` on **forward geocode and suggest alike**.

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen", country_filter=["IL", "PS"]))
# → ...&countryCode=IL,PS
```

### `suggest` returns the least of the five

A suggestion carries only `text` and `magicKey`. That is why ESRI is the one provider where
`structured_format` is always `None` (there is no distinct main part to split out), and it
returns no match-highlighting offsets and no result types either — of the five geocoders
only Google and HERE return offsets. `magicKey` becomes `place_id`.

---

## Isochrone

### Endpoint

`POST .../ServiceArea_World/solveServiceArea`

### Input

Standard `IsochroneOptions`. `type` `IsochroneType.TIME` ⇒ `defaultBreaks` in minutes (`esriDriveTimeUnitsMinutes`; input seconds ÷ 60); `type` `IsochroneType.DISTANCE` ⇒ `defaultBreaks` in meters (`esriDriveDistanceUnitsMeters`, passed through).
