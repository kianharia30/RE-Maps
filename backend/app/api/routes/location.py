"""Market-level endpoints: area history and area forecast."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ...core import coverage as coverage_mod
from ...core.jurisdiction import resolve_point
from ...db import fetch_all
from ...forecast import service as forecast_service
from ...models.enums import DataStatus
from ...providers import registry
from ...providers.base import DateRange
from ...valuation import index_adjust

router = APIRouter(prefix="/api/location", tags=["location"])


@router.get("/market-history")
async def market_history(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    segment: str = Query("all"),
    from_year: int = Query(2000, ge=1900, le=2100),
) -> dict:
    """The real official index series for whatever market contains this point.

    Powers the 'how has this area moved?' view, and is the same series the
    forecaster is fitted to — so a user can see exactly what the projection
    extends.
    """
    jurisdiction = await resolve_point(lon, lat)
    if jurisdiction is None:
        return {
            "status": DataStatus.UNSUPPORTED_LOCATION.value,
            "message": "Property price data is not currently available for this location.",
        }
    provider = registry.for_country(jurisdiction.country_iso2)
    entry = await coverage_mod.lookup(jurisdiction.country_iso2, jurisdiction.region_code)
    if provider is None or not coverage_mod.is_usable(entry):
        return {
            "status": DataStatus.UNSUPPORTED_LOCATION.value,
            "message": (
                (entry.notes if entry and entry.notes else None)
                or "Property price data is not currently available for this location."
            ),
            "country_iso2": jurisdiction.country_iso2,
            "country_name": jurisdiction.country_name,
        }

    # Find the most local area whose recorded sales enclose this point.
    rows = await fetch_all(
        """
        SELECT a.area_level, a.area_code, a.area_name
        FROM area_stats a
        WHERE a.country_iso2 = %s AND a.bbox IS NOT NULL
          AND ST_Contains(a.bbox, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        ORDER BY CASE a.area_level
                    WHEN 'sector' THEN 1 WHEN 'outcode' THEN 2
                    WHEN 'district' THEN 3 WHEN 'county' THEN 4 ELSE 5 END
        LIMIT 1
        """,
        (jurisdiction.country_iso2, lon, lat),
    )
    district = rows[0]["area_name"] if rows else None

    link = await index_adjust.resolve_area_code(jurisdiction.country_iso2, district)
    area_code = link["area_code"] if link else None
    if not area_code:
        return {
            "status": DataStatus.NO_DATA.value,
            "message": "No official price index series covers this point.",
            "country_iso2": jurisdiction.country_iso2,
        }

    series = await provider.get_market_statistics(
        area_level="local_authority",
        area_code=area_code,
        date_range=DateRange(start=date(from_year, 1, 1), end=date.today()),
    )
    filtered = [s for s in series if s["segment"] == segment]
    return {
        "status": DataStatus.OK.value if filtered else DataStatus.NO_DATA.value,
        "country_iso2": jurisdiction.country_iso2,
        "area_code": area_code,
        "area_name": link["area_name"] if link else None,
        "segment": segment,
        "points": [
            {
                "period": s["period"].isoformat(),
                "average_price": s["average_price"],
                "index_value": s["index_value"],
                "sales_volume": s["sales_volume"],
                "pct_change_12m": s["pct_change_12m"],
            }
            for s in filtered
        ],
    }


@router.get("/forecast")
async def location_forecast(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    year: int = Query(..., ge=1900, le=2100),
    segment: str = Query("all"),
) -> dict:
    """The market forecast for the area containing this point, with diagnostics."""
    jurisdiction = await resolve_point(lon, lat)
    provider = registry.for_country(jurisdiction.country_iso2 if jurisdiction else None)
    entry = (
        await coverage_mod.lookup(jurisdiction.country_iso2, jurisdiction.region_code)
        if jurisdiction else None
    )
    if jurisdiction is None or provider is None or not coverage_mod.is_usable(entry):
        return {
            "status": DataStatus.UNSUPPORTED_LOCATION.value,
            "message": (
                (entry.notes if entry and entry.notes else None)
                or "Property price data is not currently available for this location."
            ),
        }
    if not provider.supports_forecast or not (entry and entry.forecast_supported):
        return {
            "status": DataStatus.OUT_OF_RANGE.value,
            "message": "Forecasts are not available for this jurisdiction.",
            "country_iso2": jurisdiction.country_iso2,
        }

    rows = await fetch_all(
        """
        SELECT a.area_name FROM area_stats a
        WHERE a.country_iso2 = %s AND a.area_level = 'district' AND a.bbox IS NOT NULL
          AND ST_Contains(a.bbox, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        LIMIT 1
        """,
        (jurisdiction.country_iso2, lon, lat),
    )
    district = rows[0]["area_name"] if rows else None

    fc = await forecast_service.forecast_market(
        country_iso2=jurisdiction.country_iso2,
        district=district, segment=segment, target=date(year, 6, 30),
    )
    if fc is None:
        return {
            "status": DataStatus.OUT_OF_RANGE.value,
            "message": (
                f"A {year} forecast is beyond what the available price index "
                "supports for this area."
            ),
        }
    return {
        "status": DataStatus.OK.value,
        "area_name": fc.area_name,
        "area_level": fc.area_level,
        "target_year": year,
        "market_movement_pct": round((fc.ratio - 1) * 100, 1),
        "movement_range_pct": [
            round((fc.ratio_low - 1) * 100, 1),
            round((fc.ratio_high - 1) * 100, 1),
        ],
        "confidence": fc.confidence.value,
        "methodology": fc.diagnostics,
    }
