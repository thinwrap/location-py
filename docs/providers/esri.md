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

The service hosts are fixed (`route-api.arcgis.com`, `geocode-api.arcgis.com`), so ArcGIS Enterprise deployments cannot be reached through this connector — `passthrough` changes parameters, not the endpoint.

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

Standard `RoutingOptions`. Path geometry returned as coordinate arrays `[[[lng,lat],...]]`; encoded to standard polyline.

Any of `optimize`, `optimize_fixed_origin`, `optimize_fixed_destination` sends
`findBestSequence=true` with both preserve flags explicit — Esri defaults both to `true`
once sequencing is on:

| Flags | `preserveFirstStop` / `preserveLastStop` |
|---|---|
| `optimize` only, or both fixed flags | `true` / `true` |
| `optimize_fixed_origin` only | `true` / `false` |
| `optimize_fixed_destination` only | `false` / `true` |

`avoid_tolls` / `avoid_ferries` / `avoid_highways` map to the restrictions `Avoid Toll Roads`,
`Avoid Ferries` and `Avoid Limited Access Roads`. For driving (no `travelMode` sent — the
service default) they go in the top-level `restrictionAttributeNames`. When a travel-mode
object is sent (walking), Esri applies the mode's own `restrictionAttributeNames` and ignores
the top-level parameter, so the restrictions are appended to the mode's list instead. The
matrix's `avoid_tolls` follows the same rule.

Esri applies traffic whenever a start time is sent (typical traffic, or live within 4 hours
of now) and none without one, so the time follows `traffic_mode`:

| `traffic_mode` | `departure_time` | Sent |
|---|---|---|
| `NONE` (default) | any | no `startTime` — time-independent speeds |
| `TRAFFIC` | not set | `startTime=now` (live traffic where available) |
| `TRAFFIC` | set | `startTime` (epoch milliseconds) + `startTimeIsUTC=true` |

`startTimeIsUTC=true` matters because Esri's default (`false`) reads the value in the first
stop's local time zone. A naive `datetime` is taken as UTC. A string that does not parse as
ISO-8601 raises `invalid_request` before any request, even under `NONE`; a value that is
neither a `datetime` nor a string is a `TypeError` when the options are constructed.
Dropping the time under `NONE` also leaves stop `ArriveTime`/`DepartTime` in `raw` null and
skips Esri's time-referenced closure data. A `startTime` in `passthrough.body` still wins.

The connector sends `ignoreInvalidLocations=false`. Esri's default (`true`) drops a stop it
cannot locate on the network and routes the rest; with `false` the request fails instead, and
the in-body error naming the stop as *unlocated* maps to `no_route`.

### Per-leg values come from the stops FeatureSet, not from directions

Legs are differences of the per-stop **cumulative** costs
(`Cumul_TravelTime` / `Cumul_Kilometers`), so the connector sends `returnStops=true` +
`accumulateAttributeNames` (see [Replacing a default list](#replacing-a-default-list)) and
`returnDirections=false`. Esri documents the
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
| HTTP 200 + body `error.code == 400` whose `details[]` names an *unlocated* stop | `no_route` |
| HTTP 200 + body `error.code == 400` (otherwise) | `invalid_request` |
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

Standard `MatrixOptions`. `traffic_mode` follows the [routing rule](#input) with `timeOfDay`
in place of `startTime` (`solveODCostMatrix` has no `startTime` parameter): `NONE` sends no
time, `TRAFFIC` sends `timeOfDay=now`, or your time as epoch milliseconds with
`timeOfDayIsUTC=true`. A `timeOfDay` in `passthrough.body` still wins. Cells are decoded
from the `costAttributeNames` columns (`TravelTime` driving / `WalkTime` walking, plus
`Kilometers`); a response without a recognised time column and `Kilometers` raises `unknown`
(the column list in `cause`) rather than emitting zeros. A cell missing either cost is omitted.
A straight-line `odLines` answer is read the same way — `Total_TravelTime` (or
`Total_WalkTime` walking) and `Total_Kilometers`, unroutable or cost-less pairs omitted, an
out-of-range origin/destination id raising `unknown`. `travel_mode` cycling raises `ConnectorError` with `provider_code` `unsupported_travel_mode` (ESRI's hosted World service doesn't ship a cycling network). Use `passthrough.body["travelMode"]` JSON to pass a different travel mode object.

---

## Geocoding

### Endpoints

- Forward: `GET .../GeocodeServer/findAddressCandidates`
- Reverse: `GET .../GeocodeServer/reverseGeocode`
- Suggest: `GET .../GeocodeServer/suggest`

Reverse geocode answers a location with no address nearby as an in-body error 400 ("Unable to
find address for the specified location."); that is returned as an empty `candidates` list, not
an error. A place-details `magicKey` that resolves to no candidate, or to one without a
location, returns `candidate=None`; a stale `magicKey` (in-body error 400) stays
`invalid_request`.

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

Standard `IsochroneOptions`. `solveServiceArea` has no break-units parameter: `defaultBreaks`
are read in the units of the active impedance attribute.

- `IsochroneType.TIME` ⇒ `defaultBreaks` in minutes (input seconds ÷ 60) under the default
  `TravelTime` impedance (or `WalkTime` for walking).
- `IsochroneType.DISTANCE` ⇒ `defaultBreaks` in kilometres (input meters ÷ 1000) with
  `impedanceAttributeName=Kilometers`. For walking, the travel mode overrides that parameter, so
  the walking travel-mode object is sent with its own `impedanceAttributeName` set to
  `Kilometers` ("Walking Distance").

Breaks are sent to six decimals, and each returned contour's `value` is **your** value: 7 s goes
out as `0.116667` minutes, and converting Esri's `ToBreak` echo back would read 7.00002 s.

`outputPolygons` is `esriNAOutputPolygonDetailed` only when every break is within Esri's
detailed-polygon cap — 15 minutes (900 s) or 15 miles (24140 m); otherwise
`esriNAOutputPolygonSimplified`, whose cap is 300 minutes / 300 miles.

`departure_time` (an ISO-8601 string) is sent as `timeOfDay` (epoch milliseconds) with
`timeOfDayIsUTC=true`; an unparseable value raises `invalid_request` before any request.
Isochrones have no `traffic_mode`, so a `departure_time` makes the service area
traffic-aware (typical traffic, or live within 4 hours of now); omit it for time-independent
speeds.

### Output

A service-area polygon's `rings` mix exterior rings (clockwise) and holes (counter-clockwise) in
no guaranteed order. The connector classifies them by winding and attaches each hole to the
exterior that contains it; one exterior gives a `Polygon`, several give a `MultiPolygon`. Holes
are kept, and rings are re-wound to RFC 7946 (exterior counter-clockwise, holes clockwise).

---

## Replacing a default list

Passthrough **replaces** a list-valued parameter the connector sets; it does not merge with it.
That is deliberate — a merge would leave you no way to remove a value. To add one, start from
the published default, the same tuple the connector builds its request from.
`accumulateAttributeNames` is a comma-joined form field, so `passthrough.body` replaces it whole:

| Operation | Constant | Sent when |
|---|---|---|
| Routing | `ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING` = `("TravelTime", "Kilometers")` | driving (the default) |
| Routing | `ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_WALKING` = `("WalkTime", "Kilometers")` | `travel_mode=WALKING` |
| Matrix | `ESRI_MATRIX_ACCUMULATE_ATTRIBUTES` = `("Kilometers",)` | always (`TravelTime` is the impedance) |

```python
from thinwrap.location import ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING, Passthrough, RoutingOptions

names = ",".join(ESRI_ROUTING_ACCUMULATE_ATTRIBUTES_DRIVING + ("Miles",))
routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(body={"accumulateAttributeNames": names}),
))
```

Pick the constant that matches the travel mode: Esri only accumulates a time attribute the
active impedance uses, and silently returns nothing for the other one.

Legs are always read from `Cumul_<time>` for the mode's time attribute (`TravelTime` driving,
`WalkTime` walking) and `Cumul_Kilometers`, by name. Extra attributes you accumulate —
`Miles`, `TruckTravelTime`, anywhere in the list — land in `raw` and never change which fields
feed the legs.

> **Omitting a value the normalizer reads.**
>
> - Routing: per-leg values are differences of the stops' `Cumul_<time>` and `Cumul_Kilometers`.
>   If your list drops one, the connector falls back to matching the remaining `Cumul_*` keys by
>   shape (a `Miles` column still works as the distance), and failing that to the route's
>   `Total_*` values split **evenly** across legs — the totals stay right but per-leg values do
>   not. Drop every distance attribute and distances come back `0`. Nothing raises.
> - Matrix: the `Kilometers` column fills `distance_meters`. Without it the call raises `unknown`
>   (the column list in `cause`) rather than reporting 0 m.

### Geocoding `outFields`

Forward geocode and place details send `outFields=*`. That is a wildcard, not a list, so there is
no constant to extend. `outFields` only controls the `attributes` object on each candidate, and
the normalizer reads **none** of those attributes: `formatted_address` comes from the candidate's
`address`, `location` from `location.x`/`location.y`, and `viewport` from `extent`, all of which
Esri returns whatever `outFields` says. Narrowing `outFields` through `passthrough.query` therefore
changes only `raw`. Reverse geocode and suggest do not send it.
