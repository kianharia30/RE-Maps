"""FrancePropertyProvider — from the official geo-DVF transaction dataset.

This provider exists partly to prove the architecture is genuinely
jurisdiction-agnostic: France's data profile is *materially different* from the
UK's, and the frontend needs no France-specific code to render it correctly.

    Feature                UK (HM Land Registry)   France (geo-DVF)
    ---------------------  ----------------------  -------------------------
    Transaction prices     1995 ->                 2021 ->
    Coordinates            postcode centroid       cadastral parcel
    Floor area             only via EPC (key)      included in every record
    Official price index   UK HPI, monthly, LA     none ingested
    Forecast               yes                     NO - index history too short

Because there is no long official index for France, forecasting is switched
off, and the API reports that honestly rather than extrapolating four years of
data ten years forward. Index adjustment for historical estimates uses a
price-per-square-metre index derived from the DVF transactions themselves,
which is labelled as derived and not mix-adjusted.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from ...core.currency import currency_for_country
from ...core.errors import NotFound
from ...db import fetch_all, fetch_one
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


class FrancePropertyProvider(PropertyDataProvider):
    key = "fr_dvf"
    country_iso2 = "FR"
    currency_code = "EUR"
    source_keys = ("fr_dvf",)
    coordinate_precision = CoordinatePrecision.PARCEL
    max_precision = PrecisionLevel.EXACT_TRANSACTION
    supports_forecast = False        # no long official index — see module docstring
    supports_characteristics = True  # surface area and room count are in the data

    # Parcel-level coordinates justify a much tighter first ring than the UK's
    # postcode centroids: 150 m here really means 150 m.
    comparable_policy = ComparableSearchPolicy(
        radius_steps_m=(200, 450, 900, 1800, 3600),
        min_comparables=3,
        target_comparables=15,
        max_months_back=48,
        prefer_same_type=True,
        require_same_type=False,
    )

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
        self, *, west: float, south: float, east: float, north: float,
        date_range: DateRange, limit: int = 500,
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
            {"country": self.country_iso2, "w": west, "s": south, "e": east,
             "n": north, "from": date_range.start, "to": date_range.end,
             "types": property_types, "limit": limit},
        )
        return [Transaction(**r) for r in rows]

    async def get_property(self, property_id: int) -> PropertyDetail | None:
        row = await fetch_one(
            """
            SELECT p.*, ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon
            FROM properties p WHERE p.id = %s AND p.country_iso2 = %s
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
            # Parcel coordinates locate the plot, which for a flat is the
            # building rather than the individual dwelling.
            position_is_approximate=row["coordinate_precision"] != "PROPERTY",
            characteristics=PropertyCharacteristics(
                property_type=row["property_type"], tenure=row["tenure"],
                bedrooms=row["bedrooms"], bathrooms=row["bathrooms"],
                habitable_rooms=row["habitable_rooms"],
                floor_area_sqm=float(row["floor_area_sqm"]) if row["floor_area_sqm"] else None,
                lot_area_sqm=float(row["lot_area_sqm"]) if row["lot_area_sqm"] else None,
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
            WHERE t.property_id = %s ORDER BY t.transaction_date DESC
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
        return await fetch_all(
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
        raw, _ = await comps_mod.search(
            country_iso2=self.country_iso2, lat=target.latitude,
            lon=target.longitude, as_of=as_of, policy=self.comparable_policy,
            target_type=target.property_type, target_area=target.floor_area_sqm,
            exclude_property_id=property_id,
            symmetric_window=as_of < date.today().replace(month=1, day=1),
        )
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
            PriceType.CURRENT_ESTIMATE if as_of.year >= today.year
            else PriceType.HISTORICAL_ESTIMATE
        )
        return await avm.value(
            target, as_of=as_of, policy=self.comparable_policy,
            source_refs=await self.sources(), price_type=price_type,
        )

    async def price_for_year(
        self, property_id: int, year: int
    ) -> tuple[Price | None, DataStatus, str | None]:
        srcs = await self.sources()
        today = date.today()

        row = await fetch_one(
            """
            SELECT t.id, t.price::float8 AS price, t.transaction_date,
                   t.currency_code, t.source_record_id
            FROM transactions t
            WHERE t.property_id = %s AND extract(year FROM t.transaction_date) = %s
            ORDER BY t.transaction_date DESC LIMIT 1
            """,
            (property_id, year),
        )
        if row:
            return (
                Price(
                    value=row["price"], currency=row["currency_code"],
                    price_type=PriceType.TRANSACTION,
                    date=row["transaction_date"],
                    precision_level=PrecisionLevel.EXACT_TRANSACTION,
                    confidence=Confidence.HIGH,
                    methodology=(
                        "Declared transfer value (valeur foncière) recorded by "
                        "the DGFiP."
                    ),
                    sources=srcs, source_record_id=row["source_record_id"],
                    last_updated=datetime.now(UTC),
                ),
                DataStatus.OK, None,
            )

        if year > today.year:
            # Stated plainly rather than extrapolated: France has no long
            # official index in this deployment, so no forecast is defensible.
            return (
                None,
                DataStatus.OUT_OF_RANGE,
                (
                    "Forecasts are not available for France: the available "
                    "official transaction history (2021 onwards) is too short "
                    "to fit a defensible price-trend model."
                ),
            )

        as_of = min(date(year, 12, 31), today)
        result = await self.value_property(property_id, as_of=as_of)

        # No usable comparables near a past date: fall back to back-casting the
        # current valuation with the DVF-derived local index. Labelled and
        # graded down — see valuation/backcast.py.
        if result.status is DataStatus.INSUFFICIENT_EVIDENCE and year < today.year:
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
                value=result.estimated_price, currency=self.currency_code,
                price_type=(
                    PriceType.CURRENT_ESTIMATE if is_current
                    else PriceType.HISTORICAL_ESTIMATE
                ),
                date=as_of, lower_bound=result.low_estimate,
                upper_bound=result.high_estimate, confidence=result.confidence,
                precision_level=PrecisionLevel.PROPERTY_ESTIMATE,
                methodology=result.method or "Comparable-sales AVM",
                sources=srcs, last_updated=result.computed_at,
                evidence=result.evidence,
            ),
            DataStatus.OK, None,
        )

    async def get_historical_prices(
        self, property_id: int, *, from_year: int, to_year: int
    ) -> PriceHistory:
        points: list[PricePoint] = []
        for year in range(from_year, to_year + 1):
            price, status, _ = await self.price_for_year(property_id, year)
            if price is None or status is not DataStatus.OK:
                continue
            points.append(PricePoint(year=year, date=price.date, price=price))
        if not points:
            return PriceHistory(
                property_id=property_id, currency=self.currency_code,
                status=DataStatus.INSUFFICIENT_EVIDENCE,
                message="Not enough evidence near this property to build a price history.",
            )
        return PriceHistory(
            property_id=property_id, currency=self.currency_code, points=points
        )


def register_coverage_row() -> dict:
    return {
        "provider_key": FrancePropertyProvider.key,
        "country_iso2": "FR",
        "region_code": None,
        "region_name": "France (excluding Alsace, Moselle, Mayotte)",
        "transaction_level_data": True,
        "property_characteristics": True,
        "market_index": True,      # derived from DVF itself, clearly labelled
        "forecast_supported": False,
        "max_precision": PrecisionLevel.EXACT_TRANSACTION.value,
        "coordinate_precision": CoordinatePrecision.PARCEL.value,
        "currency_code": currency_for_country("FR"),
        "notes": (
            "Transactions from 2021 onwards with cadastral-parcel coordinates "
            "and declared floor area. Alsace, Moselle and Mayotte are excluded "
            "from the source dataset. Forecasting is disabled: the official "
            "history is too short to fit a defensible trend model. Historical "
            "estimates use a price-per-square-metre index derived from the DVF "
            "transactions themselves, which is not mix-adjusted."
        ),
        "source_keys": list(FrancePropertyProvider.source_keys),
    }
