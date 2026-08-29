"""Normalised internal property model (§26).

Not every provider populates every field; anything a provider cannot supply
stays ``None`` rather than being guessed at.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from .enums import Confidence, CoordinatePrecision, DataStatus, PrecisionLevel
from .price import Price, SourceRef, Transaction


class PropertyAddress(BaseModel):
    line: str | None = None
    saon: str | None = None
    paon: str | None = None
    street: str | None = None
    locality: str | None = None
    town: str | None = None
    district: str | None = None
    county: str | None = None
    postcode: str | None = None
    country_iso2: str


class PropertyCharacteristics(BaseModel):
    property_type: str | None = None
    tenure: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    habitable_rooms: int | None = None
    floor_area_sqm: float | None = None
    lot_area_sqm: float | None = None
    year_built: int | None = None
    new_build_at_sale: bool | None = None
    source: str | None = None


class PropertySummary(BaseModel):
    """The compact shape returned for every marker on the map."""

    id: int
    latitude: float
    longitude: float
    coordinate_precision: CoordinatePrecision
    # True when the displayed point is a de-collided offset from a shared
    # centroid; the UI must not present it as a surveyed position.
    position_is_approximate: bool = False
    address_short: str | None = None
    postcode: str | None = None
    property_type: str | None = None
    price: Price


class PropertyDetail(BaseModel):
    """Everything the side panel needs (§17)."""

    id: int
    address: PropertyAddress
    latitude: float | None = None
    longitude: float | None = None
    coordinate_precision: CoordinatePrecision | None = None
    position_is_approximate: bool = False
    characteristics: PropertyCharacteristics

    # The figure for the year the user has selected on the timeline.
    selected_year: int
    selected_year_price: Price | None = None
    selected_year_status: DataStatus = DataStatus.OK
    selected_year_message: str | None = None

    current_estimate: Price | None = None
    last_transaction: Transaction | None = None
    transactions: list[Transaction] = Field(default_factory=list)

    sources: list[SourceRef] = Field(default_factory=list)
    data_updated_at: datetime | None = None


class PricePoint(BaseModel):
    """One point on the property price-history chart (§18).

    ``price_type`` is what lets the chart draw observed sales, back-cast
    estimates and forward forecasts as visually distinct series.
    """

    year: int
    date: date
    price: Price


class PriceHistory(BaseModel):
    property_id: int
    currency: str
    points: list[PricePoint] = Field(default_factory=list)
    status: DataStatus = DataStatus.OK
    message: str | None = None


class Comparable(BaseModel):
    """A nearby real transaction used as valuation evidence (§19)."""

    transaction_id: int
    latitude: float | None = None
    longitude: float | None = None
    distance_km: float
    distance_miles: float
    sold_price: float
    currency: str
    sold_date: date
    property_type: str | None = None
    floor_area_sqm: float | None = None
    bedrooms: int | None = None
    address_short: str | None = None
    similarity: float = Field(ge=0.0, le=1.0)
    # The comparable's price re-stated at the valuation date using the local
    # official index — this is the quantity the AVM actually averages.
    index_adjusted_price: float | None = None


class ValuationResult(BaseModel):
    """Structured valuation response (§14)."""

    status: DataStatus
    message: str | None = None
    estimated_price: float | None = None
    low_estimate: float | None = None
    high_estimate: float | None = None
    currency: str | None = None
    confidence: Confidence | None = None
    precision_level: PrecisionLevel = PrecisionLevel.NONE
    valuation_date: date | None = None
    method: str | None = None
    model_version: str | None = None
    comparable_count: int = 0
    data_sources: list[SourceRef] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    computed_at: datetime | None = None
