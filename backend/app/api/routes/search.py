"""GET /api/search — global location search (§10, §46).

Search works everywhere on Earth. Coverage is a separate question, answered
alongside each result so the UI can move the map to Tokyo *and* tell the user
straight away that no property data exists there.
"""
from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AfterValidator

from ...core import coverage as coverage_mod
from ...core.jurisdiction import resolve_point
from ...db import fetch_one
from ...geocode.nominatim import get_geocoder
from ...models.geo import BoundingBox, SearchResult

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["search"])


# ---------------------------------------------------------------------------
# UK postcodes are resolved from our own gazetteer, not the general geocoder.
#
# This is a correctness fix, not an optimisation. Nominatim's free-text search
# for "MK9 2AB" returned a place at latitude -9.47 -- it had matched something
# entirely unrelated in the southern hemisphere. We already hold 2.6M
# authoritative postcode centroids (Ordnance Survey / ONS derived), so a
# postcode-shaped query should be answered from them: it is exact, instant, and
# costs the rate-limited geocoder nothing.
#
# Full unit  e.g. "MK9 2AB"   -> that postcode's centroid
# Outcode    e.g. "MK9"       -> the centroid of the outcode's live postcodes
# ---------------------------------------------------------------------------
_UK_POSTCODE_FULL = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$", re.I)
_UK_OUTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$", re.I)

_POSTCODE_SQL = """
SELECT postcode_pretty AS name, ST_Y(geom) AS lat, ST_X(geom) AS lon,
       admin_district, uk_country, precision::text AS precision
FROM postcodes
WHERE country_iso2 = 'GB' AND postcode = %s
ORDER BY (status = 'live') DESC
LIMIT 1
"""

# An outcode has no single official point, so we take the centroid of its live
# member postcodes and a bounding box over their real extent.
_OUTCODE_SQL = """
SELECT outcode AS name,
       ST_Y(ST_Centroid(ST_Collect(geom))) AS lat,
       ST_X(ST_Centroid(ST_Collect(geom))) AS lon,
       ST_XMin(ST_Extent(geom)) AS west, ST_YMin(ST_Extent(geom)) AS south,
       ST_XMax(ST_Extent(geom)) AS east, ST_YMax(ST_Extent(geom)) AS north,
       count(*) AS n,
       mode() WITHIN GROUP (ORDER BY uk_country) AS uk_country
FROM postcodes
WHERE country_iso2 = 'GB' AND outcode = %s AND status = 'live'
GROUP BY outcode
"""


def _looks_like_uk_postcode(query: str) -> bool:
    cleaned = query.strip().upper()
    return bool(_UK_POSTCODE_FULL.match(cleaned) or _UK_OUTCODE.match(cleaned))


async def _uk_postcode_result(query: str) -> SearchResult | None:
    """Resolve a UK postcode or outcode from the gazetteer, or None."""
    cleaned = query.strip().upper()

    full = _UK_POSTCODE_FULL.match(cleaned)
    if full:
        normalised = (full.group(1) + full.group(2)).replace(" ", "")
        row = await fetch_one(_POSTCODE_SQL, (normalised,))
        if row:
            region = row["admin_district"] or row["uk_country"] or "United Kingdom"
            return SearchResult(
                id=f"postcode/{normalised}",
                display_name=f"{row['name']}, {region}, United Kingdom",
                name=row["name"],
                latitude=row["lat"],
                longitude=row["lon"],
                kind="postcode",
                country_iso2="GB",
                country_name="United Kingdom",
                suggested_zoom=16.5,
            )
        return None

    if _UK_OUTCODE.match(cleaned):
        row = await fetch_one(_OUTCODE_SQL, (cleaned,))
        if row and row["n"]:
            bbox = None
            if row["west"] != row["east"] and row["south"] != row["north"]:
                bbox = BoundingBox(
                    west=row["west"], south=row["south"],
                    east=row["east"], north=row["north"],
                ).clamped()
            return SearchResult(
                id=f"outcode/{cleaned}",
                display_name=(
                    f"{cleaned} postcode district "
                    f"({row['n']:,} postcodes), {row['uk_country']}, United Kingdom"
                ),
                name=cleaned,
                latitude=row["lat"],
                longitude=row["lon"],
                kind="postcode",
                country_iso2="GB",
                country_name="United Kingdom",
                bbox=bbox,
                suggested_zoom=13.0,
            )
    return None


def _non_blank(value: str) -> str:
    """`min_length=1` alone lets " " through, which then normalises to an empty
    query and quietly returns no results. Rejecting it keeps the contract
    honest: a blank query is a client error, not an empty answer."""
    if not value.strip():
        raise ValueError("query must contain at least one non-whitespace character")
    return value


# Query() must sit INSIDE Annotated alongside the validator. With
# `q: SearchQuery = Query(...)` FastAPI treats the default as the whole
# parameter spec and the AfterValidator is silently ignored.
SearchQuery = Annotated[
    str,
    Query(min_length=1, max_length=200, description="place name, postcode or address"),
    AfterValidator(_non_blank),
]


@router.get("/search", response_model=list[SearchResult])
async def search(
    q: SearchQuery,
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> list[SearchResult]:
    # A UK postcode is answered authoritatively from our own gazetteer, and the
    # geocoder is still consulted for anything else the query might mean.
    results: list[SearchResult] = []
    postcode = await _uk_postcode_result(q)
    if postcode:
        results.append(postcode)

    if len(results) < limit:
        geocoder = get_geocoder()
        try:
            geocoded = await geocoder.search(q, limit=limit - len(results))
        except Exception:
            if not results:
                raise
            geocoded = []      # a gazetteer hit is still a useful answer

        # A query shaped like a UK postcode must not be answered with an
        # unrelated foreign place. Searching the (non-existent) "MK9 2AB"
        # returned a street in Brazil from the general geocoder; for a
        # postcode-shaped query we would rather return nothing and let the UI
        # say "no matching places found".
        if _looks_like_uk_postcode(q):
            geocoded = [r for r in geocoded if (r.country_iso2 or "").upper() == "GB"]

        seen = {(round(r.latitude, 4), round(r.longitude, 4)) for r in results}
        for result in geocoded:
            key = (round(result.latitude, 4), round(result.longitude, 4))
            if key not in seen:
                seen.add(key)
                results.append(result)

    await _annotate_coverage(results)
    return results


async def _annotate_coverage(results: list[SearchResult]) -> None:
    """Flag each result with whether property data genuinely exists there.

    Resolved per RESULT COORDINATE, not per country, because coverage is not
    uniform within a country: "EH1 1AA" is in the United Kingdom but Scotland
    has no open transaction data, and a country-level flag reported it as
    covered.
    """
    for result in results:
        jurisdiction = await resolve_point(result.longitude, result.latitude)
        if jurisdiction is None:
            result.has_property_data = False
            result.coverage_note = (
                "Property price data is not currently available for this location."
            )
            continue

        entry = await coverage_mod.lookup(
            jurisdiction.country_iso2, jurisdiction.region_code
        )
        result.has_property_data = coverage_mod.is_usable(entry)
        if result.has_property_data:
            result.coverage_note = (
                f"Coverage: {entry.region_name}" if entry and entry.region_name else None
            )
        else:
            result.coverage_note = (
                (entry.notes if entry and entry.notes else None)
                or "Property price data is not currently available for this location."
            )


@router.get("/reverse", response_model=SearchResult | None)
async def reverse(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> SearchResult | None:
    geocoder = get_geocoder()
    result = await geocoder.reverse(lat, lon)
    if result:
        await _annotate_coverage([result])
    return result
