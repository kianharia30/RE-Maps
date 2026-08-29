"""Coverage registry response shapes (§5)."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from .enums import CoordinatePrecision, DataStatus, PrecisionLevel
from .price import SourceRef


class CoverageEntry(BaseModel):
    provider_key: str
    country_iso2: str
    country_name: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    transaction_level_data: bool
    property_characteristics: bool
    market_index: bool
    forecast_supported: bool
    max_precision: PrecisionLevel
    coordinate_precision: CoordinatePrecision | None = None
    historical_from: date | None = None
    historical_to: date | None = None
    currency_code: str | None = None
    notes: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class CoverageResponse(BaseModel):
    status: DataStatus
    message: str | None = None
    country_iso2: str | None = None
    country_name: str | None = None
    entry: CoverageEntry | None = None
    # The years the timeline should offer for this jurisdiction.
    min_year: int | None = None
    max_data_year: int | None = None
    max_forecast_year: int | None = None
    current_year: int | None = None


class CoverageIndex(BaseModel):
    """Everything we support, for the /api/coverage landing view."""

    supported: list[CoverageEntry] = Field(default_factory=list)
    generated_at: str | None = None
