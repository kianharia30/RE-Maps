# Architecture

```
                    ┌──────────────────────────────────────────┐
   browser  ───────▶│  Next.js 16 · React 19 · MapLibre GL JS  │
                    │  full-screen map, timeline, side panel     │
                    └───────────────────┬──────────────────────┘
                                        │  bbox + zoom + year + segment
                                        ▼
                    ┌──────────────────────────────────────────┐
                    │  FastAPI                                  │
                    │                                           │
                    │  JurisdictionResolver                     │
                    │    coordinates → country (+ sub-region)   │
                    │           ▼                               │
                    │  CoverageRegistry                         │
                    │    what data genuinely exists here?       │
                    │           ▼                               │
                    │  ProviderRegistry                         │
                    │    UKPropertyProvider │ FrancePropertyProvider │
                    │           ▼                               │
                    │  Valuation ── Comparables ── IndexAdjust  │
                    │  Forecast  ── Backcast    ── Confidence   │
                    └───────────────────┬──────────────────────┘
                                        ▼
                    ┌──────────────────────────────────────────┐
                    │  PostgreSQL 18 + PostGIS 3.6              │
                    │  transactions · properties · postcodes    │
                    │  market_indices · area_stats · forecasts  │
                    │  data_sources · provider_coverage         │
                    └──────────────────────────────────────────┘
```

Every map and property request passes through the same three gates, in order:
**resolve the jurisdiction → ask the coverage registry → select a provider.**
There is deliberately no fallback provider, so an unregistered jurisdiction
returns an honest `NO_DATA` rather than an empty result set that looks like a
bug.

---

## The request path, concretely

A user pans to Edinburgh at zoom 17 and the frontend calls
`GET /api/map/prices?bbox=…&zoom=17&year=2026`:

1. `core/zoom.py` maps zoom 17 → `PROPERTY` tier.
2. `core/jurisdiction.py` resolves the viewport centre against
   PostGIS country polygons → `GB`, and then to a **UK constituent country**
   via nearest live postcode centroid → `Scotland` → `region_code = 'GB-SCT'`.
3. `core/coverage.py` looks up `('GB', 'GB-SCT')`. That row exists precisely to
   record an absence: `transaction_level_data = false`,
   `max_precision = 'NONE'`. `is_usable()` returns `False`.
4. The route returns `UNSUPPORTED_LOCATION` **with the registry's own reason**:
   *"HM Land Registry Price Paid Data covers England and Wales only; Scottish
   transactions are registered with Registers of Scotland, which does not
   publish an equivalent open transaction-level dataset."*

The same request over Milton Keynes resolves to `('GB', None)`, selects
`UKPropertyProvider`, and returns individual dwellings.

---

## Key design decisions, and why

### PostgreSQL + PostGIS, with hand-written SQL
Spatial predicates and window functions are the performance-critical part of
this application, and hand-written SQL keeps the query plans legible. An ORM
would obscure exactly the code that needs reviewing. Schema changes are numbered
`.sql` files with a checksum ledger (`backend/migrate.py`) rather than Alembic
autogeneration, which is hard to review against PostGIS types.

### Providers as policy objects over a shared store
All countries' records live in the same normalised tables. A provider owns what
is genuinely jurisdiction-specific: which datasets it may cite, how precisely it
can locate a dwelling, whether an official index exists, whether forecasting is
defensible, and its currency.

Adding a country means: write an ingester, register the sources, register
coverage, subclass `PropertyDataProvider`. **No frontend change.**

### A coverage registry that can record absence
The most important row type in `provider_coverage` is the one that says *there
is nothing here*. Scotland and Northern Ireland are registered explicitly so the
England-and-Wales entry cannot be applied to them by accident, and each carries
the real reason in `notes`, which the API returns verbatim.

`ingest/coverage.py` also **intersects each provider's declaration with measured
reality**: a provider claiming transaction-level coverage with zero rows loaded
comes out as `false`. A country's claimed range is read from the data, not
declared.

### Zoom-tiered aggregation, split by cost
| Zoom | Tier | Source |
|---|---|---|
| ≥ 14.5 | PROPERTY | live, grid-decluttered dwellings |
| 13.2–14.5 | STREET | live `GROUP BY`, 3-year window |
| 12.0–13.2 | POSTCODE | live `GROUP BY`, 3-year window |
| 10.5–12.0 | NEIGHBOURHOOD | precomputed (postcode sector) |
| 9.0–10.5 | CITY | precomputed (outcode) |
| 7.0–9.0 | REGION | precomputed (district) |
| 4.5–7.0 | COUNTRY | precomputed (county) |
| < 4.5 | WORLD | precomputed (country) |

Coarse tiers are precomputed into `area_stats` because a median over millions of
rows per request would not be fast. Street and postcode tiers are computed live
because precomputing ~1.7M postcodes per year would be far more storage than the
queries cost — at those zooms a viewport holds only a few hundred sales.

The ladder has per-country overrides (`LEVEL_OVERRIDES`): France's
administrative hierarchy has one fewer rung, so its CITY tier is served by
communes rather than by a postcode-outcode equivalent that does not exist.

Three properties of the aggregates matter:
- every value is a **real order statistic** over real sales, never a model
  output;
- each tier has a **minimum sample size** (5 sales for a sector, 50 for a
  country) below which no row is published at all;
- only evidence-grade transactions are counted.

### Honest marker positions
Price Paid records share a postcode centroid, so many dwellings occupy one
point. The map:
- thins to one dwelling per screen-space grid cell, keeping the
  best-evidenced one, and reports the true in-view count;
- spreads the remaining co-located markers on a small deterministic ring
  (≤ ~40 m, never leaving the postcode) purely so they can be clicked;
- returns `coordinate_precision` and `position_is_approximate` on every
  property, and the panel states that the position is the postcode centroid.

**The stored geometry is never modified.**

### Errors that cannot be mistaken for missing data
`DataStatus` separates `NO_DATA`, `UNSUPPORTED_LOCATION`,
`INSUFFICIENT_EVIDENCE`, `OUT_OF_RANGE` and `PROVIDER_ERROR`. Honest-absence
cases are `200` responses with a status; genuine failures are `5xx`. The
frontend renders visibly different messages, and only offers *Retry* for real
failures — retrying a place with no data would just produce the same answer.

### Performance
- `bbox && geom` predicates so the GIST index drives every spatial scan;
- BRIN index on `transaction_date` (naturally clustered, tiny);
- server-side aggregation and hard per-tier row caps;
- 260 ms request debounce plus `AbortController` cancellation of superseded
  requests, so a fast pan cannot let a stale response overwrite a newer one;
- index pruning driven by measurement — three overlapping spatial indexes on
  `transactions` tripled write amplification during bulk load for no plan
  improvement (migrations `007`, `008`).

---

## Repository layout

```
backend/
  app/
    config.py            settings; secrets from the environment only
    db.py                psycopg3 pools (async for API, sync for ingest/ML)
    models/              the normalised model, mirrored in TypeScript
      enums.py           PriceType, PrecisionLevel, DataStatus, …
      price.py           the Price value object (§51)
    core/
      jurisdiction.py    coordinates → country → sub-region
      coverage.py        the coverage registry
      currency.py        formatting only — never conversion
      zoom.py            the zoom → tier ladder
      errors.py          typed errors → HTTP + DataStatus
    providers/
      base.py            PropertyDataProvider interface
      registry.py        explicit registration; no fallback provider
      uk/provider.py     HM Land Registry + UK HPI
      fr/provider.py     geo-DVF + derived index
    valuation/
      comparables.py     expanding-radius search + similarity
      index_adjust.py    official index restatement
      avm.py             the model
      backcast.py        index back-cast fallback
      confidence.py      rule-based grading
    forecast/
      model.py           pure, testable projection maths
      service.py         DB-driven fitting + caching
    geocode/             Geocoder interface + Nominatim (throttled, cached)
    api/routes/          search, coverage, map, property, location
  migrations/            001–011, numbered SQL with a checksum ledger
  ingest/                one module per dataset, all resumable
  ml/
    eval_avm.py          held-out accuracy + interval calibration
    backtest_forecast.py rolling-origin backtest + parameter sweep
  tests/                 256 tests; `db`-marked ones skip without a database

frontend/src/
  app/page.tsx           state, URL sync, request orchestration
  components/
    MapCanvas.tsx        MapLibre lifecycle, markers, heatmap
    Timeline.tsx         past / present / future, ranges from coverage
    PropertyPanel.tsx    the side panel / bottom sheet
    PriceChart.tsx       observed vs estimate vs forecast, visually distinct
    PriceBadge.tsx       the single source of price-type labelling
    SearchBar.tsx        debounced autocomplete with coverage flags
  lib/api.ts             typed client with per-key request cancellation
  types/api.ts           mirror of backend/app/models
```

---

## Adding a country

1. **Verify the source.** Licence permits your use, bulk download or documented
   API, transaction-level or index-level, real coordinates or not.
2. **Register it** in `ingest/sources.py` with owner, licence, attribution and
   permitted use. Nothing can be displayed without this.
3. **Write the ingester** in `ingest/`, following `uk_ppd.py` (postcode-geocoded)
   or `fr_dvf.py` (coordinates supplied). Stream via `COPY` into an `UNLOGGED`
   staging table, transform in SQL, dedupe on
   `(source_key, source_record_id)`, record the run in `ingestion_runs`.
4. **Subclass `PropertyDataProvider`**, declaring `coordinate_precision`,
   `supports_forecast`, `comparable_policy` and `source_keys`.
5. **Register the provider** in `providers/registry.py` and add a
   `register_coverage_row()`.
6. **Add tier overrides** in `core/zoom.py` if the administrative hierarchy
   differs.
7. **Recompute** with `make stats`, then run `make test` — the no-fabrication
   suite will check that your coverage claims are backed by real rows.

The frontend needs no changes. It reads `PriceType`, `PrecisionLevel`,
`Confidence` and currency from the response.
