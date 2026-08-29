#!/usr/bin/env python
"""Backtest the forecaster on real history, and calibrate its intervals (§39).

Protocol
--------
For each local-authority index series and each origin month T:

    fit using ONLY observations up to and including T
    predict T+h for a set of horizons h
    compare with what the index actually did

The training slice is a hard truncation of the series, so there is no way for a
future observation to reach the fit (§40). Origins are spaced a year apart to
keep the samples reasonably independent.

Outputs
-------
1. Accuracy per horizon: MAE and median error in percentage points of cumulative
   growth, plus a comparison against two honest baselines:

     random walk  — "prices stay flat from here"
     linear drift — "recent growth continues indefinitely, undamped"

   A forecast that cannot beat a random walk has no predictive value, and this
   is where that gets found out rather than asserted.

2. The empirical standard deviation of log forecast error per horizon. This is
   written to `model_evaluations` and read back by
   `app/forecast/service.py` to build prediction intervals from measured error
   instead of a parametric guess (§16).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
from datetime import date

from app.config import get_settings
from app.db import close_async_pool, execute, fetch_all
from app.forecast import model as fmodel

log = logging.getLogger("backtest_forecast")

HORIZONS_MONTHS = [12, 24, 36, 48, 60, 84, 120]

SERIES_SQL = """
SELECT area_code, area_name, area_level,
       array_agg(period ORDER BY period)      AS periods,
       array_agg(index_value ORDER BY period) AS values
FROM market_indices
WHERE country_iso2 = %(country)s
  AND segment = 'all'
  AND index_value IS NOT NULL
  AND area_level = ANY(%(levels)s::text[])
GROUP BY area_code, area_name, area_level
HAVING count(*) >= %(min_months)s
"""


def _add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


async def _collect_fits(country: str, min_origin_year: int, max_series: int | None):
    """Fit every (series, origin) once and keep the fits in memory.

    The calibration sweep evaluates many shrinkage settings; refitting for each
    would be wasteful, and the fit itself does not depend on the shrinkage.
    Returns (fits, national_series) where each fit carries the actual future
    ratios it will be scored against.
    """
    rows = await fetch_all(
        SERIES_SQL,
        {
            "country": country,
            "levels": ["local_authority", "region", "country"],
            "min_months": fmodel.MIN_MONTHS_REQUIRED + max(HORIZONS_MONTHS),
        },
    )
    if max_series:
        rows = rows[:max_series]
    log.info("collecting fits from %s index series", len(rows))

    national = next(
        (r for r in rows if r["area_level"] == "country" and r["area_code"].startswith("K")),
        None,
    )
    national_values = [float(v) for v in national["values"]] if national else None
    national_periods = list(national["periods"]) if national else None

    fits: list[tuple[fmodel.SeriesFit, dict[int, float]]] = []
    for row in rows:
        periods = list(row["periods"])
        values = [float(v) for v in row["values"]]
        for origin_idx in range(len(periods)):
            origin_period = periods[origin_idx]
            if origin_period.year < min_origin_year or origin_period.month != 1:
                continue
            if origin_idx + 1 < fmodel.MIN_MONTHS_REQUIRED:
                continue

            nat_train = None
            if national_values and national_periods:
                nat_cut = [
                    v for p, v in zip(national_periods, national_values, strict=False)
                    if p <= origin_period
                ]
                nat_train = nat_cut if len(nat_cut) >= fmodel.MIN_MONTHS_REQUIRED else None

            fitted = fmodel.fit(
                periods=periods[: origin_idx + 1],
                index_values=values[: origin_idx + 1],
                area_code=row["area_code"],
                area_name=row["area_name"],
                area_level=row["area_level"],
                segment="all",
                national_periods=None,
                national_values=nat_train,
            )
            if fitted is None:
                continue

            actuals: dict[int, float] = {}
            for h in HORIZONS_MONTHS:
                idx = origin_idx + h
                if idx < len(values) and values[origin_idx] > 0 and values[idx] > 0:
                    actuals[h] = values[idx] / values[origin_idx]
            if actuals:
                fits.append((fitted, actuals))
    log.info("collected %s (series, origin) fits", len(fits))
    return fits


def _score(fits, drift: float, momentum: float) -> dict[int, list[float]]:
    """Log errors per horizon for one shrinkage setting."""
    errors: dict[int, list[float]] = {h: [] for h in HORIZONS_MONTHS}
    for fitted, actuals in fits:
        horizons = sorted(actuals)
        projections = fmodel.project(
            fitted, horizons, drift_shrinkage=drift, momentum_shrinkage=momentum
        )
        for proj in projections:
            actual = actuals.get(proj.horizon_months)
            if actual is None:
                continue
            errors[proj.horizon_months].append(
                math.log(proj.ratio) - math.log(actual)
            )
    return errors


def _rmse(errs: dict[int, list[float]]) -> float:
    """Pooled RMSE across horizons, used as the sweep's objective."""
    flat = [e for v in errs.values() for e in v]
    return math.sqrt(statistics.fmean([e * e for e in flat])) if flat else float("inf")


async def calibrate(country: str, min_origin_year: int, max_series: int | None) -> dict:
    """Sweep the shrinkage multipliers and report skill against a random walk."""
    fits = await _collect_fits(country, min_origin_year, max_series)
    if not fits:
        raise SystemExit("no fits collected — is the index data loaded?")

    # Random-walk baseline: predict no change.
    rw = {h: [] for h in HORIZONS_MONTHS}
    for _fitted, actuals in fits:
        for h, actual in actuals.items():
            rw[h].append(0.0 - math.log(actual))
    rw_rmse = _rmse(rw)

    results = []
    for drift in [0.0, 0.15, 0.25, 0.35, 0.5, 0.7, 1.0]:
        for momentum in [0.0, 0.25, 0.55, 0.8, 1.0]:
            errs = _score(fits, drift, momentum)
            rmse = _rmse(errs)
            results.append(
                {
                    "drift_shrinkage": drift,
                    "momentum_shrinkage": momentum,
                    "pooled_rmse_log": round(rmse, 5),
                    "skill_vs_random_walk": round(1 - rmse / rw_rmse, 4),
                }
            )
    results.sort(key=lambda r: r["pooled_rmse_log"])
    best = results[0]
    log.info(
        "best: drift=%s momentum=%s skill_vs_random_walk=%+.4f",
        best["drift_shrinkage"], best["momentum_shrinkage"],
        best["skill_vs_random_walk"],
    )
    return {
        "random_walk_pooled_rmse_log": round(rw_rmse, 5),
        "best": best,
        "sweep": results,
        "fits": len(fits),
    }


async def backtest(country: str, min_origin_year: int, max_series: int | None) -> dict:
    rows = await fetch_all(
        SERIES_SQL,
        {
            "country": country,
            "levels": ["local_authority", "region", "country"],
            # Enough history to fit AND leave room to score the longest horizon.
            "min_months": fmodel.MIN_MONTHS_REQUIRED + max(HORIZONS_MONTHS),
        },
    )
    if max_series:
        rows = rows[:max_series]
    log.info("backtesting %s index series", len(rows))

    national = next(
        (r for r in rows if r["area_level"] == "country" and r["area_code"].startswith("K")),
        None,
    )
    national_values = [float(v) for v in national["values"]] if national else None
    national_periods = list(national["periods"]) if national else None

    # horizon -> list of log errors (predicted minus actual, in log space)
    errors: dict[int, list[float]] = {h: [] for h in HORIZONS_MONTHS}
    rw_errors: dict[int, list[float]] = {h: [] for h in HORIZONS_MONTHS}
    drift_errors: dict[int, list[float]] = {h: [] for h in HORIZONS_MONTHS}
    origins_used = 0

    for row in rows:
        periods = list(row["periods"])
        values = [float(v) for v in row["values"]]

        for origin_idx in range(len(periods)):
            origin_period = periods[origin_idx]
            if origin_period.year < min_origin_year or origin_period.month != 1:
                continue  # one origin per year, in January
            # Need enough history behind the origin to fit at all.
            if origin_idx + 1 < fmodel.MIN_MONTHS_REQUIRED:
                continue

            train_periods = periods[: origin_idx + 1]
            train_values = values[: origin_idx + 1]

            # National series is truncated to the same origin, so the shrinkage
            # target cannot leak future information either.
            nat_train = None
            if national_values and national_periods:
                nat_cut = [
                    v for p, v in zip(national_periods, national_values, strict=False)
                    if p <= origin_period
                ]
                nat_train = nat_cut if len(nat_cut) >= fmodel.MIN_MONTHS_REQUIRED else None

            fitted = fmodel.fit(
                periods=train_periods,
                index_values=train_values,
                area_code=row["area_code"],
                area_name=row["area_name"],
                area_level=row["area_level"],
                segment="all",
                national_periods=None,
                national_values=nat_train,
            )
            if fitted is None:
                continue

            scored_any = False
            projections = fmodel.project(fitted, HORIZONS_MONTHS)  # calibrated defaults
            for proj in projections:
                target_idx = origin_idx + proj.horizon_months
                if target_idx >= len(values):
                    continue
                actual_ratio = values[target_idx] / values[origin_idx]
                if actual_ratio <= 0:
                    continue

                log_actual = math.log(actual_ratio)
                errors[proj.horizon_months].append(math.log(proj.ratio) - log_actual)
                # Baseline 1: random walk (ratio == 1, i.e. no change).
                rw_errors[proj.horizon_months].append(0.0 - log_actual)
                # Baseline 2: undamped linear extrapolation of recent growth.
                drift_errors[proj.horizon_months].append(
                    fitted.g_short * proj.horizon_months - log_actual
                )
                scored_any = True
            if scored_any:
                origins_used += 1

    def summarise(errs: dict[int, list[float]]) -> dict:
        out = {}
        for h, values_ in sorted(errs.items()):
            if len(values_) < 20:
                continue
            abs_pp = [abs(math.exp(e) - 1) * 100 for e in values_]
            out[str(h)] = {
                "n": len(values_),
                "mae_pct_points": round(statistics.fmean(abs_pp), 2),
                "median_ae_pct_points": round(statistics.median(abs_pp), 2),
                "rmse_log": round(
                    math.sqrt(statistics.fmean([e * e for e in values_])), 4
                ),
                "bias_log": round(statistics.fmean(values_), 4),
            }
        return out

    model_summary = summarise(errors)
    rw_summary = summarise(rw_errors)
    drift_summary = summarise(drift_errors)

    # The calibration table the forecaster reads back.
    sigma_by_horizon = {
        h: round(statistics.pstdev(v), 5)
        for h, v in sorted(errors.items())
        if len(v) >= 20
    }

    # Skill: >0 means we beat the baseline on RMSE at that horizon.
    skill = {}
    for h in model_summary:
        m = model_summary[h]["rmse_log"]
        for name, baseline in (("vs_random_walk", rw_summary), ("vs_linear_drift", drift_summary)):
            b = baseline.get(h, {}).get("rmse_log")
            if b:
                skill.setdefault(h, {})[name] = round(1 - m / b, 4)

    return {
        "series_tested": len(rows),
        "origins_used": origins_used,
        "min_origin_year": min_origin_year,
        "horizons_months": HORIZONS_MONTHS,
        "model": model_summary,
        "baseline_random_walk": rw_summary,
        "baseline_linear_drift": drift_summary,
        "skill_score_rmse": skill,
        "sigma_log_by_horizon_months": sigma_by_horizon,
        "protocol": (
            "Each origin fits only on observations up to and including that "
            "month; the national shrinkage series is truncated identically. "
            "One origin per calendar year (January) per series."
        ),
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest the market forecaster")
    ap.add_argument("--country", default="GB")
    ap.add_argument("--min-origin-year", type=int, default=2005)
    ap.add_argument("--max-series", type=int, default=None)
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument(
        "--calibrate",
        action="store_true",
        help="sweep the shrinkage multipliers instead of scoring the current ones",
    )
    args = ap.parse_args()

    if args.calibrate:
        print(json.dumps(
            await calibrate(args.country, args.min_origin_year, args.max_series),
            indent=2,
        ))
        await close_async_pool()
        return

    metrics = await backtest(args.country, args.min_origin_year, args.max_series)
    print(json.dumps(metrics, indent=2))

    if not args.no_store:
        settings = get_settings()
        await execute(
            """
            INSERT INTO model_evaluations
                (model, model_version, country_iso2, evaluation_kind,
                 train_period, test_period, sample_size, metrics, notes)
            VALUES ('forecast', %s, %s, 'backtest', %s, %s, %s, %s, %s)
            """,
            (
                settings.forecast_model_version,
                args.country,
                f"expanding window, origins from {args.min_origin_year}",
                "1 to 10 years ahead of each origin",
                metrics["origins_used"],
                json.dumps(metrics),
                (
                    "Rolling-origin backtest on real UK HPI series. Intervals "
                    "served by the API are built from "
                    "sigma_log_by_horizon_months recorded here."
                ),
            ),
        )
        log.info("stored backtest; forecast intervals are now empirically calibrated")
    await close_async_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(main())
