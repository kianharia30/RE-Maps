"""Index adjustment: restating a past price at another date (§12, §13).

A sale from March 2019 is not evidence of what a house is worth in 2026 unless
you move it through the local market. This module does that using the official
UK House Price Index, at the most local level available:

    local authority + property type
        -> local authority, all types
        -> English region / Wales, same type
        -> country (England / Wales)
        -> UK

Every adjustment reports which level it actually used, so the API can say so
and the confidence model can penalise coarse adjustments. There is deliberately
no synthetic fallback: if no series covers the dates, the caller gets None and
must degrade honestly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from ..db import fetch_all, fetch_one

log = logging.getLogger(__name__)

# Fallback chain of (area_level, label) tried in order.
_ALL_SEGMENT = "all"

# In-process cache: (area_code, segment) -> {period -> index_value}.
# The HPI is republished monthly; the API process is restarted on redeploy, and
# a stale month cannot change a historical adjustment materially.
_SERIES_CACHE: dict[tuple[str, str], dict[date, float]] = {}
_LINK_CACHE: dict[tuple[str, str], dict | None] = {}
_PARENT_CACHE: dict[str, list[dict]] = {}


@dataclass(frozen=True, slots=True)
class Adjustment:
    """The factor to multiply a price by, plus how it was derived."""

    ratio: float
    area_code: str
    area_name: str
    area_level: str
    segment: str
    from_period: date
    to_period: date
    # True when `to_period` had to be pulled back to the latest published month
    # because the requested date is beyond the index's coverage.
    to_period_clamped: bool
    index_from: float
    index_to: float

    @property
    def pct_change(self) -> float:
        return (self.ratio - 1.0) * 100.0

    def describe(self) -> str:
        level = {
            "local_authority": "local authority",
            "region": "regional",
            "country": "national",
        }.get(self.area_level, self.area_level)
        seg = "all property types" if self.segment == _ALL_SEGMENT else self.segment.replace("_", " ")
        return (
            f"UK HPI {level} series for {self.area_name} ({seg}): "
            f"{self.pct_change:+.1f}% between {self.from_period:%b %Y} and "
            f"{self.to_period:%b %Y}"
        )


def _month(d: date) -> date:
    return date(d.year, d.month, 1)


async def resolve_area_code(country_iso2: str, district: str | None) -> dict | None:
    """Map a transaction's district text onto an HPI local-authority code."""
    if not district:
        return None
    ck = (country_iso2, district)
    if ck in _LINK_CACHE:
        return _LINK_CACHE[ck]
    row = await fetch_one(
        """
        SELECT area_code, area_name, match_method, match_score
        FROM area_index_links WHERE country_iso2 = %s AND district = %s
        """,
        (country_iso2, district),
    )
    _LINK_CACHE[ck] = row
    return row


async def _series(area_code: str, segment: str) -> dict[date, float]:
    ck = (area_code, segment)
    cached = _SERIES_CACHE.get(ck)
    if cached is not None:
        return cached
    rows = await fetch_all(
        """
        SELECT period, index_value FROM market_indices
        WHERE area_code = %s AND segment = %s AND index_value IS NOT NULL
        ORDER BY period
        """,
        (area_code, segment),
    )
    series = {r["period"]: float(r["index_value"]) for r in rows}
    _SERIES_CACHE[ck] = series
    return series


async def _fallback_areas(area_code: str) -> list[dict]:
    """Progressively coarser areas to try after `area_code` itself.

    Derived from the GSS code namespace, which encodes the hierarchy:
    E06/E07/E08/E09 local authorities sit inside E12 regions inside E92
    England; W06 inside W92 Wales.
    """
    if area_code in _PARENT_CACHE:
        return _PARENT_CACHE[area_code]

    wanted: list[str]
    if area_code.startswith("W"):
        wanted = ["W92000004", "K02000001"]
    elif area_code.startswith("S"):
        wanted = ["S92000003", "K02000001"]
    elif area_code.startswith("N"):
        wanted = ["N92000002", "K02000001"]
    else:
        # England: we do not have a LA->region lookup in the HPI file itself,
        # so we go straight to England then the UK. Region-level series are
        # still used when the *search* area is itself a region.
        wanted = ["E92000001", "K02000001"]

    rows = await fetch_all(
        """
        SELECT DISTINCT area_code, area_name, area_level
        FROM market_indices WHERE area_code = ANY(%s)
        """,
        (wanted,),
    )
    ordered = sorted(rows, key=lambda r: wanted.index(r["area_code"]))
    _PARENT_CACHE[area_code] = ordered
    return ordered


def _nearest(series: dict[date, float], target: date) -> tuple[date, float] | None:
    """Exact month if present, else the closest published month within 6 months.

    Six months keeps an adjustment anchored to genuinely nearby market
    conditions; beyond that we would rather fail than fudge.
    """
    if not series:
        return None
    if target in series:
        return target, series[target]
    best: tuple[date, float] | None = None
    best_gap = 10**9
    for period, value in series.items():
        gap = abs((period.year - target.year) * 12 + (period.month - target.month))
        if gap < best_gap:
            best_gap, best = gap, (period, value)
    if best is None or best_gap > 6:
        return None
    return best


async def adjustment(
    *,
    country_iso2: str,
    district: str | None,
    property_type: str | None,
    from_date: date,
    to_date: date,
    area_code: str | None = None,
    area_name: str | None = None,
) -> Adjustment | None:
    """The factor that restates a `from_date` price at `to_date`.

    Returns None when no official series covers the pair of dates — the caller
    must then fall back to a coarser precision level or report insufficient
    evidence, never invent a number.
    """
    from_m, to_m = _month(from_date), _month(to_date)

    candidates: list[tuple[str, str, str]] = []   # (code, name, level)
    if area_code:
        candidates.append((area_code, area_name or area_code, "local_authority"))
    else:
        link = await resolve_area_code(country_iso2, district)
        if link:
            candidates.append((link["area_code"], link["area_name"], "local_authority"))

    seed = candidates[0][0] if candidates else "E92000001"
    for parent in await _fallback_areas(seed):
        candidates.append((parent["area_code"], parent["area_name"], parent["area_level"]))

    segments = []
    if property_type and property_type != "other":
        segments.append(property_type)
    segments.append(_ALL_SEGMENT)

    for code, name, level in candidates:
        for segment in segments:
            series = await _series(code, segment)
            if len(series) < 24:      # too short to trust
                continue
            a = _nearest(series, from_m)
            b = _nearest(series, to_m)
            if not a or not b or a[1] <= 0:
                continue
            return Adjustment(
                ratio=b[1] / a[1],
                area_code=code,
                area_name=name,
                area_level=level,
                segment=segment,
                from_period=a[0],
                to_period=b[0],
                to_period_clamped=b[0] != to_m,
                index_from=a[1],
                index_to=b[1],
            )
    return None


async def latest_period(country_iso2: str = "GB") -> date | None:
    """The most recent month the index has been published for."""
    row = await fetch_one(
        "SELECT max(period) AS p FROM market_indices WHERE country_iso2 = %s",
        (country_iso2,),
    )
    return row["p"] if row else None


def clear_caches() -> None:
    """Used by tests, and after re-ingesting the index."""
    _SERIES_CACHE.clear()
    _LINK_CACHE.clear()
    _PARENT_CACHE.clear()
