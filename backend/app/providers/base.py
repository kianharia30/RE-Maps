"""The `PropertyDataProvider` interface (§3, §25).

The frontend never knows which provider answered. A provider owns everything
jurisdiction-specific:

  * which datasets it may cite,
  * how precisely it can locate a dwelling,
  * whether an official price index exists to time-adjust with,
  * whether forecasting is defensible there,
  * what its currency is.

Ingested records for every country live in the same normalised tables, so a
provider is a *policy* object over that store rather than a separate database.
Adding a country therefore means: write an ingester, register the sources,
register coverage, and subclass this. No frontend change (§25).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date

from ..models.coverage import CoverageEntry
from ..models.enums import CoordinatePrecision, PrecisionLevel
from ..models.price import SourceRef, Transaction
from ..models.property import (
    Comparable,
    PriceHistory,
    PropertyCharacteristics,
    PropertyDetail,
    ValuationResult,
)


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must be <= end")


@dataclass(frozen=True, slots=True)
class ComparableSearchPolicy:
    """Jurisdiction-specific rules for what counts as usable evidence.

    These are not tuning knobs for making numbers look better — they encode how
    dense and how precisely located a country's data actually is. A country
    with parcel-level coordinates (France) can use a tighter radius than one
    with postcode centroids (UK).
    """

    radius_steps_m: tuple[int, ...]
    min_comparables: int
    target_comparables: int
    max_months_back: int
    prefer_same_type: bool = True
    require_same_type: bool = False


class PropertyDataProvider(abc.ABC):
    """Normalised access to one jurisdiction's property data."""

    key: str
    country_iso2: str
    currency_code: str
    source_keys: tuple[str, ...]
    coordinate_precision: CoordinatePrecision
    max_precision: PrecisionLevel
    supports_forecast: bool
    supports_characteristics: bool
    comparable_policy: ComparableSearchPolicy

    # --- identity -----------------------------------------------------------

    def supports(self, country_iso2: str, region_code: str | None = None) -> bool:
        """Whether this provider covers the given jurisdiction."""
        return country_iso2.upper() == self.country_iso2

    @abc.abstractmethod
    async def get_coverage(self, region_code: str | None = None) -> CoverageEntry | None:
        ...

    @abc.abstractmethod
    async def sources(self) -> list[SourceRef]:
        """Citable references for everything this provider can return."""

    # --- raw evidence -------------------------------------------------------

    @abc.abstractmethod
    async def get_transactions(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        date_range: DateRange,
        limit: int = 500,
        property_types: list[str] | None = None,
    ) -> list[Transaction]:
        """Genuine recorded sales inside a bounding box and date window."""

    @abc.abstractmethod
    async def get_property(self, property_id: int) -> PropertyDetail | None:
        ...

    @abc.abstractmethod
    async def get_property_characteristics(
        self, property_id: int
    ) -> PropertyCharacteristics | None:
        ...

    @abc.abstractmethod
    async def get_historical_prices(
        self, property_id: int, *, from_year: int, to_year: int
    ) -> PriceHistory:
        """Per-year series for one dwelling: observed sales, back-cast
        estimates and forecasts, each labelled with its own price type."""

    @abc.abstractmethod
    async def get_market_statistics(
        self, *, area_level: str, area_code: str, date_range: DateRange
    ) -> list[dict]:
        """Official index / aggregate series for an area."""

    # --- derived figures ----------------------------------------------------

    @abc.abstractmethod
    async def get_comparables(
        self, property_id: int, *, as_of: date, limit: int = 20
    ) -> list[Comparable]:
        ...

    @abc.abstractmethod
    async def value_property(
        self, property_id: int, *, as_of: date
    ) -> ValuationResult:
        """Estimate one dwelling's value at a date. MUST return an
        INSUFFICIENT_EVIDENCE status rather than a guess when the evidence is
        too thin (§22)."""

    # --- helper -------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.country_iso2}>"
