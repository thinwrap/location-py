# `thinwrap-location` (Python) — contributor guide

This folder (`.ai/`) is for developers — and the coding agents working alongside
them — who are **changing this library**. It is not usage documentation.

> **Using the package?** See [`../README.md`](../README.md). `.ai/` is contributor-only.

## Map of this folder

- **guidelines.md** (this file) — entry point + the "add a connector" recipe.
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — facade → dispatch → base + invariants.
- [`CONVENTIONS.md`](./CONVENTIONS.md) — layout, naming, error mapping, testing.

## The shape in one sentence

A consumer constructs an operation facade from a provider **config object**
(`Routing(GoogleConfig(api_key=...))`); the config's type selects the provider
and the facade builds a per-provider connector (in `_<provider>.py`) that
subclasses `BaseConnector`, which centralizes the injectable synchronous
transport + transport-error normalization. No global middleware.

## Setup & verify

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=thinwrap.location --cov-fail-under=80
```

Python ≥ 3.10. **Zero runtime dependencies — do not add any** (SigV4/HMAC, when
needed, is hand-rolled on the standard library). `urllib`, `json`, `uuid`, etc.
pytest is a dev-only dependency.

## Add a connector

One operation = one connector class per provider, in the provider's module
(`_google.py`, `_mapbox.py`, …). Touch-points, in order:

1. **Config** — add the `<Provider>Config` dataclass + its `provider_id` property
   to [`config.py`](../src/thinwrap/location/config.py); add the id to
   [`enums.py`](../src/thinwrap/location/enums.py) `ProviderID`.
2. **Connector(s)** — in `_<provider>.py`, add a `<Provider><Operation>Connector`
   subclassing `BaseConnector`, its operation method, and module-level vendor
   helpers (`_<provider>_map_vendor_error`, response navigation).
3. **Dispatch** — add the `isinstance` arm to the relevant facade(s) in
   [`facades.py`](../src/thinwrap/location/facades.py). An unsupported (provider,
   operation) pair falls through to a `ValueError` — the runtime analog of the
   sibling libraries' compile-time provider unions.
4. **Export** — add public types to [`__init__.py`](../src/thinwrap/location/__init__.py) `__all__`.
5. **Tests** — `tests/test_<provider>.py`; inject `helpers.FakeTransport` via the
   facade's `transport=` argument; async-matrix tests pass `sleep=no_sleep`.

### Definition of done (the CI gates)

```bash
pytest --cov=thinwrap.location --cov-fail-under=80   # ≥80% line coverage
python -c "import thinwrap.location"                 # offline import smoke
```

## Invariants you must not break

- **Zero runtime deps / no vendor SDKs.**
- **Stateless wrapper.** No caching, retries, idempotency keys, or telemetry.
- **≥90% baseline-coverage rule.** A field belongs on the base operation input
  only if ≥90% of that operation's providers support it; everything else goes to
  `Passthrough` (input) / `raw` (output).
- **Normalize at the wire layer.** meters / seconds / `LatLng` (lat-first) /
  Google precision-5 polyline. The four polyline utilities are locked.
- **Per-connector locality.** Vendor error mapping + outlier translation live in
  `_<provider>.py` — never in `BaseConnector`. Keys forwarded verbatim.
- **`ProviderCode`**: 6 canonical + 5 location-extended, byte-identical to the
  siblings, surfaced via `ConnectorError`; the raw `Retry-After` rides in
  `cause["retryAfter"]` (no top-level `retry_after_seconds`).
- **OSRM** requires an explicit `base_url`, validated before any HTTP.
- **Python idioms:** synchronous API; a bring-your-own `Transport` protocol
  (default = stdlib `urllib`, never follows redirects); errors raised as
  `ConnectorError`.
