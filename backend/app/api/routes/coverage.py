"""GET /api/coverage — what data genuinely exists, and where (§5, §21)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...config import get_settings
from ...core import coverage as coverage_mod
from ...core.jurisdiction import resolve_point
from ...db import fetch_all
from ...models.coverage import CoverageIndex, CoverageResponse
from ...models.price import SourceRef

router = APIRouter(prefix="/api", tags=["coverage"])


@router.get("/coverage", response_model=CoverageIndex)
async def coverage_index() -> CoverageIndex:
    """Everything RE-Maps supports. Used by the coverage panel."""
    return await coverage_mod.index()


@router.get("/coverage/at", response_model=CoverageResponse)
async def coverage_at(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> CoverageResponse:
    """Coverage for a point, including the year range the timeline should show."""
    settings = get_settings()
    jurisdiction = await resolve_point(lon, lat)
    return await coverage_mod.describe(
        jurisdiction.country_iso2 if jurisdiction else None,
        settings.forecast_max_horizon_years,
        region_code=jurisdiction.region_code if jurisdiction else None,
    )


@router.get("/sources", response_model=list[SourceRef])
async def sources() -> list[SourceRef]:
    """Every registered dataset with its licence and attribution (§44)."""
    rows = await fetch_all(
        """
        SELECT key, name, owner, url, licence, licence_url, attribution,
               source_published_at, last_ingested_at
        FROM data_sources ORDER BY name
        """
    )
    return [SourceRef(**r) for r in rows]
