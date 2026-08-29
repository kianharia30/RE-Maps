"""The coverage registry (§5).

Answers: *for this place, what data genuinely exists?* The API consults this
before doing any work, so an unsupported country costs one indexed lookup and
returns an honest answer rather than an empty result set that looks like a bug.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from ..db import fetch_all, fetch_one
from ..models.coverage import CoverageEntry, CoverageIndex, CoverageResponse
from ..models.enums import CoordinatePrecision, DataStatus, PrecisionLevel
from ..models.price import SourceRef
from .errors import UnsupportedLocation

_ENTRY_COLS = """
    pc.provider_key, pc.country_iso2, c.name AS country_name, pc.region_code,
    pc.region_name, pc.transaction_level_data, pc.property_characteristics,
    pc.market_index, pc.forecast_supported, pc.max_precision,
    pc.coordinate_precision, pc.historical_from, pc.historical_to,
    pc.currency_code, pc.notes, pc.source_keys
"""


async def _source_refs(keys: list[str]) -> list[SourceRef]:
    if not keys:
        return []
    rows = await fetch_all(
        """
        SELECT key, name, owner, url, licence, licence_url, attribution,
               source_published_at, last_ingested_at
        FROM data_sources WHERE key = ANY(%s) ORDER BY key
        """,
        (keys,),
    )
    return [SourceRef(**row) for row in rows]


async def _to_entry(row: dict) -> CoverageEntry:
    return CoverageEntry(
        provider_key=row["provider_key"],
        country_iso2=row["country_iso2"],
        country_name=row.get("country_name"),
        region_code=row["region_code"],
        region_name=row["region_name"],
        transaction_level_data=row["transaction_level_data"],
        property_characteristics=row["property_characteristics"],
        market_index=row["market_index"],
        forecast_supported=row["forecast_supported"],
        max_precision=PrecisionLevel(row["max_precision"]),
        coordinate_precision=(
            CoordinatePrecision(row["coordinate_precision"])
            if row["coordinate_precision"] else None
        ),
        historical_from=row["historical_from"],
        historical_to=row["historical_to"],
        currency_code=row["currency_code"],
        notes=row["notes"],
        sources=await _source_refs(row["source_keys"] or []),
    )


async def lookup(
    country_iso2: str, region_code: str | None = None
) -> CoverageEntry | None:
    """Coverage entry for a jurisdiction, most specific first.

    A country's data coverage is not always uniform. Where a `region_code` has
    its own row, that row wins — this is what stops the UK entry (built on
    HM Land Registry Price Paid Data, England and Wales only) from being
    applied to Scotland or Northern Ireland.
    """
    if region_code:
        row = await fetch_one(
            f"""
            SELECT {_ENTRY_COLS}
            FROM provider_coverage pc
            LEFT JOIN countries c ON c.iso2 = pc.country_iso2
            WHERE pc.country_iso2 = %s AND pc.region_code = %s
            LIMIT 1
            """,
            (country_iso2.upper(), region_code),
        )
        if row:
            return await _to_entry(row)

    row = await fetch_one(
        f"""
        SELECT {_ENTRY_COLS}
        FROM provider_coverage pc
        LEFT JOIN countries c ON c.iso2 = pc.country_iso2
        WHERE pc.country_iso2 = %s AND pc.region_code IS NULL
        LIMIT 1
        """,
        (country_iso2.upper(),),
    )
    return await _to_entry(row) if row else None


def is_usable(entry: CoverageEntry | None) -> bool:
    """Whether an entry represents genuinely available property data.

    A region can be *registered* precisely in order to record that it has
    nothing — Scotland and Northern Ireland exist in the registry so that the
    England-and-Wales entry cannot be applied to them by accident. Such a row
    must be treated as no coverage.
    """
    if entry is None:
        return False
    return entry.transaction_level_data and entry.max_precision is not PrecisionLevel.NONE


async def require(
    country_iso2: str, region_code: str | None = None
) -> CoverageEntry:
    entry = await lookup(country_iso2, region_code)
    if not is_usable(entry):
        raise UnsupportedLocation(
            entry.notes if entry and entry.notes else None
        )
    return entry


async def has_coverage(
    country_iso2: str | None, region_code: str | None = None
) -> bool:
    if not country_iso2:
        return False
    return is_usable(await lookup(country_iso2, region_code))


async def supported_countries() -> set[str]:
    """Countries with at least one usable coverage entry.

    Used by search to flag results, so a country registered only to record its
    *absence* of data must not appear here.
    """
    rows = await fetch_all(
        """
        SELECT DISTINCT country_iso2 FROM provider_coverage
        WHERE transaction_level_data AND max_precision <> 'NONE'
        """
    )
    return {r["country_iso2"] for r in rows}


async def index() -> CoverageIndex:
    rows = await fetch_all(
        f"""
        SELECT {_ENTRY_COLS}
        FROM provider_coverage pc
        LEFT JOIN countries c ON c.iso2 = pc.country_iso2
        ORDER BY pc.country_iso2, pc.region_code NULLS FIRST
        """
    )
    return CoverageIndex(
        supported=[await _to_entry(r) for r in rows],
        generated_at=datetime.now(UTC).isoformat(),
    )


async def describe(
    country_iso2: str | None,
    max_forecast_years: int,
    region_code: str | None = None,
) -> CoverageResponse:
    """Full coverage answer including the year range the timeline should offer."""
    if not country_iso2:
        return CoverageResponse(
            status=DataStatus.UNSUPPORTED_LOCATION,
            message=(
                "Property price data is not currently available for this location."
            ),
        )
    entry = await lookup(country_iso2, region_code)
    name = await fetch_one("SELECT name FROM countries WHERE iso2=%s", (country_iso2,))
    country_nm = name["name"] if name else None

    if not is_usable(entry):
        # An unusable entry still carries the *reason*, which is far more useful
        # to a user than a bare "not available".
        return CoverageResponse(
            status=DataStatus.UNSUPPORTED_LOCATION,
            message=(
                (entry.notes if entry and entry.notes else None)
                or "Property price data is not currently available for this location."
            ),
            country_iso2=country_iso2,
            country_name=country_nm,
        )

    # The timeline's bounds come from what is actually in the database, not
    # from a hard-coded range (§11).
    #
    # The upper bound is the later of the last recorded transaction and the last
    # published index month, because a valuation needs BOTH: comparable sales to
    # anchor on, and an index to restate them at the requested date. Offering a
    # year beyond that would put selectable years on the timeline that can only
    # ever answer "insufficient evidence" — France's transactions stop at 2023,
    # so its timeline stops there too, rather than at the current year.
    bounds = await fetch_one(
        """
        SELECT (SELECT min(extract(year FROM transaction_date))::int
                  FROM transactions WHERE country_iso2 = %(c)s) AS min_txn_year,
               (SELECT max(extract(year FROM transaction_date))::int
                  FROM transactions WHERE country_iso2 = %(c)s) AS max_txn_year,
               (SELECT max(extract(year FROM period))::int
                  FROM market_indices WHERE country_iso2 = %(c)s) AS max_index_year
        """,
        {"c": country_iso2},
    )
    bounds = bounds or {}
    min_year = bounds.get("min_txn_year")
    max_txn_year = bounds.get("max_txn_year")
    max_index_year = bounds.get("max_index_year")
    current_year = date.today().year

    max_year = max(
        [y for y in (max_txn_year, max_index_year) if y] or [current_year]
    )
    # Never advertise a year beyond the present as though it were observed.
    max_year = min(max_year, current_year)

    return CoverageResponse(
        status=DataStatus.OK,
        country_iso2=country_iso2,
        country_name=country_nm,
        entry=entry,
        min_year=min_year,
        max_data_year=max_year,
        max_forecast_year=(
            (max_year or current_year) + max_forecast_years
            if entry.forecast_supported else max_year
        ),
        current_year=current_year,
    )
