# Forecasting methodology

The headline fact about this forecaster is that **the first version did not
work**, the backtest said so, and the model in the product is the one that
survived recalibration. This document shows both, because a forecast is only
worth anything if you can see what it was tested against.

Reproduce everything here with `make evaluate` and `make calibrate`.

---

## 1. What is forecast, and what is not

Forecasts are produced for a **geographic market**, never for an individual
dwelling in isolation. A property's future value is then:

```
future value = current dwelling estimate × forecast market movement
```

That is the only claim the data supports. Nothing here pretends to know that
*this* house will outperform its street, and the API's `evidence` payload shows
both components separately so the split is visible.

**France has forecasting disabled.** Its official transaction history starts in
2021, and its price index is derived and not mix-adjusted (see
DATA_SOURCES §5). Three years of such a series cannot support a defensible
ten-year projection, so the API returns `OUT_OF_RANGE` with that reason rather
than extrapolating. This is the coverage registry doing its job.

---

## 2. The model

`backend/app/forecast/model.py`. Fitted to the real monthly UK House Price Index
series for the area.

```
g_long   long-run monthly log growth (up to 20 years of history)
g_short  recent monthly log growth (last 4 years)
d        = g_short − g_long          (momentum relative to trend)

Δlog(h) = λ_d · g_long · h  +  λ_m · d · Σ_{i=1..h} φ^i
```

Three things happen before those terms are used.

**Shrinkage toward the national series.** `g_long` is blended with the national
drift in proportion to how short the local history is
(`w_local = months / (months + 120)`). A small local authority with a jumpy
index does not get a confident idiosyncratic forecast.

**Damped momentum.** `φ = 0.945` monthly gives momentum a half-life of about 12
months, so the projection reverts to trend instead of extrapolating a hot or
cold spell linearly. The damped sum converges to ~17.2 months of contribution
however long the horizon.

**Empirical shrinkage.** `λ_d` and `λ_m` are multipliers chosen by measurement,
not argument. See the next section.

---

## 3. The calibration, including the version that failed

### The first attempt scored worse than "prices stay flat"

Running the backtest with the terms taken at face value (`λ_d = 1`, `λ_m = 1`):

| Horizon | Skill vs random walk | Bias (log) |
|---|---|---|
| 12 mo | **−0.089** | −0.008 |
| 36 mo | **−0.252** | +0.019 |
| 60 mo | **−0.343** | +0.073 |
| 120 mo | **−0.352** | +0.118 |

Negative skill at every horizon means the model was **worse than simply saying
prices will not change**, with a bias that grew to +12% in log terms by ten
years out. It beat undamped linear extrapolation, which is a low bar.

**Why.** An OLS drift fitted to UK house-price history is dominated by the
1995–2007 boom. Extrapolating it forward over-predicts, badly and increasingly
with horizon.

### The sweep

`make calibrate` fits every (series, origin) pair once, then scores every
shrinkage combination against a random-walk baseline. 376 index series, 7,577
fits, pooled out-of-sample RMSE in log space:

| λ_d (drift) | λ_m (momentum) | Pooled RMSE | Skill vs random walk |
|---|---|---|---|
| **0.35** | **0.00** | **0.13804** | **+0.314** |
| 0.35 | 0.25 | 0.14405 | +0.284 |
| 0.50 | 0.00 | 0.14550 | +0.277 |
| 0.25 | 0.00 | 0.14633 | +0.273 |
| 0.35 | 0.55 | 0.15441 | +0.233 |
| 1.00 | 1.00 | — | negative |

Random-walk baseline: 0.20122.

### Two conclusions, one of them uncomfortable

**λ_d = 0.35.** The fitted trend must be cut to about a third to be useful. UK
house prices behave close to a random walk with small drift.

**λ_m = 0.00 — the momentum term has no predictive value and is switched off.**
Every non-zero weight made the forecast worse. The damped-momentum machinery was
the original centrepiece of this model; measurement rejected it. The parameter
is kept so a future recalibration on different data can re-enable it, and the
reason it is zero is documented at the constant itself rather than buried here.

---

## 4. Accuracy after calibration

Rolling-origin backtest, origins from 2005, one per calendar year per series.
Errors are in **percentage points of cumulative growth**.

| Horizon | n | Model MAE | Random walk | Linear drift | Skill vs RW | Bias (log) |
|---|---|---|---|---|---|---|
| 12 mo | 7,577 | **4.6 pp** | 5.3 pp | 6.0 pp | **+0.070** | −0.008 |
| 24 mo | 7,201 | **7.3 pp** | 8.4 pp | 11.5 pp | **+0.121** | −0.015 |
| 36 mo | 6,825 | **9.4 pp** | 10.7 pp | 18.0 pp | **+0.156** | −0.019 |
| 48 mo | 6,449 | **11.4 pp** | 13.1 pp | 25.5 pp | **+0.184** | −0.022 |
| 60 mo | 6,073 | **13.2 pp** | 16.1 pp | 33.9 pp | **+0.236** | −0.031 |
| 84 mo | 5,321 | **15.8 pp** | 20.7 pp | 58.0 pp | **+0.329** | −0.038 |
| 120 mo | 4,193 | **16.6 pp** | 28.8 pp | 105.6 pp | **+0.482** | −0.057 |

Positive skill at every horizon, growing with horizon — which is what you would
expect from a model whose value is *not* extrapolating momentum. The residual
bias is small and slightly negative, i.e. mildly conservative.

Read the absolute numbers honestly: a ten-year forecast is still wrong by
**16.6 percentage points of growth on average**. That is why ten-year forecasts
are graded LOW or VERY_LOW confidence and shown with wide intervals.

---

## 5. Prediction intervals

Intervals are **not** a parametric guess. The backtest records the empirical
standard deviation of log forecast error at each horizon and stores it in
`model_evaluations`; `app/forecast/service.py` reads it back and builds
80% central intervals from measured error.

| Horizon | σ (log) | ⇒ 80% interval |
|---|---|---|
| 12 mo | 0.063 | ≈ ±8% |
| 24 mo | 0.093 | ≈ ±12% |
| 36 mo | 0.117 | ≈ ±15% |
| 48 mo | 0.139 | ≈ ±18% |
| 60 mo | 0.157 | ≈ ±21% |
| 84 mo | 0.183 | ≈ ±25% |
| 120 mo | 0.194 | ≈ ±26% |

Between measured horizons σ is interpolated linearly; beyond 120 months it is
extrapolated as √time. If no backtest has been run, the model falls back to a
provisional band and **says so** — the API returns
`interval_basis: "provisional"` plus an explicit warning, so an uncalibrated
deployment cannot quietly present uncalibrated intervals as measured ones.

A property-level forecast **compounds** the current valuation's own uncertainty
with the market forecast's, so a long-horizon range can never appear tighter
than the estimate it is built on.

---

## 6. Which market a forecast comes from

Each aggregated area is projected with **its own local index series**, resolved
from the modal district of its member sales and stored on the `area_stats` row
(`index_area_code`, migration `011`).

This was a real bug worth naming: only the district tier had a district name to
look up, so at outcode, sector and county level the resolution silently fell
back to the **national** series. Every outcode in the country would have been
projected with the same UK growth rate — a national average dressed up as a
local figure. Now the API reports `forecast_market` on every forecast area, and
an area with no local series gets **no forecast** rather than a national rate.

A spot check over 72 outcodes around Milton Keynes uses 12 distinct local
markets (LU2 → Luton, NN3 → West Northamptonshire, MK45 → Central
Bedfordshire, …).

---

## 7. Variables considered and rejected

The specification lists many candidate variables. Each was assessed against a
single test: *is there a reliable, licensable, sufficiently local series for
it, and does adding it beat the baseline out of sample?*

| Variable | Status | Reason |
|---|---|---|
| Historical local HPI | **used** | Official, monthly, per local authority |
| Local price momentum | **rejected by measurement** | Zero skill; see §3 |
| Inflation (CPI) | not used | Available nationally, but adds no *local* information; partly reflected in the index already |
| Mortgage / interest rates | not used | National series only, so it cannot differentiate local markets — it would shift every forecast identically |
| Local income growth | not used | ONS ASHE is annual and lagged ~18 months at local-authority level |
| Population growth | not used | Mid-year estimates are annual and heavily smoothed |
| Housing supply / completions | not used | MHCLG live tables are annual and inconsistent between authorities |
| Unemployment | not used | Claimant-count is available locally but was not shown to improve out-of-sample RMSE |

Adding a national-only variable to a local forecast is the trap here: it would
make the model look more sophisticated while adding no ability to distinguish
one market from another. The honest position is that a shrunk local trend, with
measured intervals, is what this data supports.

---

## 8. Limitations

1. **Turning points are not predicted.** A trend model cannot see a crash or a
   policy shock. The intervals reflect historical error including past turning
   points, which is the most that can be claimed.
2. **The index is mix-adjusted, individual properties are not.** A dwelling can
   diverge from its index for reasons the model cannot observe.
3. **Local-authority granularity.** Within a large authority, sub-markets move
   differently.
4. **Backtest origins start in 2005**, so the sample covers the financial
   crisis, the post-2013 recovery and the pandemic period — but only one full
   cycle. Interval widths are estimated from limited independent history.
5. **Ten years is the cap** (`FORECAST_MAX_HORIZON_YEARS`), and even that is
   presented at low confidence.
