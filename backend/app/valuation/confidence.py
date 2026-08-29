"""Confidence grading (§22).

Deliberately rule-based and inspectable rather than a learned score, because
the grade's job is to tell a user how much to trust a number, and that
explanation has to hold up: every input below is a quantity we measured, and
each one has a documented effect.

The design bias is toward admitting uncertainty. VERY_LOW is not a label we
attach to a precise-looking number — it is the signal to stop showing a
dwelling-level figure at all and fall back to an area statistic (§22).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models.enums import Confidence


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    comparable_count: int
    effective_count: float          # sum of similarity weights
    median_distance_m: float
    dispersion: float               # weighted MAD of log adjusted prices
    top_similarity: float
    months_to_nearest_evidence: int
    adjustment_level: str           # local_authority | region | country | none
    own_sale_years_ago: float | None
    horizon_years: float            # 0 for present, >0 for forecast


def grade(inp: ConfidenceInputs) -> tuple[Confidence, list[str]]:
    """Return a grade plus the human-readable reasons behind it."""
    reasons: list[str] = []
    score = 0.0

    # --- volume of evidence ------------------------------------------------
    if inp.effective_count >= 12:
        score += 2.0
        reasons.append(f"{inp.comparable_count} nearby comparable sales")
    elif inp.effective_count >= 6:
        score += 1.2
        reasons.append(f"{inp.comparable_count} nearby comparable sales")
    elif inp.effective_count >= 3:
        score += 0.5
        reasons.append(f"only {inp.comparable_count} nearby comparable sales")
    else:
        score -= 1.0
        reasons.append(f"very few comparable sales ({inp.comparable_count})")

    # --- how local the evidence is -----------------------------------------
    if inp.median_distance_m <= 400:
        score += 1.5
        reasons.append("comparables are on or beside the same street")
    elif inp.median_distance_m <= 1000:
        score += 0.8
        reasons.append("comparables are within the immediate neighbourhood")
    elif inp.median_distance_m <= 2500:
        score += 0.1
    else:
        score -= 0.8
        reasons.append("comparables are spread over a wide area")

    # --- agreement between comparables -------------------------------------
    if inp.dispersion <= 0.14:
        score += 1.3
        reasons.append("comparable prices agree closely")
    elif inp.dispersion <= 0.24:
        score += 0.5
    elif inp.dispersion <= 0.38:
        score -= 0.4
        reasons.append("comparable prices vary considerably")
    else:
        score -= 1.3
        reasons.append("comparable prices vary widely")

    # --- quality of the individual best match ------------------------------
    if inp.top_similarity >= 0.8:
        score += 0.5
    elif inp.top_similarity < 0.5:
        score -= 0.5
        reasons.append("no closely-matching comparable found")

    # --- staleness ---------------------------------------------------------
    if inp.months_to_nearest_evidence > 36:
        score -= 0.9
        reasons.append("the nearest evidence is more than three years old")
    elif inp.months_to_nearest_evidence > 18:
        score -= 0.3

    # --- how local the index adjustment was --------------------------------
    if inp.adjustment_level == "local_authority":
        score += 0.6
    elif inp.adjustment_level == "region":
        score += 0.1
    elif inp.adjustment_level == "country":
        score -= 0.5
        reasons.append("only a national price index was available for this area")
    else:
        score -= 1.2
        reasons.append("no official price index could be applied")

    # --- the property's own sale history -----------------------------------
    if inp.own_sale_years_ago is not None:
        if inp.own_sale_years_ago <= 4:
            score += 1.4
            reasons.append(
                f"the property itself sold {inp.own_sale_years_ago:.0f} year(s) ago"
            )
        elif inp.own_sale_years_ago <= 10:
            score += 0.7
            reasons.append("the property has a recorded sale within the last decade")
        else:
            score += 0.2

    # --- forecast horizon --------------------------------------------------
    if inp.horizon_years > 0:
        score -= min(2.4, 0.42 * inp.horizon_years)
        reasons.append(f"forecast {inp.horizon_years:.0f} years ahead")

    if score >= 4.2:
        return Confidence.HIGH, reasons
    if score >= 2.2:
        return Confidence.MEDIUM, reasons
    if score >= 0.6:
        return Confidence.LOW, reasons
    return Confidence.VERY_LOW, reasons


# Interval half-width, in log space, applied at the estimate. Calibrated in
# ml/eval_avm.py against held-out real transactions and refreshed there; these
# are the measured values, not guesses (see docs/VALUATION.md).
INTERVAL_BY_CONFIDENCE: dict[Confidence, float] = {
    Confidence.HIGH: 0.075,
    Confidence.MEDIUM: 0.125,
    Confidence.LOW: 0.200,
    Confidence.VERY_LOW: 0.300,
}
