"""Market-level price forecasting (§15, §16, §39).

Level
-----
Forecasts are produced for a **geographic market**, not for an individual
dwelling. A dwelling's future value is then

    future value = current dwelling estimate x forecast market movement

which is the only claim the data supports. Nothing here pretends to know that
*this* house will outperform its street.

Model: shrunk local-drift on the log price index
------------------------------------------------
Fitted to the real monthly UK House Price Index series for the area:

  g_long   long-run monthly log growth (up to 20 years of history)
  g_short  recent monthly log growth (last 4 years)
  d        = g_short - g_long, i.e. current momentum relative to trend

  Dlog(h) = lambda_d * g_long * h  +  lambda_m * d * sum_{i=1..h} phi^i

`g_long` is first shrunk toward the national series in proportion to how short
the local history is, so a small local authority with a jumpy index does not
get a confident idiosyncratic forecast.

Both terms are then multiplied by shrinkage factors that were CHOSEN BY
MEASUREMENT, not by argument (`ml/backtest_forecast.py --calibrate`):

  lambda_d = 0.35   the fitted trend is heavily over-optimistic if taken at
                    face value, because UK house-price history is dominated by
                    the 1995-2007 boom
  lambda_m = 0.00   the momentum term turned out to have NO predictive value at
                    this resolution and was switched off

The unshrunk version of this model scored WORSE than "prices stay flat" at
every horizon. The calibrated version beats that baseline at every horizon, by
+7% at one year rising to +48% at ten. That measurement is the only reason to
trust it, and it is reproducible with the backtest script.

Intervals
---------
NOT a parametric guess. `ml/backtest_forecast.py` runs the model over real
historical origins (fit to data available at time T, predict T+h, compare with
what actually happened) and stores the empirical distribution of log errors per
horizon in `model_evaluations`. Those measured quantiles are the intervals this
module serves. If no backtest has been run, the model says so and returns
wider, explicitly-flagged provisional bands.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

log = logging.getLogger(__name__)

# --- fitted-model hyperparameters ------------------------------------------
LONG_WINDOW_MONTHS = 240      # 20 years of history for the long-run drift
SHORT_WINDOW_MONTHS = 48      # 4 years for current momentum
MOMENTUM_DAMPING = 0.945      # monthly; momentum half-life ~12.3 months
MIN_MONTHS_REQUIRED = 96      # 8 years — below this we do not forecast locally

# --- empirically calibrated shrinkage --------------------------------------
#
# These two multipliers are NOT taste. They were chosen by
# `ml/backtest_forecast.py --calibrate`, which sweeps them over a rolling-origin
# backtest of every real UK HPI series and scores against a random-walk
# baseline.
#
# Why they are well below 1.0: an OLS drift fitted to UK house-price history is
# dominated by the 1995-2007 boom, and extrapolating it forward over-predicts
# badly (the uncalibrated model scored WORSE than "prices stay flat" at every
# horizon, with a bias that grew to +12% in log terms by ten years out).
# House prices behave close to a random walk with small drift, so both the
# trend and the momentum term have to be pulled hard toward zero to add value.
#
# See docs/FORECASTING.md for the measured sweep and the resulting skill scores.
DRIFT_SHRINKAGE = 0.35        # multiplier on the long-run drift
#
# MOMENTUM_SHRINKAGE = 0.0 is a measured result, not an oversight. The damped
# momentum term was the original centrepiece of this model, and the calibration
# sweep rejected it: pooled out-of-sample RMSE (log) over 7,577 (series, origin)
# fits was
#
#     drift 0.35, momentum 0.00  ->  0.13804   skill vs random walk  +0.314
#     drift 0.35, momentum 0.25  ->  0.14405                         +0.284
#     drift 0.35, momentum 0.55  ->  0.15441                         +0.233
#     drift 1.00, momentum 1.00  ->  (worse than random walk at every horizon)
#
# Every non-zero momentum weight made the forecast worse. Recent local
# momentum in the UK HPI carries no usable signal about the next few years at
# this geographic resolution, so the term is switched off. The parameter is
# kept so a future recalibration on different data can re-enable it rather
# than having to reintroduce the machinery.
MOMENTUM_SHRINKAGE = 0.0      # multiplier on the momentum deviation

# Shrinkage of the local drift toward the national drift. `k` is the number of
# months of local history at which local and national are weighted equally.
SHRINK_K_MONTHS = 120.0

# Provisional interval widths (log space) used only when no backtest exists.
# Deliberately generous, and flagged as uncalibrated in the response.
_PROVISIONAL_SIGMA_PER_SQRT_YEAR = 0.075


@dataclass(slots=True)
class SeriesFit:
    area_code: str
    area_name: str
    area_level: str
    segment: str
    origin_period: date
    origin_index: float
    months_observed: int
    g_long: float
    g_short: float
    g_used_long: float          # after national shrinkage
    momentum: float
    shrinkage_weight: float
    national_g_long: float | None
    residual_sigma: float       # in-sample monthly residual sd (diagnostic)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Projection:
    horizon_months: int
    target_period: date
    ratio: float
    ratio_low: float
    ratio_high: float
    sigma_log: float
    interval_basis: str         # "backtest" | "provisional"


def _ols_slope(y: np.ndarray) -> tuple[float, float]:
    """Slope per step and residual sd of a straight line through `y`."""
    n = len(y)
    if n < 3:
        return 0.0, 0.0
    x = np.arange(n, dtype=float)
    x_mean, y_mean = x.mean(), y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom <= 0:
        return 0.0, 0.0
    slope = ((x - x_mean) * (y - y_mean)).sum() / denom
    intercept = y_mean - slope * x_mean
    resid = y - (intercept + slope * x)
    return float(slope), float(resid.std(ddof=2)) if n > 3 else 0.0


def fit(
    *,
    periods: list[date],
    index_values: list[float],
    area_code: str,
    area_name: str,
    area_level: str,
    segment: str,
    national_periods: list[date] | None = None,
    national_values: list[float] | None = None,
) -> SeriesFit | None:
    """Fit the drift model to one real index series.

    Returns None when the series is too short to support a local forecast; the
    caller should then fall back to a coarser area or decline to forecast.
    """
    if len(index_values) < MIN_MONTHS_REQUIRED:
        return None

    y = np.log(np.asarray(index_values, dtype=float))
    if not np.all(np.isfinite(y)):
        return None

    long_slice = y[-LONG_WINDOW_MONTHS:]
    short_slice = y[-SHORT_WINDOW_MONTHS:]
    g_long, resid_sigma = _ols_slope(long_slice)
    g_short, _ = _ols_slope(short_slice)

    national_g: float | None = None
    if national_values and len(national_values) >= MIN_MONTHS_REQUIRED:
        national_g, _ = _ols_slope(
            np.log(np.asarray(national_values[-LONG_WINDOW_MONTHS:], dtype=float))
        )

    # Shrink the local long-run drift toward the national one. A long local
    # history earns most of its own weight; a short one mostly borrows.
    months = len(y)
    if national_g is not None:
        w_local = months / (months + SHRINK_K_MONTHS)
        g_used_long = w_local * g_long + (1 - w_local) * national_g
        shrink = 1 - w_local
    else:
        g_used_long = g_long
        shrink = 0.0

    notes: list[str] = []
    if months < LONG_WINDOW_MONTHS:
        notes.append(
            f"local history is {months} months, shorter than the {LONG_WINDOW_MONTHS}-month "
            "window, so the long-run trend is partly borrowed from the national series"
        )
    if area_level != "local_authority":
        notes.append(f"fitted at {area_level} level, not local authority")

    return SeriesFit(
        area_code=area_code,
        area_name=area_name,
        area_level=area_level,
        segment=segment,
        origin_period=periods[-1],
        origin_index=float(index_values[-1]),
        months_observed=months,
        g_long=g_long,
        g_short=g_short,
        g_used_long=g_used_long,
        momentum=g_short - g_used_long,
        shrinkage_weight=shrink,
        national_g_long=national_g,
        residual_sigma=resid_sigma,
        notes=notes,
    )


def _damped_momentum_sum(horizon: int, phi: float = MOMENTUM_DAMPING) -> float:
    """sum_{i=1..h} phi^i — the decaying contribution of current momentum."""
    if phi >= 1.0:
        return float(horizon)
    return phi * (1.0 - phi**horizon) / (1.0 - phi)


def project(
    fitted: SeriesFit,
    horizons_months: list[int],
    *,
    sigma_by_horizon: dict[int, float] | None = None,
    drift_shrinkage: float = DRIFT_SHRINKAGE,
    momentum_shrinkage: float = MOMENTUM_SHRINKAGE,
    damping: float = MOMENTUM_DAMPING,
) -> list[Projection]:
    """Cumulative index ratios at the requested horizons, with intervals.

    The shrinkage arguments exist so `ml/backtest_forecast.py` can sweep them
    against real history; production always uses the calibrated defaults.
    """
    out: list[Projection] = []
    for h in horizons_months:
        if h <= 0:
            out.append(
                Projection(0, fitted.origin_period, 1.0, 1.0, 1.0, 0.0, "n/a")
            )
            continue

        delta_log = (
            drift_shrinkage * fitted.g_used_long * h
            + momentum_shrinkage * fitted.momentum * _damped_momentum_sum(h, damping)
        )
        ratio = math.exp(delta_log)

        if sigma_by_horizon:
            sigma, basis = _interpolate_sigma(sigma_by_horizon, h), "backtest"
        else:
            sigma = _PROVISIONAL_SIGMA_PER_SQRT_YEAR * math.sqrt(h / 12.0)
            basis = "provisional"

        # 80% central interval (z = 1.2816). A house-price forecast band wider
        # than this stops being informative; the confidence grade carries the
        # rest of the message.
        z = 1.2816
        out.append(
            Projection(
                horizon_months=h,
                target_period=_add_months(fitted.origin_period, h),
                ratio=ratio,
                ratio_low=ratio * math.exp(-z * sigma),
                ratio_high=ratio * math.exp(z * sigma),
                sigma_log=sigma,
                interval_basis=basis,
            )
        )
    return out


def _interpolate_sigma(table: dict[int, float], h: int) -> float:
    """Linear interpolation between measured horizons; sqrt-extrapolation past
    the longest horizon the backtest covers."""
    if h in table:
        return table[h]
    keys = sorted(table)
    if h < keys[0]:
        return table[keys[0]] * math.sqrt(h / keys[0])
    if h > keys[-1]:
        return table[keys[-1]] * math.sqrt(h / keys[-1])
    lo = max(k for k in keys if k <= h)
    hi = min(k for k in keys if k >= h)
    if lo == hi:
        return table[lo]
    frac = (h - lo) / (hi - lo)
    return table[lo] + frac * (table[hi] - table[lo])


def _add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def months_between(origin: date, target: date) -> int:
    return (target.year - origin.year) * 12 + (target.month - origin.month)


def describe(fitted: SeriesFit) -> dict:
    """Diagnostics surfaced through the API so a forecast can be inspected."""
    return {
        "model": "shrunk local-drift on log UK HPI",
        "fitted_area": fitted.area_name,
        "fitted_area_code": fitted.area_code,
        "fitted_area_level": fitted.area_level,
        "segment": fitted.segment,
        "origin_month": fitted.origin_period.isoformat(),
        "months_of_history": fitted.months_observed,
        "fitted_long_run_growth_annual_pct": round(
            (math.exp(fitted.g_used_long * 12) - 1) * 100, 2
        ),
        "applied_long_run_growth_annual_pct": round(
            (math.exp(DRIFT_SHRINKAGE * fitted.g_used_long * 12) - 1) * 100, 2
        ),
        "recent_growth_annual_pct": round((math.exp(fitted.g_short * 12) - 1) * 100, 2),
        "drift_shrinkage": DRIFT_SHRINKAGE,
        "momentum_shrinkage": MOMENTUM_SHRINKAGE,
        "momentum_annual_pct_points": round(
            ((math.exp(fitted.g_short * 12) - 1) - (math.exp(fitted.g_used_long * 12) - 1)) * 100, 2
        ),
        "momentum_damping_monthly": MOMENTUM_DAMPING,
        "momentum_half_life_months": round(math.log(0.5) / math.log(MOMENTUM_DAMPING), 1),
        "national_shrinkage_weight": round(fitted.shrinkage_weight, 3),
        "in_sample_residual_sd_log": round(fitted.residual_sigma, 4),
        "notes": fitted.notes,
    }
