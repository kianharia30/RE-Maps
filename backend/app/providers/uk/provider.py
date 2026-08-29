"""UKPropertyProvider — England & Wales, from HM Land Registry open data.

Coverage and its honest limits
------------------------------
* Transactions: HM Land Registry Price Paid Data. **England & Wales only.**
  Scotland (Registers of Scotland) and Northern Ireland (Land & Property
  Services) do not publish equivalent open transaction-level data, so a search
  in Edinburgh or Belfast correctly reports no property data.
* Coordinates: postcode centroids, because building-level coordinates for
  England & Wales require OS AddressBase, which is licensed. Recorded as
  ``coordinate_precision = POSTCODE`` and surfaced in the UI — a marker is
  positioned in the right postcode, not on the right roof.
* Characteristics: property type and tenure come with every transaction.
  Floor area, bedrooms and habitable rooms require the EPC dataset, which needs
  a free API key; without it those fields are simply absent.
* Index: UK House Price Index, per local authority and per property type.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from ...core.currency import currency_for_country
from ...core.errors import NotFound
from ...db import fetch_all, fetch_one
from ...forecast import service as forecast_service
from ...models.coverage import CoverageEntry
from ...models.enums import (
    Confidence,
    CoordinatePrecision,
    DataStatus,
    PrecisionLevel,
    PriceType,
)
from ...models.price import Price, SourceRef, Transaction
from ...models.property import (
    Comparable,
    PriceHistory,
    PricePoint,
    PropertyAddress,
    PropertyCharacteristics,
    PropertyDetail,
    ValuationResult,
)
from ...valuation import avm, backcast, index_adjust
from ...valuation import comparables as comps_mod
from ..base import ComparableSearchPolicy, DateRange, PropertyDataProvider

log = logging.getLogger(__name__)


class UKPropertyProvider(PropertyDataProvider):
    key = "uk_land_registry"
    country_iso2 = "GB"
    currency_code = "GBP"
    source_keys = (
        "uk_land_registry_ppd",
        "uk_hpi",
        "uk_open_postcode_geo",
    )
    coordinate_precision = CoordinatePrecision.POSTCODE
    max_precision = PrecisionLevel.EXACT_TRANSACTION
    supports_forecast = True
    supports_characteristics = False    # flipped on when EPC is configured

    # Postcode-centroid coordinates mean a 250 m radius would be spuriously
    # precise, so the first ring is wider than it would be for parcel-level data.
    comparable_policy = ComparableSearchPolicy(
        radius_steps_m=(400, 800, 1600, 3200, 6400),
        min_comparables=3,
        target_comparables=15,
        max_months_back=60,
        prefer_same_type=True,
        require_same_type=False,
    )

    def __init__(self) -> None:
        from ...config import get_settings

        self.supports_characteristics = get_settings().epc_enabled
        if self.supports_characteristics:
            self.source_keys = (*self.source_keys, "uk_epc")

    # --- coverage / provenance ---------------------------------------------

    async def sources(self) -> list[SourceRef]:
        rows = await fetch_all(
            """
            SELECT key, name, owner, url, licence, licence_url, attribution,
                   source_published_at, last_ingested_at
            FROM data_sources WHERE key = ANY(%s) ORDER BY key
            """,
            (list(self.source_keys),),
        )
        return [SourceRef(**r) for r in rows]

    async def get_coverage(self, region_code: str | None = None) -> CoverageEntry | None:
        from ...core import coverage

        return await coverage.lookup(self.country_iso2)

    # --- raw evidence -------------------------------------------------------

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
        rows = await fetch_all(
            """
            SELECT t.id, t.transaction_date AS date, t.price::float8 AS price,
                   t.currency_code AS currency, t.property_type, t.tenure,
                   t.new_build, t.floor_area_sqm::float8 AS floor_area_sqm,
                   t.price_per_sqm::float8 AS price_per_sqm,
                   p.address_line AS address, t.postcode_norm AS postcode,
                   t.source_key, t.source_record_id
            FROM transactions t
            LEFT JOIN properties p ON p.id = t.property_id
            WHERE t.country_iso2 = %(country)s
              AND t.geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
              AND t.transaction_date BETWEEN %(from)s AND %(to)s
              AND t.is_residential
              AND (%(types)s::text[] IS NULL OR t.property_type = ANY(%(types)s::text[]))
            ORDER BY t.transaction_date DESC
            LIMIT %(limit)s
            """,
            {
                "country": self.country_iso2,
                "w": west, "s": south, "e": east, "n": north,
                "from": date_range.start, "to": date_range.end,
                "types": property_types, "limit": limit,
            },
        )
        return [Transaction(**r) for r in rows]

    async def get_property(self, property_id: int) -> PropertyDetail | None:
        row = await fetch_one(
            """
            SELECT p.*, ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon
            FROM properties p
            WHERE p.id = %s AND p.country_iso2 = %s
            """,
            (property_id, self.country_iso2),
        )
        if not row:
            return None

        txns = await self._property_transactions(property_id)
        return PropertyDetail(
            id=row["id"],
            address=PropertyAddress(
                line=row["address_line"], saon=row["saon"], paon=row["paon"],
                street=row["street"], locality=row["locality"], town=row["town"],
                district=row["district"], county=row["county"],
                postcode=row["postcode"], country_iso2=self.country_iso2,
            ),
            latitude=row["lat"], longitude=row["lon"],
            coordinate_precision=(
                CoordinatePrecision(row["coordinate_precision"])
                if row["coordinate_precision"] else None
            ),
            position_is_approximate=row["coordinate_precision"] in
                ("POSTCODE", "NEIGHBOURHOOD", "REGION"),
            characteristics=PropertyCharacteristics(
                property_type=row["property_type"], tenure=row["tenure"],
                bedrooms=row["bedrooms"], bathrooms=row["bathrooms"],
                habitable_rooms=row["habitable_rooms"],
                floor_area_sqm=(
                    float(row["floor_area_sqm"]) if row["floor_area_sqm"] else None
                ),
                lot_area_sqm=(
                    float(row["lot_area_sqm"]) if row["lot_area_sqm"] else None
                ),
                year_built=row["year_built"],
                new_build_at_sale=row["new_build_at_sale"],
                source=row["characteristics_source"],
            ),
            selected_year=date.today().year,
            transactions=txns,
            last_transaction=txns[0] if txns else None,
            sources=await self.sources(),
        )

    async def _property_transactions(self, property_id: int) -> list[Transaction]:
        rows = await fetch_all(
            """
            SELECT t.id, t.transaction_date AS date, t.price::float8 AS price,
                   t.currency_code AS currency, t.property_type, t.tenure,
                   t.new_build, t.floor_area_sqm::float8 AS floor_area_sqm,
                   t.price_per_sqm::float8 AS price_per_sqm,
                   p.address_line AS address, t.postcode_norm AS postcode,
                   t.source_key, t.source_record_id
            FROM transactions t
            LEFT JOIN properties p ON p.id = t.property_id
            WHERE t.property_id = %s
            ORDER BY t.transaction_date DESC
            """,
            (property_id,),
        )
        return [Transaction(**r) for r in rows]

    async def get_property_characteristics(
        self, property_id: int
    ) -> PropertyCharacteristics | None:
        row = await fetch_one(
            """
            SELECT property_type, tenure, bedrooms, bathrooms, habitable_rooms,
                   floor_area_sqm, lot_area_sqm, year_built, new_build_at_sale,
                   characteristics_source
            FROM properties WHERE id = %s AND country_iso2 = %s
            """,
            (property_id, self.country_iso2),
        )
        if not row:
            return None
        return PropertyCharacteristics(
            property_type=row["property_type"], tenure=row["tenure"],
            bedrooms=row["bedrooms"], bathrooms=row["bathrooms"],
            habitable_rooms=row["habitable_rooms"],
            floor_area_sqm=float(row["floor_area_sqm"]) if row["floor_area_sqm"] else None,
            lot_area_sqm=float(row["lot_area_sqm"]) if row["lot_area_sqm"] else None,
            year_built=row["year_built"],
            new_build_at_sale=row["new_build_at_sale"],
            source=row["characteristics_source"],
        )

    async def get_market_statistics(
        self, *, area_level: str, area_code: str, date_range: DateRange
    ) -> list[dict]:
        rows = await fetch_all(
            """
            SELECT period, segment, average_price::float8 AS average_price,
                   index_value::float8 AS index_value, sales_volume,
                   pct_change_12m::float8 AS pct_change_12m, area_name, area_level
            FROM market_indices
            WHERE area_code = %s AND period BETWEEN %s AND %s
            ORDER BY period, segment
            """,
            (area_code, date_range.start, date_range.end),
        )
        return rows

    # --- derived figures ----------------------------------------------------

    async def _target(self, property_id: int) -> avm.TargetProperty:
        row = await fetch_one(
            """
            SELECT p.id, ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon,
                   p.property_type, p.floor_area_sqm::float8 AS floor_area_sqm,
                   p.district, p.postcode_norm, p.address_line
            FROM properties p WHERE p.id = %s AND p.country_iso2 = %s
            """,
            (property_id, self.country_iso2),
        )
        if not row or row["lat"] is None:
            raise NotFound("That property does not exist, or has no location.")
        return avm.TargetProperty(
            id=row["id"], country_iso2=self.country_iso2,
            latitude=row["lat"], longitude=row["lon"],
            property_type=row["property_type"],
            floor_area_sqm=row["floor_area_sqm"],
            district=row["district"], postcode_norm=row["postcode_norm"],
            address_line=row["address_line"],
        )

    async def get_comparables(
        self, property_id: int, *, as_of: date, limit: int = 20
    ) -> list[Comparable]:
        target = await self._target(property_id)
        raw, _radius = await comps_mod.search(
            country_iso2=self.country_iso2,
            lat=target.latitude, lon=target.longitude, as_of=as_of,
            policy=self.comparable_policy,
            target_type=target.property_type,
            target_area=target.floor_area_sqm,
            exclude_property_id=property_id,
            symmetric_window=as_of < date.today().replace(month=1, day=1),
        )
        # Index-adjust each so the panel can show what it implies for today.
        for comp in raw[:limit]:
            adj = await index_adjust.adjustment(
                country_iso2=self.country_iso2,
                district=comp.district or target.district,
                property_type=comp.property_type,
                from_date=comp.sold_date, to_date=as_of,
            )
            if adj:
                comp.index_adjusted_price = comp.price * adj.ratio
        return avm.to_comparable_models(raw, limit, self.currency_code)

    async def value_property(self, property_id: int, *, as_of: date) -> ValuationResult:
        target = await self._target(property_id)
        today = date.today()
        price_type = (
            PriceType.CURRENT_ESTIMATE
            if as_of.year >= today.year
            else PriceType.HISTORICAL_ESTIMATE
        )
        return await avm.value(
            target, as_of=as_of, policy=self.comparable_policy,
            source_refs=await self.sources(), price_type=price_type,
        )

    # --- the timeline: one figure per year, correctly typed ------------------

    async def price_for_year(
        self, property_id: int, year: int
    ) -> tuple[Price | None, DataStatus, str | None]:
        """The single figure the side panel shows for the selected year.

        Resolution order, which is what keeps the four price categories
        distinct (§2):
          1. a genuine sale recorded in that year          -> TRANSACTION
          2. year in the future                            -> FORECAST
          3. otherwise, a modelled value for that year     -> HISTORICAL/CURRENT ESTIMATE
        """
        srcs = await self.sources()
        today = date.today()

        # 1. A real sale that year always wins.
        row = await fetch_one(
            """
            SELECT t.id, t.price::float8 AS price, t.transaction_date,
                   t.currency_code, t.source_key, t.source_record_id,
                   t.market_value_basis
            FROM transactions t
            WHERE t.property_id = %s
              AND extract(year FROM t.transaction_date) = %s
            ORDER BY t.transaction_date DESC LIMIT 1
            """,
            (property_id, year),
        )
        if row:
            note = (
                "Recorded sale price from HM Land Registry."
                if row["market_value_basis"] == "STANDARD"
                else (
                    "Recorded sale, categorised by HM Land Registry as an "
                    "'additional price paid' transaction (for example a "
                    "repossession or a transfer not at full market value), so "
                    "it may not reflect market value."
                )
            )
            return (
                Price(
                    value=row["price"],
                    currency=row["currency_code"],
                    price_type=PriceType.TRANSACTION,
                    date=row["transaction_date"],
                    precision_level=PrecisionLevel.EXACT_TRANSACTION,
                    confidence=Confidence.HIGH,
                    methodology=note,
                    sources=[s for s in srcs if s.key == "uk_land_registry_ppd"],
                    source_record_id=row["source_record_id"],
                    last_updated=datetime.now(UTC),
                ),
                DataStatus.OK,
                None,
            )

        # 2. Future years -> forecast, built on the current estimate.
        if year > today.year:
            return await self._forecast_for_year(property_id, year, srcs)

        # 3. Modelled value for a past year or the present.
        as_of = min(date(year, 12, 31), today)
        result = await self.value_property(property_id, as_of=as_of)

        # 3b. For a past year with no usable comparables, fall back to
        # back-casting the current valuation with the official local index.
        # Clearly labelled and graded down — see valuation/backcast.py.
        if (
            result.status is DataStatus.INSUFFICIENT_EVIDENCE
            and year < today.year
        ):
            target = await self._target(property_id)
            current = await self.value_property(property_id, as_of=today)
            if current.status is DataStatus.OK:
                result = await backcast.backcast(
                    current=current, as_of=as_of,
                    country_iso2=self.country_iso2, district=target.district,
                    property_type=target.property_type, source_refs=srcs,
                )

        if result.status is not DataStatus.OK or result.estimated_price is None:
            return None, result.status, result.message

        is_current = year >= today.year
        return (
            Price(
                value=result.estimated_price,
                currency=self.currency_code,
                price_type=(
                    PriceType.CURRENT_ESTIMATE if is_current
                    else PriceType.HISTORICAL_ESTIMATE
                ),
                date=as_of,
                lower_bound=result.low_estimate,
                upper_bound=result.high_estimate,
                confidence=result.confidence,
                precision_level=PrecisionLevel.PROPERTY_ESTIMATE,
                methodology=result.method or "Comparable-sales AVM",
                sources=srcs,
                last_updated=result.computed_at,
                evidence=result.evidence,
            ),
            DataStatus.OK,
            None,
        )

    async def _forecast_for_year(
        self, property_id: int, year: int, srcs: list[SourceRef]
    ) -> tuple[Price | None, DataStatus, str | None]:
        """future value = current dwelling estimate x forecast market movement."""
        today = date.today()
        current = await self.value_property(property_id, as_of=today)
        if current.status is not DataStatus.OK or current.estimated_price is None:
            return (
                None,
                current.status,
                current.message
                or "A forecast needs a current valuation, which is not available here.",
            )

        target = await self._target(property_id)
        fc = await forecast_service.forecast_market(
            country_iso2=self.country_iso2,
            district=target.district,
            segment=target.property_type or "all",
            target=date(year, 6, 30),
        )
        if fc is None:
            return (
                None,
                DataStatus.OUT_OF_RANGE,
                f"A {year} forecast is beyond what the available price index supports.",
            )

        await forecast_service.cache_forecast(fc, self.country_iso2, self.currency_code)

        base = current.estimated_price
        # The forecast interval must compound the *current* valuation's own
        # uncertainty with the market forecast's, otherwise a long-horizon band
        # would look tighter than the estimate it is built on.
        cur_low_ratio = (current.low_estimate or base) / base
        cur_high_ratio = (current.high_estimate or base) / base

        return (
            Price(
                value=round(base * fc.ratio),
                currency=self.currency_code,
                price_type=PriceType.FORECAST,
                date=date(year, 6, 30),
                lower_bound=round(base * fc.ratio_low * cur_low_ratio),
                upper_bound=round(base * fc.ratio_high * cur_high_ratio),
                confidence=fc.confidence,
                precision_level=PrecisionLevel.PROPERTY_ESTIMATE,
                methodology=(
                    "Current comparable-sales valuation projected forward by a "
                    "shrunk local-drift forecast of the UK House Price Index "
                    f"for {fc.area_name}."
                ),
                sources=srcs,
                last_updated=datetime.now(UTC),
                evidence={
                    "current_estimate": round(base),
                    "current_estimate_confidence": (
                        current.confidence.value if current.confidence else None
                    ),
                    "forecast_market_movement_pct": round((fc.ratio - 1) * 100, 1),
                    "forecast_movement_range_pct": [
                        round((fc.ratio_low - 1) * 100, 1),
                        round((fc.ratio_high - 1) * 100, 1),
                    ],
                    "forecast_area": fc.area_name,
                    "forecast_area_level": fc.area_level,
                    "horizon_months": fc.horizon_months,
                    "model": fc.diagnostics,
                },
            ),
            DataStatus.OK,
            None,
        )

    async def get_historical_prices(
        self, property_id: int, *, from_year: int, to_year: int
    ) -> PriceHistory:
        """The chart series (§18): observed sales, back-cast estimates and
        forecasts, each carrying its own price type so they render distinctly."""
        points: list[PricePoint] = []
        for year in range(from_year, to_year + 1):
            price, status, _msg = await self.price_for_year(property_id, year)
            if price is None or status is not DataStatus.OK:
                continue
            points.append(PricePoint(year=year, date=price.date, price=price))

        if not points:
            return PriceHistory(
                property_id=property_id,
                currency=self.currency_code,
                status=DataStatus.INSUFFICIENT_EVIDENCE,
                message=(
                    "There is not enough evidence near this property to build a "
                    "price history."
                ),
            )
        return PriceHistory(
            property_id=property_id, currency=self.currency_code, points=points
        )


def register_coverage_row() -> dict:
    """The provider's own declaration of what it covers, written into
    `provider_coverage` by ingest/coverage.py."""
    return {
        "provider_key": UKPropertyProvider.key,
        "country_iso2": "GB",
        "region_code": None,
        "region_name": "England and Wales",
        "transaction_level_data": True,
        "property_characteristics": False,
        "market_index": True,
        "forecast_supported": True,
        "max_precision": PrecisionLevel.EXACT_TRANSACTION.value,
        "coordinate_precision": CoordinatePrecision.POSTCODE.value,
        "currency_code": currency_for_country("GB"),
        "notes": (
            "Transaction-level coverage is England and Wales only: Scotland "
            "(Registers of Scotland) and Northern Ireland (Land & Property "
            "Services) do not publish comparable open transaction data. "
            "Coordinates are postcode centroids, not building positions. "
            "Floor area and bedroom counts require the EPC dataset, which "
            "needs a free API key."
        ),
        "source_keys": list(UKPropertyProvider.source_keys),
    }
