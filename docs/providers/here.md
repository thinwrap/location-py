# HERE Connectors (Python)

HERE Location Services connectors for routing, distance matrix, geocoding, and isochrone via direct HTTP calls.

## Quick install

See the [package README](../../README.md) for installation. The **config type selects the provider** — pass `HereConfig` to any facade it supports:

```python
from thinwrap.location import Routing, Matrix, Geocoding, Isochrone, HereConfig

routing = Routing(HereConfig(api_key=os.environ["HERE_KEY"]))
matrix  = Matrix(HereConfig(api_key=key))
geo     = Geocoding(HereConfig(api_key=key))
iso     = Isochrone(HereConfig(api_key=key))
```

## Configuration

| Field | Type | Required | Notes |
|---|---|---|---|
| `api_key` | `str` | yes | HERE API key (REST) — single key works across Router v8, Matrix v8, Geocode/Revgeocode/Autocomplete, Isolines v8 |

Inject a custom transport with `Routing(HereConfig(...), transport=my_transport)` (see the package README); the default `urllib` transport never follows redirects.

## Auth setup

Provision a project at https://platform.here.com/ and create a REST API key. Sent as `apiKey=` query param on every request. Static — no refresh.

## Vendor docs

- Routing v8: https://docs.here.com/routing/docs/routing-v8-intro
- Matrix Routing v8: https://docs.here.com/routing/reference/postmatrix
- Geocoding & Search: https://docs.here.com/geocoding-and-search/reference/
- Isoline Routing v8: https://docs.here.com/routing/docs/isoline-v8-intro
- Pricing & rate limits: https://www.here.com/pricing

---

## Routing

### Endpoints

- Standard routing: `GET https://router.hereapi.com/v8/routes`
- Waypoint sequence: `GET https://wps.hereapi.com/v8/findsequence2`

### Input

`optimize=True` triggers the two-step `findsequence2` → `routes` flow. Travel mode maps to HERE `transportMode`. Intermediate waypoints are added as `via=lat,lng` query parameters.

The avoid flags reach **both** legs, so the stops are ordered under the same constraints the
route honours. `findsequence2` takes them as road features in one comma-delimited segment of its
`mode` string, `-3` meaning "strictly avoid": `avoid_tolls` ⇒ `tollroad:-3`, `avoid_highways` ⇒
`motorway:-3`, `avoid_ferries` ⇒ `boatFerry:-3,railFerry:-3` — e.g.
`mode=fastest;car;traffic:disabled;tollroad:-3,motorway:-3`.

`legs` has one entry per consecutive waypoint pair. HERE `sections` are not legs: a section
also ends at an implicit mode change, so a ferry or car shuttle inside a driving route becomes
its own transit section. The connector closes a leg after each section whose
`arrival.place.waypoint` is set (a `via` from the request) and after the final section,
summing the grouped sections' distance and duration.

`transport_mode` (a `HereTransportMode`) overrides the base `travel_mode` mapping with HERE's own
vehicle classes:

| Value | `/v8/routes` | `findsequence2` (`optimize=True`) |
|---|---|---|
| `CAR`, `TRUCK`, `PEDESTRIAN`, `BICYCLE`, `SCOOTER`, `TAXI` | yes | yes |
| `BUS` | yes | yes |
| `PRIVATE_BUS` | yes | **no — HTTP 400** |

`BUS` and `PRIVATE_BUS` are **not synonyms**. `BUS` may drive through bus-restricted and
bus-exclusive streets; `PRIVATE_BUS` uses those streets only where a waypoint sits on one, which
is the private pick-up / drop-off case. HERE lists `BICYCLE`, `BUS` and `PRIVATE_BUS` as Beta.

Because optimization runs through the legacy `findsequence2` endpoint, whose `mode` grammar
accepts only `car`, `truck`, `pedestrian`, `bus`, `bicycle`, `scooter` and `taxi`, combining
`PRIVATE_BUS` with `optimize=True` fails with `invalid_request` and the provider message
`Unknown transport mode 'privateBus'`. The connector forwards the value verbatim rather than
substituting `BUS` for you — pick `BUS` yourself when you need optimization.

HERE's routing enum has one further value, `networkRestrictedTruck`, deliberately omitted here:
`findsequence2` and Matrix v8 both reject it, and `/v8/routes` returns 400
`Missing 'networkRestrictedTruck[permittedNetworks]' parameter` unless that companion parameter
is supplied, which the connector does not model. Reach it through `passthrough.query` together
with `networkRestrictedTruck[permittedNetworks]` if you need it.

Taxi-specific tuning is likewise `passthrough.query`; HERE's bracketed keys pass through
verbatim:

```python
routing.route(RoutingOptions(
    waypoints=[origin, destination],
    transport_mode=HereTransportMode.TAXI,
    # Default is true. When true, HERE also ignores traffic on roads with taxi
    # lanes, on the assumption a taxi can bypass the congestion.
    passthrough=Passthrough(query={"taxi[allowDriveThroughTaxiRoads]": "false"}),
))
```

> `passthrough.query` is merged into **both** legs of the optimized flow. `findsequence2`
> ignores query parameters it does not recognize (verified live), so a `/v8/routes`-only key
> such as `taxi[...]` rides along harmlessly rather than failing the sequence call.

### Traffic and billing

Traffic is **off unless you pass `traffic_mode=TrafficMode.TRAFFIC`** (or the deprecated alias `LIVE`). HERE's own default is the
opposite: a request without `departureTime` is computed for the current time, against live
traffic. That also decides the price — HERE bills `/v8/routes` as **Time-Aware Routing**
(fewer free requests and a higher per-1k rate, see [HERE pricing](https://www.here.com/get-started/pricing))
rather than basic **Routing** unless the request carries `departureTime=any` or `traffic[mode]=disabled`.

| `traffic_mode` | `departure_time` | Sent on `/v8/routes` |
|---|---|---|
| `NONE` (default) | not set | `departureTime=any` |
| `NONE` (default) | set | `departureTime=<ISO>&traffic[mode]=disabled` |
| `TRAFFIC` | not set | nothing — HERE applies live traffic |
| `TRAFFIC` | set | `departureTime=<ISO>` |

`departureTime=any` ignores dynamic traffic but still applies long-term closures.
`traffic[mode]=disabled` is the only way to keep your departure time without traffic, and it
ignores **all** traffic data, long-term closures included — so a `departure_time` with
`NONE` can route through a road HERE knows is closed. Pass `TRAFFIC` if closures matter more to
you than the Time-Aware rate. An `arrivalTime`, or a `departureTime` other than `any`, supplied
through passthrough (`query` on routing, `body` on matrix) counts as a set time and switches the `NONE` default to
`traffic[mode]=disabled`, since HERE rejects `departureTime=any` combined with `arrivalTime`.

The same rule covers the `/v8/routes` call in the optimized flow; `findsequence2` carries
traffic in its own `mode` string (`traffic:disabled` unless `TRAFFIC`). Both keys are set before
the passthrough merge, so `passthrough.query` can still override them, e.g.
`Passthrough(query={"traffic[mode]": "default"})`.

### Error mapping

| Vendor HTTP | Vendor signal | `ProviderCode` |
|---|---|---|
| 401 | (any) | `auth_failed` |
| 403 | (any) | `auth_failed` |
| 400 | (any) | `invalid_request` |
| 429 | (respects `Retry-After`) | `rate_limited` |
| 5xx | (any) | `provider_unavailable` |

### Retry-After

On HTTP 429, `ConnectorError.cause["retryAfter"]` carries the raw header; parsed seconds in `provider_message`.

### Turn-by-turn instructions

Off by default and **not normalized** — `RoutingResult` has no `steps` attribute. Request
`actions,instructions` and read them from `res.raw`:

```python
from thinwrap.location import HERE_ROUTING_RETURN, Passthrough, RoutingOptions

res = routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(query={
        "return": ",".join(HERE_ROUTING_RETURN + ("actions", "instructions")),
    }),
))
```

Actions land at `routes[].sections[].actions[]`: `action` (the maneuver), `instruction` (localized
text), `offset` (an index into that section's polyline), plus `duration` and `length`.
HERE rejects `instructions` unless `actions` is requested alongside it. The `return` you pass
replaces the connector's own — see [Replacing a default list](#replacing-a-default-list).

For a full navigation payload HERE offers `turnByTurnActions`, which requires `polyline` in the
same `return` list.

---

## Matrix

### Endpoint

`POST https://matrix.router.hereapi.com/v8/matrix?async=true` → poll status → retrieve.

### Input

`transport_mode` (a `HereTransportMode`) overrides the base `travel_mode` mapping. Matrix v8
publishes the same eight values as Routing v8 — `CAR`, `TRUCK`, `PEDESTRIAN`, `BICYCLE`,
`SCOOTER`, `TAXI`, `BUS`, `PRIVATE_BUS` — with no `findsequence2` caveat, since matrix is a
single call. The poll deadline defaults to 60 s; override it with
`passthrough.body["timeoutMs"]`, a finite number of milliseconds greater than 0 — anything else
(a numeric string, NaN, zero, a negative) raises `invalid_request` before any request is sent.

`traffic_mode` follows the [routing rule](#traffic-and-billing) in body form: `NONE` sends
`"departureTime": "any"`, or your ISO time plus `"traffic": {"mode": "disabled"}` (with the
same closure caveat); `TRAFFIC` sends neither. Matrix v8 is one SKU with no traffic surcharge,
but its durations include live traffic by default, so this keeps them consistent with
`NONE`. Override via `passthrough.body` (e.g. `{"traffic": {"mode": "default"}}`).

---

## Geocoding

### Endpoints

- Forward: `GET https://geocode.search.hereapi.com/v1/geocode`
- Reverse: `GET https://revgeocode.search.hereapi.com/v1/revgeocode`
- Autocomplete: `GET https://autosuggest.search.hereapi.com/v1/autosuggest`

### `formatted_address` is `address.label`

Geocode, reverse geocode and place details take `formatted_address` from `address.label`,
the full address. HERE's `title` is the result's display name — for a POI just its name
(`"Empire State Building"` vs. `"Empire State Building, 20 W 34th St, New York, NY 10118,
United States"`) — so it is used only when an item has no label, and on place details it is
what `name` returns.

Place details answers an id Lookup does not know (HTTP 404) or an item without a `position`
with `candidate=None`, not an error.

### Autosuggest requires a search context

HERE documents exactly one of `at`, `in=circle` or `in=bbox` as **mandatory** on
Autosuggest, and rejects a request carrying none of them. Pass `location` (optionally with
`radius`) and the connector supplies it:

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen", location=LatLng(32.08, 34.78)))
```

`location` alone becomes `at=`; `location` + `radius` becomes `in=circle:…;r=…`. If you
need a bounding box instead, supply it yourself via `passthrough.query["in"]` — the
connector accepts that as the context. Calling `autocomplete()` with no context at all
raises `invalid_request` locally rather than relaying a vendor 400.

### Country filter

`country_filter` is ISO 3166-1 alpha-2 throughout this library; HERE expects **alpha-3**,
so the connector translates it. A code with no ISO alpha-3 mapping raises
`invalid_request` and points you at `passthrough.query["in"]` for anything non-standard
that HERE nonetheless accepts.

```python
res = geo.autocomplete(AutocompleteOptions(
    input="Dizen",
    location=LatLng(32.08, 34.78),
    country_filter=["IL", "PS"],
))
# → ...&at=32.08,34.78&in=countryCode:ISR,PSE
```

> **HERE spells both the country filter and the spatial filter `in`.** Its docs require
> the country filter to *accompany* one of `at` / `in=circle` / `in=bbox`, so when you
> combine `country_filter` with `radius` the connector emits `in` **twice** — once for the
> circle and once for the country codes — rather than letting one overwrite the other.
> Nothing is silently dropped, and `radius` still applies.

### Match-highlighting offsets

**Not normalized** — `AutocompletePrediction` has no `matches` attribute. Only 2 of the 5
geocoders return offsets at all (HERE and Google), and they disagree on both the encoding
and which string the offsets index, so this stays vendor-native in `raw`:

```python
res = geo.autocomplete(AutocompleteOptions(input="Dizen", location=LatLng(32.08, 34.78)))
highlights = res.raw["items"][0].get("highlights", {})
# → {"title": [{"start": int, "end": int}], "address": {"label": [...], "city": [...]}}
```

Note the shape difference from Google, which is why neither can be normalized into one
attribute: HERE anchors offsets to `title` and to individual **address components**
(`label`, `city`, `street`, `houseNumber`), and names the bounds `start`/`end`. Google
anchors to its own `mainText`/`secondaryText` and names them `startOffset`/`endOffset`.

---

## Isochrone

### Endpoint

`GET https://isoline.router.hereapi.com/v8/isolines`

### Input

Standard `IsochroneOptions`. `type` (`IsochroneType.TIME` / `IsochroneType.DISTANCE`) maps to HERE `range[type]=time|distance`.

`departure_time` ⇒ `departureTime=`. It must be an ISO-8601 date-time string (sent verbatim) or a
`datetime` (sent as UTC). A string that is not an ISO-8601 date-time raises `invalid_request`
before any request is sent; any other type is a `TypeError` when the options are constructed.
Isochrones have no `traffic_mode`, so a `departure_time` makes the contour traffic-aware for
that time, as HERE documents that a concrete departure time "take[s] traffic flow and other
time-dependent effects into account". HERE's isoline docs do not say what an omitted time means
(on Routing v8 it is the request time, with live traffic). Pass
`Passthrough(query={"departureTime": "any"})` for an isoline without traffic flow.

### Output

HERE types an isoline's `polygons` as a MultiPolygon: disconnected parts (e.g. linked only by a
ferry `connection`) come back as separate entries, each with an `outer` ring and optional `inner`
holes. One entry gives a `Polygon`, several give a `MultiPolygon`; holes are kept, and rings are
closed and wound per RFC 7946 (exterior counter-clockwise, holes clockwise).

---

## Replacing a default list

Passthrough **replaces** a list-valued parameter the connector sets; it does not merge with it.
That is deliberate — a merge would leave you no way to remove a value. To add one, start from
the published default, the same tuple the connector builds its request from:

| Operation | Parameter | Constant |
|---|---|---|
| Routing | `return` query (comma-joined) | `HERE_ROUTING_RETURN` = `("polyline", "summary")` |
| Matrix | `matrixAttributes` body (JSON array) | `HERE_MATRIX_ATTRIBUTES` = `("travelTimes", "distances")` |

```python
from thinwrap.location import HERE_ROUTING_RETURN, Passthrough, RoutingOptions

routing.route(RoutingOptions(
    waypoints=[origin, destination],
    passthrough=Passthrough(query={"return": ",".join(HERE_ROUTING_RETURN + ("turnByTurnActions",))}),
))
```

`matrixAttributes` goes through `passthrough.body`; the body is deep-merged, but a list is a leaf,
so your list replaces the connector's (`Passthrough(body={"matrixAttributes": [...]})`).

> **Omitting a value the normalizer reads.**
>
> - Routing, silently: drop `summary` and every leg's distance and duration and the totals come
>   back `0`, with `duration_without_traffic_seconds` `None`; drop `polyline` and `polyline` comes
>   back `""`. Nothing raises.
> - Matrix, loudly: `travelTimes` fills `duration_seconds` and `distances` fills
>   `distance_meters`. Drop either and the call raises `unknown`, because the result no longer
>   has the requested dimensions.
