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
    """The whole registry, with absences kept separate from coverage.

    `supported` used to contain every registered row, including the ones that
    exist precisely to record that a place has NO data. A client reading
    `supported` would have concluded that Scotland was supported. The two are
    now distinct fields, because a registered absence is useful information but
    it is not coverage.
    """

    # Jurisdictions where we hold something real.
    supported: list[CoverageEntry]
    # Jurisdictions registered specifically to record that no open data exists
    # -- Scotland and Northern Ireland, which HM Land Registry does not cover.
    # Kept in the response because "we know there is nothing here, and why" is
    # a more useful answer than silence.
    known_absences: list[CoverageEntry] = []
    generated_at: str | None = None
