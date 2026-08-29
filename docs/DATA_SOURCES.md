# Data sources

Every dataset RE-Maps uses is recorded in the `data_sources` table
(`backend/ingest/sources.py`) with its owner, licence, required attribution and
permitted use. The API refuses to emit a price without a resolvable source
reference, so this file and that table are the same information.

All availability, licensing and schema details below were verified against the
live sources on **29 August 2026**.

---

## 1. HM Land Registry Price Paid Data — *transactions, England & Wales*

| | |
|---|---|
| **URL** | <https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads> |
| **Bulk download** | `http://prod.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com/pp-<year>.csv` |
| **Documentation** | <https://www.gov.uk/guidance/about-the-price-paid-data> |
| **Owner** | HM Land Registry |
| **Licence** | Open Government Licence v3.0 |
| **Attribution (required)** | *Contains HM Land Registry data © Crown copyright and database right 2026. This data is licensed under the Open Government Licence v3.0.* |
| **Update frequency** | Monthly |
| **Geographic coverage** | England and Wales only |
| **Historical coverage** | 1995 to present (~30M rows; the complete file is 5.5 GB) |

### What it contains
Transaction unique identifier, price, date of transfer, postcode, property type
(D/S/T/F/O), old-or-new, duration (freehold/leasehold), PAON, SAON, street,
locality, town, district, county, PPD category type, record status.

### Limitations that shape the implementation
- **No coordinates.** Records carry a postcode, not a position. See §3.
- **England and Wales only.** Scotland (Registers of Scotland) and Northern
  Ireland (Land & Property Services) publish no equivalent open
  transaction-level dataset, so RE-Maps registers those regions as having no
  data (migration `009`, and `ABSENCE_ROWS` in `ingest/coverage.py`).
- **Category B rows may not be market value.** HM Land Registry flags
  repossessions, buy-to-let where identifiable, and transfers not at full market
  value as "additional price paid". They are stored and displayed as the real
  sales they are, but excluded from every derived figure — comparables, AVM
  inputs and area medians (migration `006`).
- **The two most recent months are incomplete**, because registration lags
  completion by roughly two weeks to two months.
- **Excluded transaction types**: sales not lodged with HM Land Registry, sales
  not for value, right-to-buy sales at discount, transfers under court order,
  compulsory purchases, and leases of seven years or less.
- **Property type "O" (Other)** is largely non-residential; it is flagged
  `is_residential = false` and excluded from statistics.

---

## 2. UK House Price Index — *official price index*

| | |
|---|---|
| **URL** | <https://www.gov.uk/government/statistical-data-sets/uk-house-price-index-data-downloads> |
| **Bulk download** | `http://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data/UK-HPI-full-file-<YYYY-MM>.csv` |
| **Owner** | HM Land Registry / Office for National Statistics |
| **Licence** | Open Government Licence v3.0 |
| **Attribution (required)** | *Contains HM Land Registry data © Crown copyright and database right 2026, and Office for National Statistics data © Crown copyright. Licensed under the Open Government Licence v3.0.* |
| **Update frequency** | Monthly |
| **Coverage** | United Kingdom, by country, region and local authority; monthly from 1968 (local-authority series from 1995) |

This is the backbone of all time travel in the product. It provides a
mix-adjusted hedonic index per area **and per property type**, which is used to:

1. restate each comparable sale at the valuation date,
2. back-cast a current valuation to a past year,
3. supply the real historical series the forecaster is fitted to.

**Limitation:** recent months are revised in later releases. `index_adjust.py`
accepts a month within six months of the requested date and reports when it had
to fall back, so a stale edge cannot silently distort an adjustment.

### District-name reconciliation
Price Paid records the district that existed *at the time of sale*; the HPI
publishes series for the authorities that exist *now*. English local-government
reorganisation therefore leaves districts that can never match by name.
`ingest/uk_hpi.py` links them in three passes — exact normalised match, then a
documented successor mapping (`DISTRICT_SUCCESSORS`), then trigram similarity
above 0.55. All 342 districts in the loaded data resolve to a real local series;
none falls back to the national index.

---

## 3. Open Postcode Geo — *UK postcode centroids*

| | |
|---|---|
| **URL** | <https://www.getthedata.com/open-postcode-geo> |
| **Owner** | GetTheData, derived from the ONS Postcode Directory / OS Code-Point Open |
| **Licence** | Open Government Licence v3.0 **and** the Ordnance Survey OpenData Licence |
| **Attribution (required)** | *Contains OS data © Crown copyright and database right 2026. Contains Royal Mail data © Royal Mail copyright and database right 2026. Contains National Statistics data © Crown copyright and database right 2026.* |
| **Vintage** | October 2023 |
| **Rows loaded** | 2,610,351 (1.75M live) |

This is what puts Price Paid transactions on the map. **A postcode centroid is
the centre of a postcode unit, not the position of a building** — recorded
throughout as `coordinate_precision = 'POSTCODE'` and stated in the UI.

Two further points recorded honestly:
- **Northern Ireland (BT) postcodes are absent** from this dataset for
  licensing reasons, and commercial use of NI data requires a separate licence
  from Land & Property Services. RE-Maps holds no NI transaction data, so no
  displayed price depends on them; NI is resolved geographically instead
  (`_NI_ENVELOPE` in `core/jurisdiction.py`).
- **Positional quality 7–9** indicates an imputed rather than surveyed
  position; those postcodes are demoted to `NEIGHBOURHOOD` precision.

### Why building-level coordinates are not used
Rooftop coordinates for England & Wales require **OS AddressBase**, which is
licensed and not open data. Rather than imply a precision we do not have, the
API returns `coordinate_precision` and `position_is_approximate` on every
property, the map spreads co-located markers on a small deterministic ring
purely so they can be clicked, and the property panel says the position is the
postcode centroid.

---

## 4. France geo-DVF — *transactions with real coordinates*

| | |
|---|---|
| **URL** | <https://files.data.gouv.fr/geo-dvf/latest/csv/> |
| **Dataset page** | <https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/> |
| **Owner** | Direction générale des Finances publiques, geolocated by Etalab |
| **Licence** | Licence Ouverte / Open Licence 2.0 (Etalab) |
| **Attribution (required)** | *Contient des données de la Direction générale des Finances publiques (DVF), géolocalisées par Etalab, sous Licence Ouverte 2.0.* |
| **Update frequency** | Twice yearly |
| **Coverage** | France excluding Alsace, Moselle and Mayotte (separate land registry systems) |
| **Historical coverage** | 2021 onwards in the current release |

This provider exists partly to prove the architecture is genuinely
jurisdiction-agnostic, because France's data profile is materially different
from the UK's:

| | UK (Land Registry) | France (geo-DVF) |
|---|---|---|
| Coordinates | postcode centroid | **cadastral parcel** |
| Floor area | only via EPC (needs a key) | **in every record** |
| Official index | UK HPI, monthly, per LA | none published as open bulk |
| Forecasting | yes | **no** — history too short |

### The mutation problem, and how it is handled
A DVF *mutation* is a transfer, not a dwelling: one declared `valeur_fonciere`
can cover several lots. Attributing the full value to any one dwelling would be
wrong, so `ingest/fr_dvf.py` keeps only mutations containing **exactly one
residential building and no commercial premises**. Everything else is counted
and reported as rejected. Of 4.67M rows in the 2021 file, 214,760 qualified for
the loaded departments.

Records are also rejected when the declared value is under €5,000, floor area
under 8 m², or price per m² outside €150–€60,000 — these are data errors rather
than unusual sales, and every rejection is counted in `ingestion_runs`.

---

## 5. RE-Maps derived French price index — *a derived work, clearly labelled*

Because France has no open bulk price index, `ingest/fr_index.py` derives one:
**median price per square metre, by department and property type, per quarter**,
computed over the same real transactions the map shows.

It is registered as a separate source (`fr_dvf_derived`) and is **not presented
as an official index**. Its weakness is stated in the code, in the source
registry and in France's coverage notes: it is **not mix-adjusted**, so it moves
when the composition of sales changes. A minimum of 30 sales per
department-quarter is required before a point is published, and forecasting for
France stays disabled.

---

## 6. Natural Earth Admin 0 — *country polygons*

| | |
|---|---|
| **URL** | <https://www.naturalearthdata.com/downloads/10m-cultural-vectors/> |
| **Licence** | Public domain |
| **Use** | Resolving coordinates to an ISO country, which selects the provider |

---

## 7. Nominatim (OpenStreetMap) — *geocoding*

| | |
|---|---|
| **URL** | <https://nominatim.openstreetmap.org> |
| **Usage policy** | <https://operations.osmfoundation.org/policies/nominatim/> |
| **Licence** | ODbL 1.0 |
| **Attribution (required)** | *Geocoding © OpenStreetMap contributors, ODbL 1.0.* |

The public endpoint's policy forbids heavy or bulk use and requires an
identifying User-Agent. `app/geocode/nominatim.py` enforces all of it:

- an identifying `User-Agent` on every request, plus `email=` when
  `NOMINATIM_EMAIL` is set,
- a process-wide lock enforcing a minimum interval (default 1 request/second),
- every response cached in Postgres for `GEOCODE_CACHE_DAYS` (default 30),
- the frontend debounces keystrokes by 320 ms and cancels superseded requests.

**For real traffic, run your own instance** (Nominatim or Photon) and point
`NOMINATIM_URL` at it. The `Geocoder` interface in `app/geocode/base.py` exists
so the provider can be swapped without touching the search route.

---

## 8. OpenFreeMap — *base map tiles*

| | |
|---|---|
| **URL** | <https://openfreemap.org> |
| **Licence** | ODbL 1.0 |
| **Attribution (required)** | *Base map © OpenFreeMap, © OpenMapTiles, Data © OpenStreetMap contributors.* |
| **Cost** | Free, no API key, no stated usage limits |

Chosen over Mapbox/MapTiler specifically because it needs no account or key, so
a fresh clone renders a map immediately.

---

## 9. EPC (Energy Performance of Buildings) — *needs a free key; not enabled*

| | |
|---|---|
| **URL** | <https://epc.opendatacommunities.org/> |
| **Owner** | Ministry of Housing, Communities and Local Government |
| **Licence** | Open Government Licence v3.0 |
| **Cost** | Free, but requires a registered account (GOV.UK One Login) |

This is the only source that would materially improve UK valuations and cannot
be obtained without your action. See **[External credentials](../README.md#external-credentials-what-only-you-can-do)**
in the README for exactly what to do.

Without it, UK properties have no floor area, bedroom or habitable-room data.
The UI names those fields as unavailable rather than hiding them, and **nothing
is substituted**.

---

## Licensing summary

| Source | Commercial use | Attribution required | Notes |
|---|---|---|---|
| Land Registry PPD | ✅ | ✅ | England & Wales only |
| UK HPI | ✅ | ✅ | Recent months revised |
| Open Postcode Geo | ✅ | ✅ | NI data needs a separate LPS licence |
| geo-DVF | ✅ | ✅ | Excludes Alsace, Moselle, Mayotte |
| Natural Earth | ✅ | — | Public domain |
| Nominatim | ⚠️ | ✅ | Public endpoint forbids bulk use — self-host |
| OpenFreeMap | ✅ | ✅ | No key required |
| EPC | ✅ | ✅ | Requires a free account |

**No source is scraped.** Every dataset is an official bulk download or a
documented API used within its stated terms. Rightmove, Zoopla, Zillow and
similar platforms are not used, because doing so would violate their terms.
