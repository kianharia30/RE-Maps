"""The `Price` value object — §51.

Nothing in this application returns a bare number. Every monetary figure is
wrapped with what it *is*, where it came from, and how confident we are.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import Confidence, PrecisionLevel, PriceType


class SourceRef(BaseModel):
    """A citable reference to the dataset a figure came from."""

    key: str
    name: str
    owner: str
    url: str
    licence: str
    licence_url: str | None = None
    attribution: str
    source_published_at: date | None = None
    last_ingested_at: datetime | None = None


class Price(BaseModel):
    """A single monetary figure plus everything needed to interpret it."""

    value: float
    currency: str = Field(min_length=3, max_length=3)
    price_type: PriceType
    date: date
    lower_bound: float | None = None
    upper_bound: float | None = None
    confidence: Confidence | None = None
    precision_level: PrecisionLevel
    methodology: str
    sources: list[SourceRef] = Field(default_factory=list)
    source_record_id: str | None = None
    last_updated: datetime | None = None

    # Free-form, model-specific explainability payload (§38). Only ever
    # populated with quantities the model actually used.
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_observed(self) -> bool:
        """True only for genuine recorded transactions."""
        return self.price_type is PriceType.TRANSACTION


class Transaction(BaseModel):
    """A genuine recorded sale."""

    id: int
    date: date
    price: float
    currency: str
    property_type: str | None = None
    tenure: str | None = None
    new_build: bool | None = None
    floor_area_sqm: float | None = None
    price_per_sqm: float | None = None
    address: str | None = None
    postcode: str | None = None
    source_key: str
    source_record_id: str
