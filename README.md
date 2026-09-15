# RE-Maps

**Google Maps, but for property prices.** Search anywhere on Earth, pan and zoom
an interactive map, and see residential property prices geographically — moving
backwards and forwards through time.

Every figure comes from official open data, or from a model whose inputs are
official open data. Where reliable data does not exist, the application says so.
There is no demo mode, no mock provider, and no fabricated price anywhere in the
codebase.

---

## What actually works right now

This is a running system, not a scaffold. The numbers below are from the loaded
database, measured on 3 September 2026.

| | |
|---|---|
| **Real transactions loaded** | **6,021,794** |
| — England & Wales (HM Land Registry) | 5,428,239 (2021–2026) |
| — France (geo-DVF, 8 metro departments) | 593,555 (2021–2023) |
| Distinct dwellings identified | 5,464,429 |
| Official index observations | 744,709 points, 495 areas, from 1968 |
| Precomputed map aggregates | 275,910 |
| UK postcode centroids | 2,610,351 |
| Country polygons / sub-national regions | 239 / 4,557 |
| **Jurisdictions with real data** | **31** |
| Tests passing | **299** |
| **AVM median error** (held-out real sales) | **7.18%** — 85.2% within 20% |
| **Forecast skill vs random walk** | **+7% at 1 year, +48% at 10 years** |

## What "supported" means here

**A figure is shown only where real money stands behind it.** Countries that
publish only a house price index show nothing at all — an index measures change
("prices moved 4.2%"), never what a home costs, and a percentage on a map where
everything else is a price is worse than an honest blank.

**Transaction-level** — individual recorded sales, so the map can show a
specific dwelling and value it.

| Jurisdiction | Sales | Coordinates | Floor area | Forecast |
|---|---|---|---|---|
| England & Wales | 5,428,239 (2021–2026) | postcode centroid | ⚠️ needs EPC key | ✅ |
| France (8 metros) | 593,555 (2021–2023) | ✅ **cadastral parcel** | ✅ every record | ❌ history too short |

**Area-level prices** — real money, but nothing that can be placed on an
individual dwelling.

| Jurisdiction | Figure | Statistic | Areas | Source |
|---|---|---|---|---|
| United States | $ home values | **median**, owner-estimated | 3,169 counties + national | [Census ACS B25077](https://www.census.gov/data/developers/data-sets/acs-5year.html) |
| Ireland | € from 630,247 recorded sales | **median** | 26 counties + national | [Property Price Register](https://propertypriceregister.ie/) |
| Singapore | S$ from 240,345 resale records | **median** | national | [data.gov.sg HDB resale](https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view) |
| Australia | A$ dwelling stock value | **mean** | 8 states + national | [ABS RES_DWELL_ST](https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/total-value-dwellings) |
| Netherlands | € published average | **mean** | 12 provinces + national | [CBS 83625NED](https://opendata.cbs.nl/statline/#/CBS/nl/dataset/83625NED/table) |
| Sweden | kr published average | **mean** | 21 counties + national | [SCB BO0501](https://www.statistikdatabasen.scb.se/pxweb/en/ssd/START__BO__BO0501/) |
| Denmark | kr published average | **mean** | 5 regions + national | [Statistics Denmark EJEN77](https://www.statbank.dk/EJEN77) |

Three labels appear on markers, and the differences are real:

- **median** — the middle of prices people actually paid.
- **avg** — an arithmetic mean published by a statistics office. House prices
  are right-skewed, so a mean sits well above a median for the same market.
- **est. value** — US only. The ACS asks owners what they think their home
  would sell for; it describes the standing stock, not sales, and owner
  estimates run above market outcomes.

Each source has limits the API states rather than hides: Singapore is HDB
public housing only (about four in five residents, no private condominiums);
Australia is the mean value of the dwelling stock, not transfers; Sweden
excludes tenant-owned flats, understating city housing; Denmark is
single-family houses in arm's-length sales; Ireland excludes non-market
transfers and VAT-exclusive new builds.

**Everywhere else shows nothing.** Twenty-four further countries publish an
official index, which is held in `market_indices` and used where it is valid,
but is never rendered as a price. They are registered as uncovered *with the
reason*, so the API answers:

> No property prices are published for this country. An official house price
> index exists at national level only, but an index measures change only — it
> says how much prices moved, not what a home costs — and no price level or
> individual sale records are published openly here.

Scotland and Northern Ireland are registered the same way: HM Land Registry
Price Paid Data covers England and Wales only.

Eurostat publishes no price-level dataset at all — its catalogue was searched,
and every housing series it carries is an index or a ratio. There is no
pan-European source of actual prices, which is why each country above needed
its own national statistics office.

## Data sources

Full detail, licences and required attributions: **[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)**.

| Source | What for | Licence |
|---|---|---|
| [HM Land Registry Price Paid Data](https://www.gov.uk/guidance/about-the-price-paid-data) | UK transactions | OGL v3.0 |
| [UK House Price Index](https://www.gov.uk/government/statistical-data-sets/uk-house-price-index-data-downloads) | index adjustment, forecasting | OGL v3.0 |
| [Open Postcode Geo](https://www.getthedata.com/open-postcode-geo) | UK postcode centroids | OGL v3.0 + OS OpenData |
| [France geo-DVF](https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/) | French transactions | Licence Ouverte 2.0 |
| [Natural Earth](https://www.naturalearthdata.com/) | country polygons | public domain |
| [Nominatim](https://nominatim.openstreetmap.org) | geocoding | ODbL 1.0 |
| [OpenFreeMap](https://openfreemap.org) | base map tiles | ODbL 1.0 |

Required attribution is displayed in the map attribution bar and in every
property panel:

> Contains HM Land Registry data © Crown copyright and database right 2026.
> This data is licensed under the Open Government Licence v3.0.
> Contains OS data © Crown copyright and database right 2026. Contains Royal
> Mail data © Royal Mail copyright and database right 2026. Contains National
> Statistics data © Crown copyright and database right 2026.
> Contient des données de la DGFiP (DVF), géolocalisées par Etalab, Licence
> Ouverte 2.0.
> Base map © OpenFreeMap, © OpenMapTiles, Data © OpenStreetMap contributors.

**Nothing is scraped.** Every dataset is an official bulk download or a
documented API used within its terms. Rightmove, Zoopla and Zillow are not used.

---

## External credentials — what only you can do

Everything above runs with **no API keys**. One optional integration cannot be
set up without you:

### EPC — Energy Performance of Buildings (England & Wales)

| | |
|---|---|
| **Why it matters** | The only open source of **floor area, habitable rooms and construction age** for UK dwellings. It would enable price-per-m², size-adjusted comparables, and a genuine reduction in valuation error. |
| **Free or paid** | **Free** |
| **What to do** | 1. Go to <https://epc.opendatacommunities.org/><br>2. Register (GOV.UK One Login) and accept the terms<br>3. Copy the email you registered with and the API key you are issued |
| **Environment variables** | `EPC_API_EMAIL` and `EPC_API_KEY` in `.env` |
| **Alternative** | None that is open. OS AddressBase has richer attributes but is commercially licensed. |

Without it the application works fully; UK properties simply have no floor area
or bedroom count. The UI **names the missing fields** rather than hiding them:

> *Not available in the open data for this property: bedrooms, bathrooms,
> rooms, floor area, plot, built, price per m².*

**Nothing is substituted.** France needs no key — geo-DVF includes floor area.

### Optional but recommended for real traffic

`NOMINATIM_EMAIL` should contain a real contact address: the OSM Nominatim usage
policy requires identification, and the public endpoint is not for heavy use.
For production, self-host Nominatim or Photon and set `NOMINATIM_URL`.

---

## API

Interactive docs at <http://127.0.0.1:8000/docs>.

| Endpoint | Purpose |
|---|---|
| `GET /api/search?q=` | global geocoding, with a coverage flag per result |
| `GET /api/coverage/at?lat=&lon=` | what data exists here, and the timeline range |
| `GET /api/coverage` | everything supported |
| `GET /api/sources` | every dataset with licence and attribution |
| `GET /api/map/prices?bbox=&zoom=&year=&segment=` | the map itself |
| `GET /api/property/{id}?year=` | the side panel |
| `GET /api/property/{id}/history` | the chart series |
| `GET /api/property/{id}/comparables?year=` | the evidence |
| `GET /api/property/{id}/valuation?year=` | structured valuation + explainability |
| `GET /api/property/{id}/forecast?year=` | labelled projection + methodology |
| `GET /api/location/market-history?lat=&lon=` | the real official index series |
| `GET /api/location/forecast?lat=&lon=&year=` | market forecast + diagnostics |
| `GET /api/health` | liveness and what is loaded |

Every price is returned as a `Price` object carrying its own interpretation:

```json
{
  "value": 285133,
  "currency": "GBP",
  "price_type": "CURRENT_ESTIMATE",
  "date": "2026-08-29",
  "lower_bound": 244724,
  "upper_bound": 332215,
  "confidence": "MEDIUM",
  "precision_level": "PROPERTY_ESTIMATE",
  "methodology": "Comparable-sales AVM, index-adjusted",
  "sources": [{ "key": "uk_land_registry_ppd", "licence": "Open Government Licence v3.0", "attribution": "…" }],
  "evidence": {
    "comparable_count": 17,
    "comparables_restricted_to_same_type": true,
    "median_index_adjusted_comparable": 279500,
    "median_comparable_distance_m": 310,
    "index_adjustment": "UK HPI local authority series for Milton Keynes (terraced): +8.2% between May 2021 and August 2026",
    "own_prior_sale": { "sold_price": 185000, "sold_date": "2021-05-28" },
    "confidence_reasons": ["17 nearby comparable sales", "comparables are on or beside the same street"]
  }
}
```

---

## Methodology

- **[docs/VALUATION.md](docs/VALUATION.md)** — the AVM, step by step, with
  measured accuracy and interval calibration.
- **[docs/FORECASTING.md](docs/FORECASTING.md)** — the forecaster, its backtest,
  and the version that failed.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — system design and how to add
  a country.

### Two results worth reading before trusting anything

**The AVM's confidence grade is informative.** On 398 held-out real sales:
HIGH → 6.84% median error, MEDIUM → 12.91%, LOW → 20.05%. A user who sees "low
confidence" is genuinely being warned.

**The first forecaster was worse than useless, and the backtest caught it.** With
its parameters taken at face value it scored *negative* skill against a
random-walk baseline at every horizon, over-predicting growth by up to 12% in log
terms at ten years. Calibration against real history cut the trend to 35% and
**switched the momentum term off entirely** — measurement showed it had no
predictive value. The shipped model beats a random walk at every horizon. Both
versions are documented, because a forecast you cannot audit is not a forecast.

---

## Testing

```bash
make test          # 256 tests
make test-unit     # only those needing no database
make check         # lint + typecheck + test
```

Integration tests are marked `@pytest.mark.db` and **skip automatically** when
no database is reachable, so `pytest` is useful on a fresh clone.

The suite that matters most is `tests/test_no_fabrication.py`, which asserts
system-wide guarantees rather than function behaviour:

- ten unsupported locations (Tokyo, New York, Sydney, Mumbai, Berlin, São Paulo,
  Cairo, mid-Atlantic, Edinburgh, Belfast) return **no price at any zoom, in any
  year**, and refuse forecasts;
- no provider is named like a stub, and there is no fallback provider;
- **no module in any price path imports `random`** — prices are deterministic;
- `EXACT_TRANSACTION` precision cannot be claimed outside a transaction branch
  (checked by source inspection);
- every claimed country has real rows behind it, every `forecast_supported`
  claim has an index series, every source key resolves to a registered licence;
- a `PROVIDER_ERROR` message may not contain "no data" or "not available".

Other suites cover address identity (the specification's
`12 High Street` / `Flat 2, 12 High St` cases), currency formatting
(including `₹1.8 Cr` and `₹95 L`), the zoom ladder's monotonicity, bounding-box
validation including antimeridian crossing, forecast interval monotonicity,
comparable scoring, confidence monotonicity, PPD row parsing and quality flags,
and database-level integrity (no duplicate source records, no coordinate without
a stated precision, no area median below its sample-size threshold, no median
computed from ineligible transactions).

---

## Deploying to a hosted database

The full database is **7.7 GB**. Every free Postgres tier is around 0.5–1 GB, so
the two do not reconcile — and the data splits cleanly along that line:

| Layer | Size | What it powers |
|---|---|---|
| **Map** — boundaries, official indices, precomputed area aggregates | ~300–460 MB | the whole world map, all 31 jurisdictions, every area zoom |
| **Sales** — 6M transactions, 5.5M dwellings, 2.6M postcode centroids | ~7.3 GB | individual property markers, postcode search, valuations |

[`backend/cloud_sync.py`](backend/cloud_sync.py) copies whichever you ask for:

```bash
export CLOUD_DATABASE_URL='postgresql://...'   # from your provider
cd backend && .venv/bin/python cloud_sync.py --profile lean --dry-run
```

| Profile | Target size | Trade-off |
|---|---|---|
| `lean` | **296 MB** | fits a 500 MB free tier with headroom. UK area figures stop at postcode district (~z11) and index history starts at 1995. |
| `map` | 464 MB | fits 500 MB with none to spare. UK figures reach postcode sector (~z14); full index history, so forecasts keep their long-run skill. |
| `full` | 7.7 GB | everything. Needs a paid tier. |

The tool needs PostGIS on the target, streams every table with `COPY` (it never
writes a temp dump — the machine this was built on had under 2 GB free), and
reads the target URL only from the environment, so a password never reaches a
command line or a log.

**A map-only deployment rewrites its own coverage registry.** Copied verbatim,
the deployed API would advertise `transaction_level_data: true` for England &
Wales and France and then return nothing — claiming data it does not hold. So
the sync sets those rows to what is actually deployed, and a request for an
individual dwelling answers:

> Individual property prices are not available here. England and Wales is
> covered by official area statistics only — zoom out to see them.

Verified by running a real API against a `lean` target: 27 countries at
continental zoom, real UK district and outcode medians, and individual-dwelling
requests refused with the message above.

## Limitations, stated plainly

0. **29 of the 31 supported jurisdictions have no price level.** They are
   covered by an official index only, which measures change rather than value.
   The map shows growth there and refuses to show a price, a comparable, a
   valuation or a forecast — a forecast projects a price level, and there is
   none to project. Only England & Wales and France carry individual sales.
1. **Transaction coverage is 2021–2026 for the UK and 2021–2023 for France** in
   this deployment, limited by disk. Earlier years load with one command.
   Historical estimates for years outside the window still work, via index
   back-cast from the current valuation — labelled and graded down accordingly.
2. **UK coordinates are postcode centroids, not buildings.** Building-level
   coordinates require OS AddressBase, which is licensed.
3. **No UK floor area or bedroom count** without an EPC key.
4. **Scotland and Northern Ireland have no transaction data** because none is
   published openly.
5. **France covers 8 metro departments** here, and its index is derived from DVF
   itself and is not mix-adjusted.
6. **A ten-year forecast is still wrong by ~16.6 percentage points of growth on
   average.** It beats a random walk, and it is shown at low confidence with
   wide intervals.
7. **No condition or renovation data exists anywhere**, so a refurbished and a
   dilapidated house on the same street are indistinguishable to the model.
8. **The postcodes table carries UPDATE bloat** (~800 MB) from migration `009`.
   Reclaim it with `VACUUM FULL postcodes` when you have ~1.5 GB free.
9. **Nominatim's public endpoint is not for production traffic.** Self-host.

---

## Security

- All secrets come from the environment; `.env` is git-ignored and
  `.env.example` contains no real values.
- No API key ever reaches the browser. The frontend only receives
  `NEXT_PUBLIC_API_BASE` and `NEXT_PUBLIC_MAP_STYLE`, both non-secret.
- Every input is validated by Pydantic; malformed input is a 4xx, never a 500.
- Rate limiting via `slowapi` (`RATE_LIMIT`, default 120/minute).
- CORS restricted to `CORS_ORIGINS`.
- All SQL is parameterised; no string interpolation of user input.
- `statement_timeout` bounds every query.
- The API exposes only property data — never buyer, seller or occupant
  information. Price Paid contains no personal data.

---

## Licence

The code in this repository is provided as-is. **The data is not ours**: each
dataset remains under its own licence, and the attribution requirements in
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) must be preserved in any deployment.
