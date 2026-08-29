"""Comparable-sales search (§19).

Finds real, arm's-length residential transactions near a target dwelling and
scores how comparable each one is. This is the evidence base for every
estimated value in the application; if this returns too little, the AVM refuses
to publish a figure rather than stretching what it has.

Only transactions flagged ``market_value_basis = 'STANDARD'`` and
``is_residential`` are eligible — HM Land Registry's category B sales
(repossessions, non-market transfers) are real but are not evidence of market
value, so they are visible in the property panel and invisible here.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

from ..db import fetch_all
from ..providers.base import ComparableSearchPolicy

log = logging.getLogger(__name__)

# Similarity model.
#
# Property type is a MULTIPLICATIVE GATE, not one weighted term among several.
# That is a deliberate correction: with type as an additive component, a
# one-bed flat 100 m away scored 0.67 as evidence for a four-bed detached
# house, because near-perfect proximity and recency outvoted the type
# mismatch. Proximity cannot make a different kind of dwelling comparable —
# they are different products that happen to share a street.
#
#     similarity = type_affinity x weighted(distance, recency, size)
#
# So a type affinity of 0.12 (detached vs flat) caps similarity at 0.12,
# which is below the AVM's inclusion floor however close the sale was.
_W_DISTANCE = 0.58
_W_RECENCY = 0.25
_W_SIZE = 0.17

# Distance at which the distance score falls to 1/e.
_DISTANCE_DECAY_M = 900.0
# Months at which the recency score falls to 1/e.
_RECENCY_DECAY_MONTHS = 30.0

# How interchangeable two property types are as evidence for one another.
_TYPE_AFFINITY: dict[frozenset[str], float] = {
    frozenset({"semi_detached", "terraced"}): 0.62,
    frozenset({"semi_detached", "detached"}): 0.55,
    frozenset({"terraced", "flat"}): 0.30,
    frozenset({"detached", "terraced"}): 0.30,
    frozenset({"semi_detached", "flat"}): 0.22,
    frozenset({"detached", "flat"}): 0.12,
    # `house` is the unsubdivided house class (France). It is highly
    # interchangeable with any specific house type and poor evidence for flats.
    frozenset({"house", "detached"}): 0.80,
    frozenset({"house", "semi_detached"}): 0.80,
    frozenset({"house", "terraced"}): 0.78,
    frozenset({"house", "flat"}): 0.20,
}


@dataclass(slots=True)
class RawComparable:
    transaction_id: int
    property_id: int | None
    price: float
    sold_date: date
    property_type: str | None
    floor_area_sqm: float | None
    distance_m: float
    latitude: float | None
    longitude: float | None
    address_short: str | None
    postcode: str | None
    district: str | None
    similarity: float = 0.0
    index_adjusted_price: float | None = None
    adjustment_note: str | None = None


# `&&` against an expanded envelope uses the GIST index; ST_DistanceSphere then
# trims to a true metric radius. Doing it the other way round (a geography
# ST_DWithin) would not hit the geometry index.
_SEARCH_SQL = """
WITH target AS (
    SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS g
)
SELECT t.id            AS transaction_id,
       t.property_id,
       t.price::float8 AS price,
       t.transaction_date AS sold_date,
       t.property_type,
       t.floor_area_sqm::float8 AS floor_area_sqm,
       ST_DistanceSphere(t.geom, target.g) AS distance_m,
       ST_Y(t.geom) AS latitude,
       ST_X(t.geom) AS longitude,
       p.address_line AS address_short,
       t.postcode_norm AS postcode,
       t.district
FROM transactions t
CROSS JOIN target
LEFT JOIN properties p ON p.id = t.property_id
WHERE t.country_iso2 = %(country)s
  AND t.market_value_basis = 'STANDARD'
  AND t.is_residential
  AND t.geom IS NOT NULL
  AND t.geom && ST_Expand(target.g, %(deg)s)
  AND ST_DistanceSphere(t.geom, target.g) <= %(radius_m)s
  AND t.transaction_date BETWEEN %(date_from)s AND %(date_to)s
  AND (%(exclude_property_id)s::bigint IS NULL OR t.property_id IS DISTINCT FROM %(exclude_property_id)s::bigint)
  AND (%(types)s::text[] IS NULL OR t.property_type = ANY(%(types)s::text[]))
ORDER BY ST_DistanceSphere(t.geom, target.g)
LIMIT %(limit)s
"""


def _months_between(a: date, b: date) -> int:
    return abs((a.year - b.year) * 12 + (a.month - b.month))


def _type_score(target_type: str | None, comp_type: str | None) -> float:
    if not target_type or not comp_type:
        return 0.45          # unknown on either side: mildly penalised
    if target_type == comp_type:
        return 1.0
    return _TYPE_AFFINITY.get(frozenset({target_type, comp_type}), 0.18)


def _size_score(target_area: float | None, comp_area: float | None) -> float | None:
    """Log-ratio similarity of floor areas, or None when unavailable.

    Returning None (rather than a neutral value) lets the caller renormalise
    the weights so a missing feature does not silently drag scores down.
    """
    if not target_area or not comp_area or target_area <= 0 or comp_area <= 0:
        return None
    return math.exp(-2.0 * abs(math.log(comp_area / target_area)))


def score(
    comp: RawComparable,
    *,
    target_type: str | None,
    target_area: float | None,
    as_of: date,
) -> float:
    """How comparable this sale is to the target, in [0, 1]."""
    distance = math.exp(-comp.distance_m / _DISTANCE_DECAY_M)
    recency = math.exp(-_months_between(comp.sold_date, as_of) / _RECENCY_DECAY_MONTHS)
    size = _size_score(target_area, comp.floor_area_sqm)

    parts = [(_W_DISTANCE, distance), (_W_RECENCY, recency)]
    if size is not None:
        parts.append((_W_SIZE, size))
    total_w = sum(w for w, _ in parts)
    proximity = sum(w * v for w, v in parts) / total_w

    # The gate. See the note on the weights above.
    return _type_score(target_type, comp.property_type) * proximity


async def search(
    *,
    country_iso2: str,
    lat: float,
    lon: float,
    as_of: date,
    policy: ComparableSearchPolicy,
    target_type: str | None = None,
    target_area: float | None = None,
    exclude_property_id: int | None = None,
    symmetric_window: bool = False,
    hard_limit: int = 160,
) -> tuple[list[RawComparable], int]:
    """Expanding-radius comparable search.

    Returns (comparables sorted by similarity, radius in metres actually used).

    ``symmetric_window`` is used for HISTORICAL estimates: evidence is drawn
    from both sides of the target date, which is legitimate for a retrospective
    back-cast and is labelled as such in the response (§12, §40). Forecasts
    never use it.
    """
    months = policy.max_months_back
    if symmetric_window:
        date_from = date(as_of.year - months // 12, as_of.month, 1)
        # Cap the forward edge at today: we cannot use sales that have not
        # happened yet.
        forward_year = as_of.year + months // 12
        date_to = min(date(forward_year, as_of.month, 28), date.today())
    else:
        date_from = date(as_of.year - months // 12, as_of.month, 1)
        date_to = min(as_of, date.today())

    types = None
    if policy.require_same_type and target_type:
        types = [target_type]

    chosen: list[RawComparable] = []
    radius_used = policy.radius_steps_m[-1]

    for radius in policy.radius_steps_m:
        rows = await fetch_all(
            _SEARCH_SQL,
            {
                "country": country_iso2,
                "lat": lat,
                "lon": lon,
                # Over-expand in degrees (safe superset), then trim exactly.
                "deg": radius / 111_320.0 * 1.6,
                "radius_m": radius,
                "date_from": date_from,
                "date_to": date_to,
                "exclude_property_id": exclude_property_id,
                "types": types,
                "limit": hard_limit,
            },
        )
        chosen = [RawComparable(**row) for row in rows]
        radius_used = radius
        if len(chosen) >= policy.target_comparables:
            break

    for comp in chosen:
        comp.similarity = score(
            comp, target_type=target_type, target_area=target_area, as_of=as_of
        )

    chosen.sort(key=lambda c: c.similarity, reverse=True)
    return chosen, radius_used
