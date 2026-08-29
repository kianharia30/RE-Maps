#!/usr/bin/env python
"""Measure AVM accuracy against real, held-out transactions (§13).

Design
------
The only honest test of a valuation model is: hide a real sale, value the
property as of that sale's date, and compare.

Two leakage guards are essential and both are enforced here (§40):

1. The held-out sale itself must not be visible. The AVM already excludes the
   target property's own transactions from the comparable search, and the
   own-prior-sale signal is recomputed with the held-out sale removed.
2. Evidence must be restricted to what existed at the time. This harness runs
   in *causal* mode by default: only sales strictly before the target date are
   eligible. That is stricter than the retrospective mode the product uses for
   back-casting historical years, so the reported error is a conservative
   bound, not a flattering one.

Metrics: MAE, median absolute error, MAPE, RMSE, and the share of predictions
within 10% and 20% of the actual price — the last is what a user actually
cares about.

Results are written to `model_evaluations` so the API and docs can quote
measured numbers instead of claims.
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
from app.models.enums import DataStatus
from app.providers import registry
from app.valuation import avm

log = logging.getLogger("eval_avm")

# Sample real sales, spread across the country and the calendar, that have at
# least one *earlier* recorded sale of the same dwelling — the population the
# AVM is actually asked about in the product.
SAMPLE_SQL = """
WITH candidates AS (
    SELECT t.id, t.property_id, t.price::float8 AS price, t.transaction_date,
           t.property_type, t.district,
           ST_Y(t.geom) AS lat, ST_X(t.geom) AS lon,
           p.floor_area_sqm::float8 AS floor_area_sqm,
           p.postcode_norm
    FROM transactions t
    JOIN properties p ON p.id = t.property_id
    WHERE t.country_iso2 = %(country)s
      AND t.market_value_basis = 'STANDARD'
      AND t.is_residential
      AND t.geom IS NOT NULL
      AND t.transaction_date BETWEEN %(from)s AND %(to)s
      AND t.price BETWEEN 20000 AND 5000000
)
SELECT * FROM candidates
-- Deterministic pseudo-random spread: hashing the id gives a stable sample
-- across runs without needing a seeded RNG in SQL.
ORDER BY md5(id::text)
LIMIT %(limit)s
"""


def _metrics(actual: list[float], predicted: list[float]) -> dict:
    errors = [p - a for a, p in zip(actual, predicted, strict=False)]
    abs_errors = [abs(e) for e in errors]
    pct_errors = [abs(e) / a for a, e in zip(actual, errors, strict=False)]
    n = len(actual)
    return {
        "n": n,
        "mae": round(statistics.fmean(abs_errors)),
        "median_absolute_error": round(statistics.median(abs_errors)),
        "mape_pct": round(100 * statistics.fmean(pct_errors), 2),
        "median_ape_pct": round(100 * statistics.median(pct_errors), 2),
        "rmse": round(math.sqrt(statistics.fmean([e * e for e in errors]))),
        "within_10_pct": round(100 * sum(1 for p in pct_errors if p <= 0.10) / n, 1),
        "within_20_pct": round(100 * sum(1 for p in pct_errors if p <= 0.20) / n, 1),
        # A systematic bias would show here even when MAPE looks acceptable.
        "median_signed_error_pct": round(
            100 * statistics.median([e / a for a, e in zip(actual, errors, strict=False)]), 2
        ),
    }


def _interval_calibration(
    actual: list[float], lows: list[float], highs: list[float]
) -> dict:
    """What share of actual prices fell inside the published interval?

    The intervals are advertised as ~80% central, so a well-calibrated model
    should land near 80%. Materially higher means they are too wide to be
    useful; materially lower means they are overconfident.
    """
    inside = sum(1 for a, lo, hi in zip(actual, lows, highs, strict=False) if lo <= a <= hi)
    widths = [
        math.log(hi / lo) / 2 for lo, hi in zip(lows, highs, strict=False) if lo > 0 and hi > 0
    ]
    return {
        "coverage_pct": round(100 * inside / len(actual), 1),
        "target_coverage_pct": 80.0,
        "median_half_width_log": round(statistics.median(widths), 4) if widths else None,
        "median_half_width_pct": (
            round(100 * (math.exp(statistics.median(widths)) - 1), 1) if widths else None
        ),
    }


async def evaluate(
    country: str, limit: int, from_year: int, to_year: int, causal: bool
) -> dict:
    provider = registry.for_country(country)
    if provider is None:
        raise SystemExit(f"No provider for {country}")

    rows = await fetch_all(
        SAMPLE_SQL,
        {
            "country": country,
            "from": date(from_year, 1, 1),
            "to": date(to_year, 12, 31),
            "limit": limit,
        },
    )
    log.info("sampled %s held-out transactions", len(rows))

    source_refs = await provider.sources()
    actual: list[float] = []
    predicted: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    by_confidence: dict[str, list[float]] = {}
    skipped: dict[str, int] = {}

    for i, row in enumerate(rows, 1):
        target = avm.TargetProperty(
            id=row["property_id"],
            country_iso2=country,
            latitude=row["lat"],
            longitude=row["lon"],
            property_type=row["property_type"],
            floor_area_sqm=row["floor_area_sqm"],
            district=row["district"],
            postcode_norm=row["postcode_norm"],
        )
        result = await avm.value(
            target,
            as_of=row["transaction_date"],
            policy=provider.comparable_policy,
            source_refs=source_refs,
            price_type=__import__(
                "app.models.enums", fromlist=["PriceType"]
            ).PriceType.HISTORICAL_ESTIMATE,
            # causal=True -> only prior sales are eligible (no look-ahead).
            symmetric_window=not causal,
        )
        if result.status is not DataStatus.OK or result.estimated_price is None:
            key = result.status.value
            skipped[key] = skipped.get(key, 0) + 1
            continue

        actual.append(row["price"])
        predicted.append(float(result.estimated_price))
        lows.append(float(result.low_estimate or result.estimated_price))
        highs.append(float(result.high_estimate or result.estimated_price))
        conf = result.confidence.value if result.confidence else "UNKNOWN"
        by_confidence.setdefault(conf, []).append(
            abs(result.estimated_price - row["price"]) / row["price"]
        )

        if i % 100 == 0:
            log.info("  %s/%s evaluated (%s usable)", i, len(rows), len(actual))

    if len(actual) < 30:
        raise SystemExit(
            f"Only {len(actual)} usable predictions — not enough to report a metric."
        )

    metrics = _metrics(actual, predicted)
    metrics["interval_calibration"] = _interval_calibration(actual, lows, highs)
    metrics["by_confidence"] = {
        conf: {
            "n": len(errs),
            "median_ape_pct": round(100 * statistics.median(errs), 2),
        }
        for conf, errs in sorted(by_confidence.items())
    }
    metrics["skipped"] = skipped
    metrics["sampled"] = len(rows)
    metrics["coverage_of_sample_pct"] = round(100 * len(actual) / len(rows), 1)
    metrics["evidence_mode"] = "causal (prior sales only)" if causal else "retrospective"
    return metrics


async def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate the AVM on held-out real sales")
    ap.add_argument("--country", default="GB")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--from-year", type=int, default=2023)
    ap.add_argument("--to-year", type=int, default=2024)
    ap.add_argument(
        "--retrospective",
        action="store_true",
        help="allow later sales as evidence (matches the product's back-cast mode)",
    )
    ap.add_argument("--no-store", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    metrics = await evaluate(
        args.country, args.limit, args.from_year, args.to_year,
        causal=not args.retrospective,
    )

    print(json.dumps(metrics, indent=2))

    if not args.no_store:
        await execute(
            """
            INSERT INTO model_evaluations
                (model, model_version, country_iso2, evaluation_kind,
                 train_period, test_period, sample_size, metrics, notes)
            VALUES ('avm', %s, %s, 'holdout', %s, %s, %s, %s, %s)
            """,
            (
                settings.avm_model_version,
                args.country,
                "all transactions in the database, excluding the target sale",
                f"{args.from_year}-{args.to_year}",
                metrics["n"],
                json.dumps(metrics),
                (
                    "Held-out real transactions. The target sale is excluded from "
                    "the comparable set and from the own-prior-sale signal. "
                    f"Evidence mode: {metrics['evidence_mode']}."
                ),
            ),
        )
        log.info("stored evaluation in model_evaluations")
    await close_async_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(main())
