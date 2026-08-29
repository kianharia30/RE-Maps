"""Index back-casting: a second, weaker route to a historical estimate (§12).

The comparable-sales AVM needs real sales *near the target date*. Transaction
coverage in any deployment is finite — this one holds a recent window — so for
an earlier year the comparable search legitimately comes up empty.

Rather than show nothing for every year before that window, we can still answer
honestly, because two real things are available for the whole HPI period
(1995 onwards):

  * a well-evidenced valuation of the dwelling *today*, and
  * the official local price index for the target month.

Back-casting divides the first by the second. That is a genuine, documented
valuation method with a real weakness: it assumes the dwelling tracked its
local market and was in comparable condition, so it cannot see a since-built
extension or a since-gutted interior. It is therefore

  * clearly labelled with its own method string,
  * graded at least one confidence step below the comparable-sales estimate,
  * given a wider interval that grows with how far back it reaches, and
  * only used when direct comparable evidence is genuinely unavailable.

It is never used for the present or the future.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime

from ..config import get_settings
from ..models.enums import Confidence, DataStatus, PrecisionLevel
from ..models.property import ValuationResult
from . import index_adjust

log = logging.getLogger(__name__)

# One confidence step down from the current estimate this is derived from.
_DEMOTE = {
    Confidence.HIGH: Confidence.MEDIUM,
    Confidence.MEDIUM: Confidence.LOW,
    Confidence.LOW: Confidence.VERY_LOW,
    Confidence.VERY_LOW: Confidence.VERY_LOW,
}

# Extra interval half-width (log space) per year reached back, on top of the
# current estimate's own band. Reflects drift between a dwelling and its index.
_WIDENING_PER_YEAR = 0.012
_MAX_EXTRA_WIDENING = 0.22


async def backcast(
    *,
    current: ValuationResult,
    as_of: date,
    country_iso2: str,
    district: str | None,
    property_type: str | None,
    source_refs: list,
) -> ValuationResult:
    """Restate a current valuation at a past date using the official index."""
    today = date.today()
    if as_of >= today.replace(day=1):
        return ValuationResult(
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            message="Back-casting only applies to past dates.",
            precision_level=PrecisionLevel.NONE,
        )
    if current.status is not DataStatus.OK or not current.estimated_price:
        return ValuationResult(
            status=current.status,
            message=current.message,
            precision_level=PrecisionLevel.NONE,
        )

    adj = await index_adjust.adjustment(
        country_iso2=country_iso2,
        district=district,
        property_type=property_type,
        from_date=today,
        to_date=as_of,
    )
    if adj is None:
        return ValuationResult(
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            message=(
                "No official price index covers this area for that year, so no "
                "historical value can be calculated."
            ),
            precision_level=PrecisionLevel.NONE,
            valuation_date=as_of,
        )

    estimate = current.estimated_price * adj.ratio
    years_back = max(0.0, (today - as_of).days / 365.25)

    # Start from the current estimate's own relative band, then widen.
    base_half = 0.12
    if current.low_estimate and current.high_estimate and current.estimated_price:
        base_half = (
            math.log(current.high_estimate / current.low_estimate) / 2.0
        )
    extra = min(_MAX_EXTRA_WIDENING, _WIDENING_PER_YEAR * years_back)
    if adj.area_level != "local_authority":
        extra += 0.04       # a coarser index tracks this dwelling less well
    half = base_half + extra

    confidence = _DEMOTE[current.confidence or Confidence.LOW]
    if adj.area_level == "country":
        confidence = _DEMOTE[confidence]
    if confidence is Confidence.VERY_LOW:
        return ValuationResult(
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            message=(
                f"There is not enough reliable evidence to value this property "
                f"in {as_of.year}."
            ),
            precision_level=PrecisionLevel.NONE,
            confidence=Confidence.VERY_LOW,
            valuation_date=as_of,
        )

    return ValuationResult(
        status=DataStatus.OK,
        estimated_price=round(estimate),
        low_estimate=round(estimate * math.exp(-half)),
        high_estimate=round(estimate * math.exp(half)),
        currency=current.currency,
        confidence=confidence,
        precision_level=PrecisionLevel.PROPERTY_ESTIMATE,
        valuation_date=as_of,
        method="Index back-cast from current valuation",
        model_version=get_settings().avm_model_version,
        comparable_count=current.comparable_count,
        data_sources=source_refs,
        evidence={
            "basis": (
                "No comparable sales are available near this date, so the "
                "current valuation was restated at that date using the "
                "official local price index."
            ),
            "current_estimate": round(current.estimated_price),
            "current_estimate_confidence": (
                current.confidence.value if current.confidence else None
            ),
            "current_estimate_comparables": current.comparable_count,
            "index_movement_pct": round(adj.pct_change, 1),
            "index_series": adj.describe(),
            "index_level": adj.area_level,
            "years_back": round(years_back, 1),
            "limitation": (
                "Assumes the property tracked its local market and was in "
                "comparable condition; it cannot account for later extensions "
                "or refurbishment."
            ),
        },
        computed_at=datetime.now(UTC),
    )
