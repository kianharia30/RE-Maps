"""The Automated Valuation Model (§13).

Method: **index-adjusted comparable sales, blended with the property's own
index-adjusted prior sale.**

Why this and not gradient boosting as the primary model: the only property
attributes England & Wales open data gives us for every dwelling are location,
property type, tenure and new-build status. A learned model over four weak
features cannot beat a well-constructed local comparable set, and it would hide
the reasoning the product needs to display. A hedonic LightGBM model *is*
trained (ml/train_hedonic.py) and evaluated against this baseline on held-out
real sales; it is used only where measurement shows it helps. See
docs/VALUATION.md for the measured numbers.

Steps
-----
1. Find real comparable sales near the target (comparables.py).
2. Restate each at the valuation date with the most local official index
   available (index_adjust.py).
3. Combine into a robust weighted central estimate.
4. If the property has its own recorded sale, restate that too and blend it in
   — usually the single strongest piece of evidence about *this* dwelling.
5. Grade confidence and derive an interval from measured dispersion.
6. If the evidence is too thin, return INSUFFICIENT_EVIDENCE. Never a guess.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from ..config import get_settings
from ..db import fetch_all
from ..models.enums import Confidence, DataStatus, PrecisionLevel, PriceType
from ..models.property import Comparable, ValuationResult
from ..providers.base import ComparableSearchPolicy
from . import comparables as comps_mod
from . import index_adjust
from .confidence import INTERVAL_BY_CONFIDENCE, ConfidenceInputs, grade

log = logging.getLogger(__name__)

# Absolute floor on evidence. Below this we do not publish a dwelling-level
# figure at any confidence — the caller falls back to an area statistic.
MIN_COMPARABLES = 3
MIN_EFFECTIVE_WEIGHT = 1.6

# Comparables scoring below this are discarded rather than down-weighted.
# Measured effect: without it, a 2-bed flat enters the evidence pool for a
# 4-bed detached house on the same street at ~0.2 weight, and enough of them
# inflate the dispersion (and therefore the published interval) far more than
# they shift the median. A hard floor is the honest fix — those sales are not
# evidence about this dwelling.
MIN_SIMILARITY = 0.35

# Cap on the published interval half-width in log space. Beyond roughly +/-38%
# an interval stops informing a decision; at that point the right answer is to
# report insufficient evidence, which the confidence grade already does.
MAX_INTERVAL_HALF = 0.32

# Maximum share of the estimate that the property's own past sale may carry.
# Capped because a single old sale reflects that transaction's particulars
# (condition, chain, motivation) as much as the market.
_MAX_OWN_WEIGHT = 0.55
# Years at which the own-sale weight decays to 1/e of its cap.
_OWN_DECAY_YEARS = 7.0


@dataclass(slots=True)
class TargetProperty:
    id: int | None
    country_iso2: str
    latitude: float
    longitude: float
    property_type: str | None
    floor_area_sqm: float | None
    district: str | None
    postcode_norm: str | None
    address_line: str | None = None


# --- robust statistics ------------------------------------------------------


def _weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    pairs = sorted(zip(values, weights, strict=False))
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[len(pairs) // 2][0]
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= q * total:
            return value
    return pairs[-1][0]


def _weighted_dispersion(values: list[float], weights: list[float]) -> float:
    """Weighted interquartile range, scaled to a standard-deviation equivalent.

    Preferred over a weighted MAD here because the comparable distribution is
    routinely asymmetric (a few high-value sales on an otherwise uniform
    street), and the IQR ignores both tails rather than being pulled by the
    heavier one. Divide by 1.349 to put it on an sd scale for a normal.
    """
    if len(values) < 4:
        centre = _weighted_quantile(values, weights, 0.5)
        deviations = [abs(v - centre) for v in values]
        return _weighted_quantile(deviations, weights, 0.5) * 1.4826
    p25 = _weighted_quantile(values, weights, 0.25)
    p75 = _weighted_quantile(values, weights, 0.75)
    return max(0.0, (p75 - p25) / 1.349)


# --- own-sale evidence ------------------------------------------------------

_OWN_SALES_SQL = """
SELECT t.id, t.price::float8 AS price, t.transaction_date, t.property_type,
       t.district, t.market_value_basis
FROM transactions t
WHERE t.property_id = %s
  AND t.market_value_basis = 'STANDARD'
  AND t.is_residential
ORDER BY t.transaction_date DESC
"""


async def _own_sale_evidence(
    property_id: int | None, as_of: date, country_iso2: str
) -> tuple[float | None, float | None, dict | None]:
    """(index-adjusted value, years since sale, detail) from this dwelling's
    own most relevant recorded sale."""
    if property_id is None:
        return None, None, None
    rows = await fetch_all(_OWN_SALES_SQL, (property_id,))
    if not rows:
        return None, None, None

    # Prefer the most recent sale at or before the valuation date; if the
    # dwelling only sold afterwards, a later sale is still legitimate evidence
    # for a *retrospective* historical estimate, so use the nearest in time.
    before = [r for r in rows if r["transaction_date"] <= as_of]
    chosen = before[0] if before else min(
        rows, key=lambda r: abs((r["transaction_date"] - as_of).days)
    )

    adj = await index_adjust.adjustment(
        country_iso2=country_iso2,
        district=chosen["district"],
        property_type=chosen["property_type"],
        from_date=chosen["transaction_date"],
        to_date=as_of,
    )
    if adj is None:
        return None, None, None

    years = abs((as_of - chosen["transaction_date"]).days) / 365.25
    detail = {
        "sold_price": chosen["price"],
        "sold_date": chosen["transaction_date"].isoformat(),
        "index_adjusted_to_valuation_date": round(chosen["price"] * adj.ratio),
        "market_movement_since_sale_pct": round(adj.pct_change, 1),
        "index_series": adj.describe(),
        "is_after_valuation_date": chosen["transaction_date"] > as_of,
    }
    return chosen["price"] * adj.ratio, years, detail


# --- the model --------------------------------------------------------------


async def value(
    target: TargetProperty,
    *,
    as_of: date,
    policy: ComparableSearchPolicy,
    source_refs: list,
    price_type: PriceType,
    symmetric_window: bool | None = None,
) -> ValuationResult:
    """Estimate `target`'s value at `as_of`, or explain why we cannot."""
    settings = get_settings()
    model_version = settings.avm_model_version
    today = date.today()
    is_historical = price_type is PriceType.HISTORICAL_ESTIMATE
    if symmetric_window is None:
        symmetric_window = is_historical

    # Pass 1: restrict to the same property type. Location and type are the two
    # strongest determinants of residential value, and mixing dwelling types is
    # the single biggest source of spurious spread in a comparable set.
    search_kwargs = {
        "country_iso2": target.country_iso2,
        "lat": target.latitude,
        "lon": target.longitude,
        "as_of": as_of,
        "target_type": target.property_type,
        "target_area": target.floor_area_sqm,
        "exclude_property_id": target.id,
        "symmetric_window": symmetric_window,
    }
    type_restricted = False
    raw: list[comps_mod.RawComparable] = []
    radius_used = policy.radius_steps_m[-1]

    if target.property_type and target.property_type != "other":
        same_type_policy = replace(policy, require_same_type=True)
        raw, radius_used = await comps_mod.search(
            policy=same_type_policy, **search_kwargs
        )
        type_restricted = True

    # Pass 2: if same-type evidence is too thin, widen to all types and let the
    # similarity floor below discard the genuinely incomparable ones.
    if len([c for c in raw if c.similarity >= MIN_SIMILARITY]) < policy.min_comparables:
        raw, radius_used = await comps_mod.search(policy=policy, **search_kwargs)
        type_restricted = False

    # Discard weak evidence outright rather than down-weighting it.
    raw = [c for c in raw if c.similarity >= MIN_SIMILARITY]

    # --- index-adjust every comparable to the valuation date ---------------
    adjusted: list[float] = []          # log adjusted price
    weights: list[float] = []
    kept: list[comps_mod.RawComparable] = []
    adjustment_levels: list[str] = []
    representative_adj = None

    for comp in raw:
        adj = await index_adjust.adjustment(
            country_iso2=target.country_iso2,
            district=comp.district or target.district,
            property_type=comp.property_type,
            from_date=comp.sold_date,
            to_date=as_of,
        )
        if adj is None:
            continue
        adjusted_price = comp.price * adj.ratio
        if adjusted_price <= 0:
            continue
        comp.index_adjusted_price = adjusted_price
        comp.adjustment_note = adj.describe()
        adjusted.append(math.log(adjusted_price))
        weights.append(max(comp.similarity, 1e-4))
        kept.append(comp)
        adjustment_levels.append(adj.area_level)
        if representative_adj is None or adj.area_level == "local_authority":
            representative_adj = representative_adj or adj

    effective = sum(weights)
    if len(kept) < MIN_COMPARABLES or effective < MIN_EFFECTIVE_WEIGHT:
        return ValuationResult(
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            message=(
                "There are too few comparable sales near this property to "
                "produce a reliable valuation."
            ),
            precision_level=PrecisionLevel.NONE,
            comparable_count=len(kept),
            method="Comparable-sales AVM",
            model_version=model_version,
            data_sources=source_refs,
            valuation_date=as_of,
            evidence={
                "comparables_found": len(kept),
                "minimum_required": MIN_COMPARABLES,
                "search_radius_m": radius_used,
            },
            computed_at=datetime.now(UTC),
        )

    # --- robust central estimate over log prices --------------------------
    centre_log = _weighted_quantile(adjusted, weights, 0.5)
    dispersion = _weighted_dispersion(adjusted, weights)
    comp_estimate = math.exp(centre_log)

    # --- blend in the dwelling's own prior sale ---------------------------
    own_value, own_years, own_detail = await _own_sale_evidence(
        target.id, as_of, target.country_iso2
    )
    own_weight = 0.0
    if own_value is not None and own_years is not None:
        own_weight = _MAX_OWN_WEIGHT * math.exp(-own_years / _OWN_DECAY_YEARS)
        # Do not let a single own sale outweigh a large, tight comparable set.
        own_weight = min(own_weight, 0.65 * effective / (effective + 4.0))
        estimate = math.exp(
            (1 - own_weight) * centre_log + own_weight * math.log(own_value)
        )
    else:
        estimate = comp_estimate

    # --- confidence and interval ------------------------------------------
    distances = sorted(c.distance_m for c in kept)
    median_distance = distances[len(distances) // 2]
    nearest_months = min(
        abs((c.sold_date.year - as_of.year) * 12 + (c.sold_date.month - as_of.month))
        for c in kept
    )
    dominant_level = (
        max(set(adjustment_levels), key=adjustment_levels.count)
        if adjustment_levels else "none"
    )

    conf, reasons = grade(
        ConfidenceInputs(
            comparable_count=len(kept),
            effective_count=effective,
            median_distance_m=median_distance,
            dispersion=dispersion,
            top_similarity=kept[0].similarity,
            months_to_nearest_evidence=nearest_months,
            adjustment_level=dominant_level,
            own_sale_years_ago=own_years,
            horizon_years=0.0,
        )
    )

    # A very low grade means we must not present a dwelling-level number (§22).
    if conf is Confidence.VERY_LOW:
        return ValuationResult(
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            message=(
                "The local evidence is too weak to value this property "
                "individually. Area-level statistics are shown instead."
            ),
            precision_level=PrecisionLevel.NONE,
            confidence=Confidence.VERY_LOW,
            comparable_count=len(kept),
            method="Comparable-sales AVM",
            model_version=model_version,
            data_sources=source_refs,
            valuation_date=as_of,
            evidence={"confidence_reasons": reasons, "search_radius_m": radius_used},
            computed_at=datetime.now(UTC),
        )

    # Interval: the wider of the calibrated band for this confidence grade and
    # the dispersion actually observed in the comparable set.
    #
    # The 0.73 multiplier on observed dispersion is calibrated, not chosen.
    # `ml/eval_avm.py` measures how often the published interval actually
    # contains the held-out sale price. At 0.90 the intervals covered 88.3% of
    # real outcomes against an advertised 80%, i.e. they were too wide to be
    # informative; 0.73 brings measured coverage to the advertised level. An
    # interval that over-covers is not "safe" — it is a different way of
    # failing to tell the user anything.
    half = max(INTERVAL_BY_CONFIDENCE[conf], dispersion * 0.73)
    if is_historical:
        # A back-cast inherits the index's own revision uncertainty, which grows
        # with distance into the past.
        half += min(0.06, 0.004 * abs(today.year - as_of.year))
    half = min(half, MAX_INTERVAL_HALF)

    low = estimate * math.exp(-half)
    high = estimate * math.exp(half)

    method = (
        "Comparable-sales AVM, index-adjusted"
        + (" (retrospective back-cast)" if is_historical else "")
    )

    # Explainability payload — every entry is a quantity the model used (§38).
    median_comparable = math.exp(_weighted_quantile(adjusted, weights, 0.5))
    evidence = {
        "comparable_count": len(kept),
        "effective_comparable_weight": round(effective, 2),
        "search_radius_m": radius_used,
        "median_comparable_distance_m": round(median_distance),
        "median_index_adjusted_comparable": round(median_comparable),
        "comparable_dispersion_log_mad": round(dispersion, 4),
        "comparable_price_range": [
            round(math.exp(_weighted_quantile(adjusted, weights, 0.25))),
            round(math.exp(_weighted_quantile(adjusted, weights, 0.75))),
        ],
        "comparables_restricted_to_same_type": type_restricted,
        "index_adjustment_level": dominant_level,
        "index_adjustment": representative_adj.describe() if representative_adj else None,
        "own_prior_sale": own_detail,
        "own_sale_weight": round(own_weight, 3) if own_weight else 0,
        "comparable_estimate_before_blend": round(comp_estimate),
        "confidence_reasons": reasons,
        "retrospective_window": bool(symmetric_window),
        "valuation_basis": (
            "price per dwelling"
            if not target.floor_area_sqm
            else "price per dwelling, size-weighted comparables"
        ),
    }

    return ValuationResult(
        status=DataStatus.OK,
        estimated_price=round(estimate),
        low_estimate=round(low),
        high_estimate=round(high),
        confidence=conf,
        precision_level=PrecisionLevel.PROPERTY_ESTIMATE,
        valuation_date=as_of,
        method=method,
        model_version=model_version,
        comparable_count=len(kept),
        data_sources=source_refs,
        evidence=evidence,
        computed_at=datetime.now(UTC),
    )


def to_comparable_models(
    raw: list[comps_mod.RawComparable], limit: int, currency: str
) -> list[Comparable]:
    out: list[Comparable] = []
    for comp in raw[:limit]:
        out.append(
            Comparable(
                transaction_id=comp.transaction_id,
                latitude=comp.latitude,
                longitude=comp.longitude,
                distance_km=round(comp.distance_m / 1000.0, 3),
                distance_miles=round(comp.distance_m / 1609.344, 2),
                sold_price=comp.price,
                currency=currency,
                sold_date=comp.sold_date,
                property_type=comp.property_type,
                floor_area_sqm=comp.floor_area_sqm,
                address_short=comp.address_short,
                similarity=round(min(max(comp.similarity, 0.0), 1.0), 3),
                index_adjusted_price=(
                    round(comp.index_adjusted_price)
                    if comp.index_adjusted_price else None
                ),
            )
        )
    return out
