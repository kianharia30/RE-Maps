"""Geographic request/response shapes."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .enums import Confidence, DataStatus, MapTier, PrecisionLevel


class BoundingBox(BaseModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _check(self) -> BoundingBox:
        if self.south > self.north:
            raise ValueError("south must be <= north")
        if self.west == self.east:
            raise ValueError("west and east must differ")
        return self

    @property
    def crosses_antimeridian(self) -> bool:
        return self.west > self.east

    @property
    def centre(self) -> tuple[float, float]:
        lat = (self.south + self.north) / 2
        if self.crosses_antimeridian:
            span = (180 - self.west) + (self.east + 180)
            lon = self.west + span / 2
            if lon > 180:
                lon -= 360
        else:
            lon = (self.west + self.east) / 2
        return lat, lon

    def clamped(self) -> BoundingBox:
        """Clamp latitudes into the Web-Mercator-safe range."""
        return BoundingBox(
            west=self.west,
            east=self.east,
            south=max(self.south, -85.05),
            north=min(self.north, 85.05),
        )

    @classmethod
    def parse(cls, raw: str) -> BoundingBox:
        parts = raw.split(",")
        if len(parts) != 4:
            raise ValueError("bbox must be 'west,south,east,north'")
        try:
            w, s, e, n = (float(p) for p in parts)
        except ValueError as exc:  # pragma: no cover - message clarity
            raise ValueError("bbox values must be numbers") from exc
        return cls(west=w, south=s, east=e, north=n)


class SearchResult(BaseModel):
    """A geocoded place. Search works globally even with no price data (§46)."""

    id: str
    display_name: str
    name: str
    latitude: float
    longitude: float
    kind: str                       # city | postcode | street | address | ...
    country_iso2: str | None = None
    country_name: str | None = None
    bbox: BoundingBox | None = None
    suggested_zoom: float
    # Populated from the coverage registry so the UI can grey out or warn
    # before the user even moves the map.
    has_property_data: bool = False
    coverage_note: str | None = None


class AreaStat(BaseModel):
    """An aggregated area figure shown at low/medium zoom."""

    area_level: str
    area_code: str
    area_name: str
    latitude: float
    longitude: float
    year: int
    median_price: float
    p25_price: float | None = None
    p75_price: float | None = None
    median_price_per_sqm: float | None = None
    transaction_count: int
    currency: str
    precision_level: PrecisionLevel
    # The span of sales the median was computed over. Equal to `year` for the
    # precomputed tiers; a multi-year window for the live street/postcode tiers,
    # which are too sparse to support a single-year median.
    window_from_year: int | None = None
    window_to_year: int | None = None
    # Year-on-year change computed from the same real order statistics.
    growth_1y_pct: float | None = None
    is_forecast: bool = False
    confidence: Confidence | None = None
    # For a forecast, the market whose index was projected — so a user can see
    # that the growth rate is local and which area it came from.
    forecast_market: str | None = None


class MapResponse(BaseModel):
    status: DataStatus
    message: str | None = None
    tier: MapTier
    year: int
    is_future: bool = False
    is_historical: bool = False
    currency: str | None = None
    # Exactly one of these is populated per tier.
    areas: list[AreaStat] = Field(default_factory=list)
    properties: list = Field(default_factory=list)
    truncated: bool = False
    total_available: int | None = None
    attributions: list[str] = Field(default_factory=list)
