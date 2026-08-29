"""Async service wrapping the forecast model: loads real series, projects,
caches, and grades confidence."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from ..config import get_settings
from ..db import execute, fetch_all, fetch_one
from ..models.enums import Confidence
from ..valuation import index_adjust
from . import model as fmodel

log = logging.getLogger(__name__)

NATIONAL_CODES = {"GB": "K02000001"}
_ENGLAND = "E92000001"

# Loaded once per process from model_evaluations (written by the backtest).
_SIGMA_TABLE: dict[str, dict[int, float] | None] = {}


@dataclass(slots=True)
class MarketForecast:
    area_code: str
    area_name: str
    area_level: str
    segment: str
    origin_period: date
    target_period: date
    horizon_months: int
    ratio: float
    ratio_low: float
    ratio_high: float
    confidence: Confidence
    interval_basis: str
    diagnostics: dict


async def _sigma_by_horizon(country_iso2: str) -> dict[int, float] | None:
    """Empirical forecast error sd per horizon, from the stored backtest."""
    if country_iso2 in _SIGMA_TABLE:
        return _SIGMA_TABLE[country_iso2]
    row = await fetch_one(
        """
        SELECT metrics FROM model_evaluations
        WHERE model = 'forecast' AND evaluation_kind = 'backtest'
          AND country_iso2 = %s
        ORDER BY computed_at DESC LIMIT 1
        """,
        (country_iso2,),
    )
    table: dict[int, float] | None = None
    if row:
        metrics = row["metrics"]
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        raw = (metrics or {}).get("sigma_log_by_horizon_months")
        if isinstance(raw, dict) and raw:
            table = {int(k): float(v) for k, v in raw.items()}
    _SIGMA_TABLE[country_iso2] = table
    return table


async def _load_series(area_code: str, segment: str) -> tuple[list[date], list[float]]:
    rows = await fetch_all(
        """
        SELECT period, index_value FROM market_indices
        WHERE area_code = %s AND segment = %s AND index_value IS NOT NULL
        ORDER BY period
        """,
        (area_code, segment),
    )
    return [r["period"] for r in rows], [float(r["index_value"]) for r in rows]


async def _fit_for_area(
    country_iso2: str, area_code: str, segment: str
) -> fmodel.SeriesFit | None:
    """Fit the requested area, falling back to progressively coarser areas."""
    meta = await fetch_one(
        "SELECT DISTINCT area_name, area_level FROM market_indices "
        "WHERE area_code = %s LIMIT 1",
        (area_code,),
    )
    national_code = NATIONAL_CODES.get(country_iso2)
    nat_periods, nat_values = (
        await _load_series(national_code, "all") if national_code else ([], [])
    )

    candidates: list[tuple[str, str]] = [(area_code, segment)]
    if segment != "all":
        candidates.append((area_code, "all"))
    # Coarser geography, then the nation.
    for fallback in (_ENGLAND, national_code):
        if fallback:
            candidates.append((fallback, "all"))

    for code, seg in candidates:
        periods, values = await _load_series(code, seg)
        if len(values) < fmodel.MIN_MONTHS_REQUIRED:
            continue
        info = (
            meta
            if code == area_code
            else await fetch_one(
                "SELECT DISTINCT area_name, area_level FROM market_indices "
                "WHERE area_code = %s LIMIT 1",
                (code,),
            )
        )
        fitted = fmodel.fit(
            periods=periods,
            index_values=values,
            area_code=code,
            area_name=(info or {}).get("area_name", code),
            area_level=(info or {}).get("area_level", "unknown"),
            segment=seg,
            national_periods=nat_periods,
            national_values=nat_values,
        )
        if fitted:
            if code != area_code:
                fitted.notes.append(
                    f"requested area {area_code} had insufficient history; "
                    f"fitted {fitted.area_name} instead"
                )
            return fitted
    return None


def _grade(horizon_months: int, fitted: fmodel.SeriesFit, basis: str) -> Confidence:
    """Forecast confidence: horizon dominates, then how local and how stable
    the fitted series is."""
    years = horizon_months / 12.0
    score = 3.0
    score -= 0.42 * years
    if fitted.area_level == "local_authority":
        score += 0.5
    elif fitted.area_level in {"region", "country"}:
        score -= 0.3
    if fitted.months_observed >= 240:
        score += 0.3
    if fitted.residual_sigma > 0.05:
        score -= 0.4
    if basis != "backtest":
        score -= 0.5      # intervals not yet empirically calibrated

    if score >= 2.6:
        return Confidence.HIGH
    if score >= 1.4:
        return Confidence.MEDIUM
    if score >= 0.3:
        return Confidence.LOW
    return Confidence.VERY_LOW


async def forecast_market(
    *,
    country_iso2: str,
    district: str | None = None,
    area_code: str | None = None,
    segment: str = "all",
    target: date,
) -> MarketForecast | None:
    """Forecast the market movement between the latest observed month and
    `target`. Returns None if no series can support it."""
    if area_code is None:
        link = await index_adjust.resolve_area_code(country_iso2, district)
        area_code = link["area_code"] if link else NATIONAL_CODES.get(country_iso2)
    if not area_code:
        return None

    fitted = await _fit_for_area(country_iso2, area_code, segment)
    if fitted is None:
        return None

    horizon = fmodel.months_between(fitted.origin_period, date(target.year, target.month, 1))
    if horizon <= 0:
        return None
    max_h = get_settings().forecast_max_horizon_years * 12
    if horizon > max_h:
        return None

    sigma_table = await _sigma_by_horizon(country_iso2)
    projection = fmodel.project(fitted, [horizon], sigma_by_horizon=sigma_table)[0]
    conf = _grade(horizon, fitted, projection.interval_basis)

    diagnostics = fmodel.describe(fitted)
    diagnostics["interval_basis"] = projection.interval_basis
    diagnostics["sigma_log"] = round(projection.sigma_log, 4)
    diagnostics["horizon_months"] = horizon
    if projection.interval_basis == "provisional":
        diagnostics["interval_warning"] = (
            "Prediction interval is provisional: no historical backtest has "
            "been run for this market yet. Run ml/backtest_forecast.py."
        )

    return MarketForecast(
        area_code=fitted.area_code,
        area_name=fitted.area_name,
        area_level=fitted.area_level,
        segment=fitted.segment,
        origin_period=fitted.origin_period,
        target_period=projection.target_period,
        horizon_months=horizon,
        ratio=projection.ratio,
        ratio_low=projection.ratio_low,
        ratio_high=projection.ratio_high,
        confidence=conf,
        interval_basis=projection.interval_basis,
        diagnostics=diagnostics,
    )


async def cache_forecast(fc: MarketForecast, country_iso2: str, currency: str) -> None:
    """Persist a computed forecast so repeated views are cheap."""
    settings = get_settings()
    await execute(
        """
        INSERT INTO forecasts (
            country_iso2, area_code, area_name, segment, origin_period,
            target_period, horizon_months, index_ratio, ratio_low, ratio_high,
            currency_code, confidence, model, model_version, diagnostics
        ) VALUES (
            %(country)s, %(area_code)s, %(area_name)s, %(segment)s,
            %(origin)s, %(target)s, %(horizon)s, %(ratio)s, %(low)s, %(high)s,
            %(currency)s, %(confidence)s, %(model)s, %(version)s, %(diagnostics)s
        )
        ON CONFLICT (country_iso2, area_code, segment, origin_period,
                     target_period, model_version)
        DO UPDATE SET index_ratio = EXCLUDED.index_ratio,
                      ratio_low = EXCLUDED.ratio_low,
                      ratio_high = EXCLUDED.ratio_high,
                      confidence = EXCLUDED.confidence,
                      diagnostics = EXCLUDED.diagnostics,
                      computed_at = now()
        """,
        {
            "country": country_iso2,
            "area_code": fc.area_code,
            "area_name": fc.area_name,
            "segment": fc.segment,
            "origin": fc.origin_period,
            "target": fc.target_period,
            "horizon": fc.horizon_months,
            "ratio": fc.ratio,
            "low": fc.ratio_low,
            "high": fc.ratio_high,
            "currency": currency,
            "confidence": fc.confidence.value,
            "model": "shrunk local-drift on log UK HPI",
            "version": settings.forecast_model_version,
            "diagnostics": json.dumps(fc.diagnostics, default=str),
        },
    )


def clear_caches() -> None:
    _SIGMA_TABLE.clear()
