"""GET /api/search — global location search (§10, §46).

Search works everywhere on Earth. Coverage is a separate question, answered
alongside each result so the UI can move the map to Tokyo *and* tell the user
straight away that no property data exists there.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AfterValidator

from ...core import coverage as coverage_mod
from ...geocode.nominatim import get_geocoder
from ...models.geo import SearchResult

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["search"])


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
    geocoder = get_geocoder()
    results = await geocoder.search(q, limit=limit)

    supported = await coverage_mod.supported_countries()
    for result in results:
        iso2 = (result.country_iso2 or "").upper()
        result.has_property_data = iso2 in supported
        if not result.has_property_data:
            result.coverage_note = (
                "Property price data is not currently available for this location."
            )
        else:
            entry = await coverage_mod.lookup(iso2)
            if entry and entry.region_name:
                result.coverage_note = f"Coverage: {entry.region_name}"
    return results


@router.get("/reverse", response_model=SearchResult | None)
async def reverse(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> SearchResult | None:
    geocoder = get_geocoder()
    result = await geocoder.reverse(lat, lon)
    if result:
        supported = await coverage_mod.supported_countries()
        result.has_property_data = (result.country_iso2 or "").upper() in supported
    return result
