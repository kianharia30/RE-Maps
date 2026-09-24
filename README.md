# RE-Maps

An interactive map of UK property prices, built from HM Land Registry's public
sales records. Zoom from the whole country down to a single house, move a
timeline from 2021 to 2036, and get a valuation for a property that hasn't sold
recently.

The rule the whole thing is built around: if there isn't real data behind a
number, the app shows nothing and says why. No interpolation, no "typical for
the area", no filling gaps with a national average.

**Stack:** Python 3.12 / FastAPI / PostgreSQL + PostGIS on the backend,
TypeScript / Next.js / MapLibre GL on the frontend.

---

## Getting it running

### 1. Install the prerequisites

You need PostgreSQL 16 or newer **with the PostGIS extension**, Python 3.12+,
and Node 22+.

On macOS with Homebrew:

```bash
brew install postgresql@18 postgis python@3.12 node
brew services start postgresql@18
```

On Ubuntu:

```bash
sudo apt install postgresql-16 postgresql-16-postgis-3 python3.12-venv nodejs npm
```

### 2. Set the project up

```bash
git clone https://github.com/kianharia30/RE-Maps.git
cd RE-Maps
cp .env.example .env        # the defaults work for a local Postgres
make setup                  # Python venv + pip install + npm install
make db-create              # creates the `remaps` database, enables PostGIS
make migrate                # applies the 18 SQL migrations
```

If `make db-create` complains that `createdb` isn't found, Postgres isn't on
your PATH. On Homebrew that's
`export PATH="/opt/homebrew/opt/postgresql@18/bin:$PATH"`.

### 3. Load some data

The full dataset is 3.5 GB. To just see it working, two years of UK sales is
plenty and takes a few minutes:

```bash
make data-download-geo                          # country/region boundaries, ~54 MB
make data-download-postcodes                    # UK postcode coordinates, ~66 MB
make data-download-hpi                          # UK House Price Index, ~35 MB
make data-download-ppd PPD_YEARS="2024 2025"    # ~80 MB of sales records
make ingest-reference
make ingest-uk
make stats                                      # precomputes the map aggregates
```

That gives you roughly 1.9M sales. For everything (2021–2026, plus France and
the other countries) run `make data-download && make ingest` and expect it to
take a while.

### 4. Start it

```bash
make dev
```

Then open **http://localhost:3000**. The API runs on port 8000; its
auto-generated docs are at http://localhost:8000/docs.

You should land on Milton Keynes. Zoom out and price markers aggregate into
postcode areas and districts; zoom past 14.5 and individual houses appear.
Click one for its sale history and a current valuation. Drag the timeline at
the top to move through the years.

### If something looks wrong

| Symptom | Cause |
|---|---|
| Map loads but no price markers | No data ingested yet — run step 3 |
| "No recorded residential sales for..." | That year isn't loaded; check `PPD_YEARS` |
| Everything blank, console shows failed requests | API isn't running, or `NEXT_PUBLIC_API_BASE` is wrong |
| `port 3000 already in use` | `lsof -ti:3000 \| xargs kill` |
| Individual houses never appear | You're below zoom 14.5, or you're outside England and Wales |

---

## What the app does

**The map.** As you pan or zoom, the frontend sends the viewport bounds and
zoom level to `/api/map/prices`. The backend turns the zoom into one of eight
tiers and queries the matching granularity:

| Zoom | Tier | What you see |
|---|---|---|
| 14.5+ | Property | individual houses |
| 13.2 | Street | median per street |
| 12.0 | Postcode | median per postcode unit |
| 10.5 | Neighbourhood | postcode sector |
| 9.0 | City | postcode outcode |
| 7.0 | Region | local authority district |
| 4.5 | Country | county |
| 0 | World | whole countries |

Sector level and coarser are precomputed into an `area_stats` table because
recomputing medians over 6M rows per pan would be far too slow. Street and
postcode level are computed live from the transactions themselves, pooling
several years, since a single street rarely sees enough sales in one year to
produce a meaningful median.

**Valuation.** Click a house that last sold in 2019 and you get an estimate for
today. It works by comparable sales: find recent sales near that property,
score each one on distance, how recent it is, whether it's the same property
type and a similar size, then take a weighted median. Each comparable's price
is adjusted to today using the official House Price Index for its local
authority. The property's own past sale is used as evidence too, indexed
forward. Every estimate carries a confidence grade and an interval, and the
panel lists which comparables produced it.

**Forecasting.** Move the timeline past the current year and prices become
projections. The model is a damped drift on the index series: it extrapolates
recent momentum but decays it towards long-run trend, shrinking a local area's
signal towards the national one when local history is thin. The uncertainty
bands come from a rolling-origin backtest, not from an assumption.

**Search.** Type a postcode, street or place name. UK postcodes resolve against
the local gazetteer; anything else goes to OpenStreetMap's Nominatim, throttled
to one request a second and cached, as their usage policy requires.

---

## Coverage, honestly

**Individual properties are England and Wales only.** HM Land Registry
publishes every residential sale with an address, which almost no other country
does. That's what makes the detailed part of this possible, and it's why it
stops at the Welsh and Scottish borders.

Scotland and Northern Ireland are registered in the database as explicitly
having no data, so the England-and-Wales dataset can never be applied to them
by accident. Search Edinburgh and the map moves there and tells you exactly
that.

Eight other countries are loaded at area level only — one figure per county,
province or state, from their national statistics offices:

| | Figure | From |
|---|---|---|
| France | 593,555 sales, cadastral parcel precision | geo-DVF |
| United States | 3,169 counties | Census ACS (owner-estimated values) |
| Ireland | 630,247 sales, 26 counties | Property Price Register |
| Sweden, Netherlands, Denmark | published averages by region | national statistics offices |
| Australia | 8 states | ABS |
| Singapore | national | HDB resale records |

Markers say `· median`, `· avg` or `· est. value`, because those aren't the
same thing. A published mean sits well above a median on the same market, and
the US figure is what owners think their home is worth rather than what anyone
paid.

Countries that publish only a house price index show nothing at all. An index
says prices moved 4.2%; it never says what a house costs, and there's no
honest way to turn one into the other.

---

## How it's put together

```
backend/
  app/
    api/routes/     map, property, search, coverage, sources endpoints
    core/           zoom tiers, jurisdiction lookup, coverage registry
    providers/      one module per country behind a shared interface
    valuation/      comparable search, scoring, the AVM, confidence grading
    forecast/       the drift model and its backtested intervals
    geocode/        Nominatim client (throttled, cached)
  ingest/           one module per data source, each registering its licence
  migrations/       18 numbered SQL files
  tests/            365 tests
frontend/src/
  components/       MapCanvas, Timeline, PropertyPanel, SearchBar
  lib/              typed API client
```

Some decisions worth knowing about:

- **Postgres does the spatial work.** Markers are selected by PostGIS bounding
  box and polygon intersection, with GiST indexes. Doing it in Python would
  mean pulling millions of rows into the app to throw most of them away.
- **Area figures are clipped into the viewport.** A country or state is drawn
  at the centre of its *visible* part, not at a stored point. An earlier
  version used one fixed point per area, which made California's figure
  unreachable from a view of Los Angeles.
- **Precision is reported from the data, not the zoom.** If you're zoomed to a
  street in Ireland, you get the county median labelled as a county median. The
  map reports what it has, not what you asked for.
- **Every source registers its own licence** in `backend/ingest/sources.py`,
  stored in the database and served at `/api/sources`, so the attributions the
  licences require are always available to the UI.

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/VALUATION.md](docs/VALUATION.md),
[docs/FORECASTING.md](docs/FORECASTING.md) and
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

---

## How accurate is it

Both numbers come from backtests stored in the `model_evaluations` table and
are reproducible with `make evaluate`.

**Valuation**, measured on held-out real sales with the target sale excluded
from its own comparable set:

- median error **7.3%**
- 64% within 10% of the true price, 85% within 20%
- the stated 80% interval actually contains the true price 88% of the time

**Forecasting**, rolling-origin backtest over 376 index series and 7,577
origins, each fitted only on data available at that origin: 7% better than a
random walk at one year, 24% at five, 48% at ten.

---

## Tests

```bash
make test     # 365 tests
make lint     # ruff + tsc
```

The interesting ones are in `backend/tests/test_no_fabrication.py`. They assert
things about the system rather than about functions: that an unsupported
location never returns a price at any zoom level, that nothing in the pricing
path calls `random`, that every country claimed as covered has data behind it,
and that a viewport straddling a border never serves one country's figures for
another.

Tests needing ingested data are marked `db` and skip automatically when the
database is empty, so CI runs the 207 pure-logic tests without downloading
gigabytes.

---

## Optional API keys

Neither is needed to run the app, and it states clearly what's missing without
them.

- **EPC** (free, UK): floor areas and room counts, which enable
  price-per-square-metre. Without it those fields show as unavailable.
- **US Census** (free): county home values. Without it the US isn't loaded.

Both go in `.env`; see `.env.example`.

---

## Limitations

1. Individual properties and valuations are England and Wales only.
2. Forecasts are UK only. The model needs 96 monthly observations; Ireland has
   17 annual ones. Selecting a future year elsewhere says so rather than
   guessing.
3. France covers 8 metro departments and stops at 2023.
4. UK floor areas need the EPC key.
5. Postcode centroids, not exact addresses, so UK markers sit at the centre of
   their postcode rather than on the building.

---

## Licence

Code is MIT — see [LICENSE](LICENSE). The datasets aren't relicensed and aren't
redistributed here; each is downloaded from its publisher at build time and
keeps its own terms.

Contains HM Land Registry data © Crown copyright and database right, licensed
under the Open Government Licence v3.0.
