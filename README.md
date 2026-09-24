# RE-Maps

A world map of property prices, built on a single rule: **never show a number
that isn't backed by real data.**

<!-- Replace YOUR-USERNAME below with your GitHub username once the repo exists. -->
[![CI](https://github.com/YOUR-USERNAME/RE-Maps/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR-USERNAME/RE-Maps/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![TypeScript](https://img.shields.io/badge/typescript-5-blue)
![Tests](https://img.shields.io/badge/tests-365%20passing-brightgreen)
![Licence](https://img.shields.io/badge/licence-MIT-green)

Zoom from the whole world down to a single house. Move a timeline from 2010 to
2036. Every figure is either a real recorded price, an official statistic, or
nothing at all — with the reason given.

<!-- ===========================================================================
  ADD A SCREENSHOT HERE — it is the first thing a reviewer looks at.

    1. make dev
    2. open http://localhost:3000, go to London, zoom to about 14
    3. save the shot as docs/images/screenshot.png
    4. delete this comment block and uncomment the line below

  A second shot of the property panel open on a single house works well too.
============================================================================ -->
<!-- ![RE-Maps showing property prices across London](docs/images/screenshot.png) -->

---

## Scope, stated plainly

**Street-level detail exists for the United Kingdom only.** That is the honest
position and it is deliberate: the UK publishes every residential sale with an
address, and almost nowhere else does.

| | What you get | Why |
|---|---|---|
| **England & Wales** | **Individual houses.** 5.4M recorded sales, a price for a specific address, a valuation for a property that hasn't sold | HM Land Registry publishes every sale with an address |
| France | Sales at cadastral-parcel precision, 8 metro departments | geo-DVF publishes parcels but not addresses |
| 7 other countries | **One figure per region** — county, province or state | Their statistics offices publish area aggregates only |
| Everywhere else | **Nothing**, with the reason | No open price data exists |

So: the UK is the product. The other eight countries demonstrate that the
ingestion, licensing and precision machinery generalises — they are not a claim
that the map works everywhere.

<details>
<summary>The nine jurisdictions with real figures</summary>

| Jurisdiction | Figure | Statistic | Areas |
|---|---|---|---|
| England & Wales | £ from 5,428,239 recorded sales | median | 10,715 areas + individual dwellings |
| United States | $ home values | median, **owner-estimated** | 3,169 counties |
| France | € from 593,555 recorded sales | median | 1,365 communes |
| Ireland | € from 630,247 recorded sales | median | 26 counties |
| Sweden | kr published average | mean | 21 counties |
| Netherlands | € published average | mean | 12 provinces |
| Australia | A$ dwelling stock value | mean | 8 states |
| Denmark | kr published average | mean | 5 regions |
| Singapore | S$ from 240,345 resale records | median | national |

Markers are labelled `· median`, `· avg` or `· est. value`, because the three
are not interchangeable. House prices are right-skewed, so a published mean
sits well above a median for the same market; and the US figure is what owners
*think* their home is worth, not what anyone paid.

Twenty-four further countries publish a house price **index** — a series based
at 100 in a reference year. An index says prices moved 4.2%; it never says what
a home costs. Deriving a price from one would need a base-year price nobody
publishes, so those countries show nothing and the API explains why.

</details>

---

## The interesting problem

A property map is trivial to build if you are willing to guess. Interpolate
between two postcodes, apply a national index to a regional average, fall back
to "typical for the area" — every one of those produces a plausible number with
nothing behind it, and a user cannot tell the difference.

This codebase is organised around refusing to do that. The consequences run
through every layer:

**The schema makes fabrication hard.** `area_stats.median_price` is nullable,
guarded by a constraint that a row must carry either a real median or an index
value, and a second constraint that anything claiming `TRANSACTIONS` basis has
both a median and a positive sale count. A `basis` column records which of four
kinds of evidence stands behind each figure, and `price_statistic` records
whether it is a mean or a median.

**Precision is derived, never asserted.** A figure is labelled by the area
level it was actually computed at, never the zoom level that requested it.
Serving an Irish county median to a street-zoom viewport labels it
`CITY_REGIONAL`, not `NEIGHBOURHOOD` — the map reports what it has, not what
you asked for.

**Absence is a first-class value.** Scotland and Northern Ireland have registry
rows recording that HM Land Registry does not cover them, so the England-and-
Wales dataset can never be applied to them by accident. Searching Edinburgh
moves the map and tells you exactly that.

**The tests encode the rule, not the implementation.** `test_no_fabrication.py`
asserts system-wide guarantees: that no unsupported location returns a price at
any zoom, that no code path in the price chain calls `random`, that every
country claimed as covered has data behind it, and that a viewport spanning a
border never serves one country's figures for another.

That last one caught a real bug. An early fix for continental zoom made a
viewport over Scotland return England and Wales's median — precisely the
substitution the registry rows exist to prevent. Ten tests now guard it.

---

## Measured results

Not estimates — these come from backtests stored in `model_evaluations` and are
reproducible with `make evaluate`.

**Automated valuation**, held-out real sales the model never saw, with the
target sale excluded from its own comparable set:

| | |
|---|---|
| Median absolute error | **7.33%** |
| Within 10% / 20% of the true price | 64.3% / 84.7% |
| Stated 80% interval, actual coverage | 88.3% (conservative, as intended) |

**Forecasting**, rolling-origin backtest over 376 real index series, 7,577
origins, each fitted only on data available at that origin:

| Horizon | Improvement vs random walk | vs linear drift |
|---|---|---|
| 1 year | +7% | +21% |
| 5 years | +24% | +58% |
| 10 years | +48% | +75% |

The forecast intervals served by the API are built from the error distribution
measured here, so a ten-year projection is visibly, honestly uncertain.

---

## Running it

Needs PostgreSQL 16+ with PostGIS, Python 3.12+ and Node 22+.

```bash
git clone https://github.com/YOUR-USERNAME/RE-Maps.git && cd RE-Maps
cp .env.example .env          # defaults work for a local Postgres
make setup                    # virtualenv, dependencies, database, migrations
make data-download-ppd PPD_YEARS="2024 2025"   # ~80 MB, two years of UK sales
make ingest-uk
make dev                      # API on :8000, web on :3000
```

`make data-download` fetches every dataset (~3.5 GB) and `make ingest` loads all
nine jurisdictions; the two-year UK slice above is enough to see the whole thing
work. `make help` lists every target.

No API key is needed. Two optional ones unlock extra data and the app states
clearly what is missing without them — UK floor areas (EPC) and US counties
(Census). Both are free; see `.env.example`.

---

## Architecture

```
backend/
  app/
    api/routes/     FastAPI endpoints — map, property, search, coverage, sources
    core/           zoom tiers, jurisdiction resolution, coverage registry, currency
    providers/      one module per jurisdiction behind a shared interface
    valuation/      comparable-sales AVM with explicit evidence tracking
    forecast/       damped-drift index model, intervals from measured backtest error
    geocode/        Nominatim client — throttled, cached, policy-compliant
    models/         Pydantic schemas shared by every route
  ingest/           one module per source; each registers its own licence
  migrations/       18 numbered SQL migrations, each explaining WHY
  tests/            365 tests
frontend/src/
  components/       MapCanvas (MapLibre), Timeline, PropertyPanel, SearchBar
  lib/              typed API client
```

**Providers behind one interface.** Adding a jurisdiction means writing an
ingester and a coverage entry, not touching the map. The map queries
`area_stats`, which is provider-independent.

**The zoom ladder degrades gracefully.** Each tier asks for a granularity, then
walks to coarser levels until it finds real rows, reporting the level it landed
on. A viewport over Germany asks for a postcode sector and gets nothing;
a viewport over Dublin asks for a sector and gets the county, labelled as one.

**Geometry, not points.** Country and region figures are selected by polygon
intersection and clipped into the visible area. An earlier version stored one
point per area, which made large regions invisible unless the viewport happened
to contain that exact coordinate — California's figure was unreachable from a
view of Los Angeles.

Further detail: [ARCHITECTURE](docs/ARCHITECTURE.md) ·
[VALUATION](docs/VALUATION.md) · [FORECASTING](docs/FORECASTING.md) ·
[DATA_SOURCES](docs/DATA_SOURCES.md)

---

## Testing

```bash
make test        # 365 tests
make lint        # ruff + tsc
```

Tests that need ingested data are marked `db` and skip automatically when the
database is empty, so CI runs the 207 pure-logic tests without downloading
gigabytes. The suite is deliberately integration-heavy: a guarantee like "an
unsupported location never returns a price" is only meaningful against a real
database with real constraints.

---

## Known limitations

1. **Individual properties are UK-only.** Everywhere else is area-level, for
   the reason in the scope table above.
2. **Forecasts are UK-only.** The model needs 96 monthly observations; the UK
   has monthly data back to 1968, while Ireland has 17 *annual* points and
   Australia 16. Interpolating months would invent observations, and the
   uncertainty bands are calibrated on UK backtests. Selecting a future year
   elsewhere says so rather than showing a guess.
3. **UK floor areas need an EPC key.** Without it, price-per-square-metre is
   unavailable and the UI shows it as such. Nothing is substituted.
4. **France covers 8 metro departments** and stops at 2023.
5. **US figures are owner estimates**, not transactions — labelled throughout.
6. **Scotland and Northern Ireland have no data at all**, by publisher, not by
   omission here.

---

## Licence

Code is MIT. The datasets are not relicensed and are not redistributed here —
each is downloaded from its publisher at build time, and every licence and
required attribution is recorded in `backend/ingest/sources.py`, stored in the
database, and served at `GET /api/sources`. See [LICENSE](LICENSE).

Contains HM Land Registry data © Crown copyright and database right, licensed
under the Open Government Licence v3.0.
