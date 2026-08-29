"""Property endpoints: detail, history, comparables, forecast (§30)."""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Path, Query

from ...config import get_settings
from ...core.errors import NotFound, UnsupportedLocation
from ...db import fetch_one
from ...models.enums import DataStatus, PriceType
from ...models.price import Price
from ...models.property import Comparable, PriceHistory, PropertyDetail, ValuationResult
from ...providers import registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/property", tags=["property"])


async def _provider_for(property_id: int):
    row = await fetch_one(
        "SELECT country_iso2 FROM properties WHERE id = %s", (property_id,)
    )
    if not row:
        raise NotFound("No such property.")
    provider = registry.for_country(row["country_iso2"])
    if provider is None:
        raise UnsupportedLocation()
    return provider


@router.get("/{property_id}", response_model=PropertyDetail)
async def get_property(
    property_id: int = Path(..., ge=1),
    year: int | None = Query(None, ge=1900, le=2100),
) -> PropertyDetail:
    """Everything the side panel shows, for the selected year (§17)."""
    provider = await _provider_for(property_id)
    detail = await provider.get_property(property_id)
    if detail is None:
        raise NotFound("No such property.")

    selected_year = year or date.today().year
    detail.selected_year = selected_year

    price, status, message = await provider.price_for_year(property_id, selected_year)
    detail.selected_year_price = price
    detail.selected_year_status = status
    detail.selected_year_message = message

    # The current estimate is shown alongside the selected year so the user can
    # always see both. It is deliberately the AVM's CURRENT_ESTIMATE and never a
    # recorded sale: a house that happens to have sold this year would otherwise
    # show its sale price under the heading "estimated current value", which is
    # exactly the conflation of price types the product must avoid (§2, §57.4).
    today = date.today()
    valuation = await provider.value_property(property_id, as_of=today)
    if valuation.status is DataStatus.OK and valuation.estimated_price is not None:
        detail.current_estimate = Price(
            value=valuation.estimated_price,
            currency=valuation.currency or provider.currency_code,
            price_type=PriceType.CURRENT_ESTIMATE,
            date=valuation.valuation_date or today,
            lower_bound=valuation.low_estimate,
            upper_bound=valuation.high_estimate,
            confidence=valuation.confidence,
            precision_level=valuation.precision_level,
            methodology=valuation.method or "Comparable-sales AVM",
            sources=valuation.data_sources,
            last_updated=valuation.computed_at,
            evidence=valuation.evidence,
        )
    else:
        detail.current_estimate = None

    if detail.sources:
        detail.data_updated_at = max(
            (s.last_ingested_at for s in detail.sources if s.last_ingested_at),
            default=None,
        )
    return detail


@router.get("/{property_id}/history", response_model=PriceHistory)
async def get_history(
    property_id: int = Path(..., ge=1),
    from_year: int | None = Query(None, ge=1900, le=2100),
    to_year: int | None = Query(None, ge=1900, le=2100),
) -> PriceHistory:
    """The chart series (§18). Each point carries its own price type so the
    frontend can render observations, estimates and forecasts distinctly."""
    provider = await _provider_for(property_id)
    today = date.today()
    settings = get_settings()

    # Default window: from the earliest data we hold for this country, to the
    # far end of the forecast horizon.
    row = await fetch_one(
        """
        SELECT min(extract(year FROM transaction_date))::int AS min_year
        FROM transactions WHERE country_iso2 = (
            SELECT country_iso2 FROM properties WHERE id = %s)
        """,
        (property_id,),
    )
    default_from = max((row or {}).get("min_year") or 2010, today.year - 14)
    start = from_year or default_from
    end = to_year or (today.year + min(settings.forecast_max_horizon_years, 6))
    if end < start:
        start, end = end, start
    # Cap the span so one request cannot trigger 100 valuations.
    end = min(end, start + 40)

    return await provider.get_historical_prices(
        property_id, from_year=start, to_year=end
    )


@router.get("/{property_id}/comparables", response_model=list[Comparable])
async def get_comparables(
    property_id: int = Path(..., ge=1),
    year: int | None = Query(None, ge=1900, le=2100),
    limit: int = Query(12, ge=1, le=50),
) -> list[Comparable]:
    """The real nearby sales the valuation is built from (§19)."""
    provider = await _provider_for(property_id)
    today = date.today()
    target_year = year or today.year
    as_of = min(date(target_year, 12, 31), today)
    return await provider.get_comparables(property_id, as_of=as_of, limit=limit)


@router.get("/{property_id}/valuation", response_model=ValuationResult)
async def get_valuation(
    property_id: int = Path(..., ge=1),
    year: int | None = Query(None, ge=1900, le=2100),
) -> ValuationResult:
    """The structured valuation, including its explainability payload (§14, §38)."""
    provider = await _provider_for(property_id)
    today = date.today()
    target_year = year or today.year
    as_of = min(date(target_year, 12, 31), today)
    return await provider.value_property(property_id, as_of=as_of)


@router.get("/{property_id}/forecast")
async def get_forecast(
    property_id: int = Path(..., ge=1),
    year: int = Query(..., ge=1900, le=2100),
) -> dict:
    """A clearly-labelled statistical forecast, with its full methodology (§39)."""
    provider = await _provider_for(property_id)
    today = date.today()
    if year <= today.year:
        return {
            "status": DataStatus.OUT_OF_RANGE.value,
            "message": f"{year} is not a future year; use /history or /valuation.",
        }
    if not provider.supports_forecast:
        return {
            "status": DataStatus.OUT_OF_RANGE.value,
            "message": (
                "Forecasts are not available for this jurisdiction. See the "
                "coverage notes for why."
            ),
        }

    price, status, message = await provider.price_for_year(property_id, year)
    if price is None or status is not DataStatus.OK:
        return {"status": status.value, "message": message}
    return {
        "status": DataStatus.OK.value,
        "year": year,
        "forecast": price.model_dump(mode="json"),
    }
