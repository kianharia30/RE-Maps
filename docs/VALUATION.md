# Valuation methodology

This document describes how RE-Maps produces the three modelled price types —
`CURRENT_ESTIMATE`, `HISTORICAL_ESTIMATE` and the property-level component of
`FORECAST` — and reports the **measured** accuracy of each.

Nothing here is asserted without a number behind it. Everything below is
reproducible with `make evaluate`.

---

## 1. Why comparable sales and not gradient boosting

The specification warns against adding ML complexity to be able to claim the
project uses AI. That warning is well aimed at this problem, because of what the
open data actually contains.

For every dwelling in England & Wales, HM Land Registry Price Paid Data gives us
exactly four usable attributes: **location, property type, tenure, and
new-build-at-sale**. Floor area, bedrooms and condition require the EPC dataset,
which needs a key most deployments will not have.

A learned model over four weak features cannot beat a well-constructed local
comparable set, and it would hide the reasoning the product is obliged to
display. So the primary model is a **comparable-sales AVM with official index
adjustment**, which is also what a surveyor does.

A hedonic gradient-boosting model is a legitimate future addition — the
`ml/` directory is structured for it — but it should only ship if measurement
shows it beats this baseline on held-out sales. It currently does not exist
because it has not been shown to.

---

## 2. The model

`backend/app/valuation/avm.py`

### Step 1 — find real comparable sales

`comparables.py` runs an **expanding-radius search** over
evidence-grade transactions only: `market_value_basis = 'STANDARD'` and
`is_residential`. Land Registry category B sales (repossessions, non-market
transfers) are visible in the property panel but never used as evidence.

Radii are jurisdiction-specific, because coordinate precision differs:

| | UK | France |
|---|---|---|
| Coordinates | postcode centroid | cadastral parcel |
| Radius steps | 400, 800, 1600, 3200, 6400 m | 200, 450, 900, 1800, 3600 m |
| Time window | 60 months | 48 months |

The UK's first ring is wider because a 200 m radius around a postcode centroid
would be spuriously precise.

The search is **two-pass**: same property type first, widening to all types only
if same-type evidence is too thin. The SQL uses `geom && ST_Expand(...)` so the
GIST index drives the scan, then `ST_DistanceSphere` trims to a true metric
radius.

### Step 2 — score each comparable

Property type is a **multiplicative gate**, not one weighted term among several:

```
similarity = type_affinity × weighted(distance, recency, floor-area ratio)
                             0.58        0.25      0.17
```

This is a correction made in response to measurement. With type as an additive
component (weight 0.28), a one-bed flat 100 m away scored **0.67** as evidence
for a four-bed detached house, because near-perfect proximity and recency
outvoted the type mismatch. Proximity cannot make a different kind of dwelling
comparable. As a gate, a detached-versus-flat affinity of 0.12 caps similarity
at 0.12 — below the inclusion floor, however close the sale was.

Comparables scoring below **`MIN_SIMILARITY = 0.35`** are discarded outright
rather than down-weighted. Weak comparables barely move the median but inflate
the dispersion, and therefore the published interval.

A missing feature (no floor area) **renormalises the weights** rather than
scoring zero — otherwise every UK property would be penalised for a dataset gap.

### Step 3 — restate every comparable at the valuation date

`index_adjust.py` multiplies each comparable by the ratio of the official index
at the valuation date to the index at its sale date, using the most local series
available and falling back in a stated order:

```
local authority + property type
  → local authority, all types
  → country (England / Wales)
  → UK
```

Every adjustment reports which level it used. The confidence model penalises
coarse adjustments, and the API returns the series in plain English:

> *UK HPI local authority series for Milton Keynes (semi detached): +8.2%
> between May 2022 and August 2026*

If no series covers the pair of dates, the function returns `None` and the
caller degrades honestly. **There is no synthetic fallback.**

### Step 4 — combine robustly

The estimate is a **similarity-weighted median of log adjusted prices**. Log
space because price distributions are right-skewed; weighted median because a
couple of unusual sales must not drag the answer.

Dispersion uses a **weighted interquartile range** scaled to a standard
deviation (÷1.349), not a MAD: comparable distributions are routinely
asymmetric (a few high-value sales on an otherwise uniform street) and the IQR
ignores both tails instead of being pulled by the heavier one.

### Step 5 — blend in the property's own past sale

If the dwelling itself has a recorded sale, it is index-adjusted to the
valuation date and blended in — usually the single strongest piece of evidence
about *this* dwelling rather than its street:

```
weight = 0.55 × exp(−years_since_sale / 7),  capped at 0.65 × n/(n+4)
```

Capped for two reasons: a single old sale reflects that transaction's
particulars (condition, chain, motivation) as much as the market, and a large
tight comparable set should not be outvoted by one data point.

### Step 6 — grade confidence and derive an interval

`confidence.py` is deliberately rule-based and inspectable rather than a learned
score, because the grade's job is to tell a user how much to trust a number and
that explanation has to hold up. Inputs: comparable count and effective weight,
median distance, dispersion, best-match similarity, staleness, how local the
index adjustment was, own-sale recency, forecast horizon. Every input is a
measured quantity, and the API returns the reasons:

```json
"confidence_reasons": [
  "17 nearby comparable sales",
  "comparables are on or beside the same street",
  "comparable prices agree closely",
  "the property itself sold 2 year(s) ago"
]
```

The interval half-width is `max(band_for_grade, observed_dispersion × 0.73)`.
The **0.73 is calibrated, not chosen** — see §4.

### Step 7 — refuse when the evidence is thin

The model returns `INSUFFICIENT_EVIDENCE` rather than a number when:

- fewer than 3 usable comparables, or effective weight below 1.6;
- no official index covers the dates;
- the confidence grade comes out `VERY_LOW`.

A `VERY_LOW` grade explicitly means *do not show a dwelling-level figure* — the
caller falls back to an area statistic, which is labelled `REGIONAL_STATISTIC`.

---

## 3. Historical estimates, and the leakage question

Two routes produce a `HISTORICAL_ESTIMATE`, tried in order:

**(a) Comparable sales around the target date.** Uses a *symmetric* window —
evidence from both before and after the target year. This is legitimate for a
retrospective back-cast and is labelled as such (`retrospective_window: true`,
method string "retrospective back-cast"). It is not used for forecasting.

**(b) Index back-cast from the current valuation** (`backcast.py`), when no
comparables exist near that date. Divides a well-evidenced *current* valuation
by the official local index movement. This is what makes the full timeline work
even for years outside the loaded transaction window.

Route (b) is a genuinely weaker method and is treated as such: the confidence
grade is demoted at least one step, the interval widens by 1.2 percentage points
per year reached back (capped at 22), and the API returns the limitation
verbatim:

> *Assumes the property tracked its local market and was in comparable
> condition; it cannot account for later extensions or refurbishment.*

### Temporal leakage
For **forecast validation**, leakage would invalidate the result, and
`ml/backtest_forecast.py` hard-truncates every series (including the national
shrinkage target) at the origin month.

For **evaluating the AVM**, `ml/eval_avm.py` defaults to *causal* mode: only
sales strictly before the target date are eligible, which is **stricter** than
the retrospective mode the product uses for back-casting. The reported error is
therefore a conservative bound, not a flattering one.

---

## 4. Measured accuracy

`make evaluate`, or:

```bash
cd backend && .venv/bin/python -m ml.eval_avm --limit 400
```

**Protocol.** Sample real transactions, hide each one, value the property as of
that sale's date, compare. The held-out sale is excluded from both the
comparable search and the own-prior-sale signal. Evidence mode: causal.

**Result — 398 held-out sales, England & Wales, 2023–2024:**

| Metric | Value |
|---|---|
| Median absolute percentage error | **7.18%** |
| Mean absolute percentage error | 10.69% |
| Median absolute error | £17,826 |
| Mean absolute error | £41,461 |
| Within 10% of the actual price | **65.6%** |
| Within 20% of the actual price | **85.2%** |
| Median signed error (bias) | −0.26% |

**Interval calibration** — does the published range actually contain the truth?

| | |
|---|---|
| Advertised coverage | 80% |
| **Measured coverage** | **84.4%** |
| Median half-width | ±17.8% |

Slightly conservative, which is the right side to err on. This is what the
0.73 dispersion multiplier was tuned for: at 0.90 the intervals covered 88.3%
against an advertised 80%, i.e. they were too wide to inform a decision. An
interval that over-covers is not "safe" — it is a different way of failing to
tell the user anything.

**Does the confidence grade mean anything?** Yes — this is the check that
matters most, because the grade is the product's main honesty signal:

| Grade | n | Median APE |
|---|---|---|
| HIGH | 366 | **6.84%** |
| MEDIUM | 28 | 12.91% |
| LOW | 4 | 20.05% |

Monotonic, with a ~3× spread between HIGH and LOW. A user who sees "low
confidence" is genuinely being warned.

**Refusals.** 2 of 400 sampled properties returned `INSUFFICIENT_EVIDENCE`
rather than a guess.

### Putting 7.18% in context
Commercial AVMs with full attribute data (floor area, bedrooms, condition,
photographs) typically report 4–8% median error. Reaching 7.18% from location,
property type and tenure alone is a reasonable result for open data, and it is
why the confidence grade and the interval matter: the model is good enough to be
useful and not good enough to be trusted blindly, and the UI says so.

---

## 5. Known limitations

1. **No floor area for the UK** without an EPC key, so no price-per-m² and no
   size adjustment. This is the single biggest available improvement.
2. **Postcode-centroid coordinates for the UK.** Within a large postcode the
   model cannot distinguish the good end of the street from the bad.
3. **No condition, extension or renovation data** anywhere. A refurbished and a
   dilapidated house on the same street are indistinguishable to the model.
4. **Category B exclusion is imperfect.** Land Registry cannot identify every
   non-market transfer, so some remain in the evidence pool.
5. **Thin markets.** Rural areas with few sales fall to LOW confidence or refuse
   outright — correctly, but it does mean coverage is uneven.
6. **The French derived index is not mix-adjusted** (see DATA_SOURCES §5).
7. **New-build premium is not modelled explicitly**, though `new_build` is
   stored and could become a feature.
