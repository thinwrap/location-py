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
- OD Cost Matrix: https://developers.arcgis.com/rest/routing/travelCostMatrix-service-direct/
- Geocoding service: https://developers.arcgis.com/rest/geocode/
- Service Area: https://developers.arcgis.com/rest/routing/serviceArea-service-direct/

---

## Routing

### Endpoint

`POST https://route-api.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/solve` — `application/x-www-form-urlencoded`.

### Input

Standard `RoutingOptions`. `optimize=True` maps to `findBestSequence=true`. Path geometry returned as coordinate arrays `[[[lng,lat],...]]`; encoded to standard polyline.

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

---

## Isochrone

### Endpoint

`POST .../ServiceArea_World/solveServiceArea`

### Input

Standard `IsochroneOptions`. `type` `IsochroneType.TIME` ⇒ `defaultBreaks` in minutes (`esriDriveTimeUnitsMinutes`; input seconds ÷ 60); `type` `IsochroneType.DISTANCE` ⇒ `defaultBreaks` in meters (`esriDriveDistanceUnitsMeters`, passed through).
