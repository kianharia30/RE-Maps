"""Populate `provider_coverage` from what each provider declares AND what the
database actually contains (§5).

The two are cross-checked deliberately: a provider claiming transaction-level
coverage with zero rows loaded would be a lie, so the declared flags are
intersected with measured reality and the historical range is taken from the
data itself. If PPD has not been ingested, `transaction_level_data` comes out
false and the API honestly reports no coverage.
"""
from __future__ import annotations

import logging

from app.db import sync_conn
from app.providers.fr.provider import register_coverage_row as fr_row
from app.providers.uk.provider import register_coverage_row as uk_row

log = logging.getLogger(__name__)

DECLARATIONS = [uk_row, fr_row]

# ---------------------------------------------------------------------------
# Region rows that exist to record an ABSENCE.
#
# Edinburgh and Belfast are inside the GB country polygon, so a
# coordinates -> country resolver would otherwise apply the England-and-Wales
# entry to them and the API would claim data it does not have. Registering
# these regions explicitly, with `transaction_level_data = false` and
# `max_precision = NONE`, is what makes the honest answer the default.
#
# Each row carries the real reason, which the API returns verbatim so the user
# learns *why* rather than just "not available".
# ---------------------------------------------------------------------------
ABSENCE_ROWS: list[dict] = [
    {
        "provider_key": "uk_land_registry",
        "country_iso2": "GB",
        "region_code": "GB-SCT",
        "region_name": "Scotland",
        "transaction_level_data": False,
        "property_characteristics": False,
        "market_index": False,
        "forecast_supported": False,
        "max_precision": "NONE",
        "coordinate_precision": None,
        "historical_from": None,
        "historical_to": None,
        "currency_code": "GBP",
        "notes": (
            "Property price data is not currently available for Scotland. "
            "HM Land Registry Price Paid Data covers England and Wales only; "
            "Scottish transactions are registered with Registers of Scotland, "
            "which does not publish an equivalent open transaction-level "
            "dataset."
        ),
        "source_keys": [],
    },
    {
        "provider_key": "uk_land_registry",
        "country_iso2": "GB",
        "region_code": "GB-NIR",
        "region_name": "Northern Ireland",
        "transaction_level_data": False,
        "property_characteristics": False,
        "market_index": False,
        "forecast_supported": False,
        "max_precision": "NONE",
        "coordinate_precision": None,
        "historical_from": None,
        "historical_to": None,
        "currency_code": "GBP",
        "notes": (
            "Property price data is not currently available for Northern "
            "Ireland. HM Land Registry Price Paid Data covers England and "
            "Wales only; Northern Irish transactions are registered with Land "
            "& Property Services. Northern Ireland postcodes are also excluded "
            "from the open postcode gazetteer for licensing reasons."
        ),
        "source_keys": [],
    },
    {
        "provider_key": "uk_land_registry",
        "country_iso2": "GB",
        "region_code": "GB-UNKNOWN",
        "region_name": "Unresolved UK area",
        "transaction_level_data": False,
        "property_characteristics": False,
        "market_index": False,
        "forecast_supported": False,
        "max_precision": "NONE",
        "coordinate_precision": None,
        "historical_from": None,
        "historical_to": None,
        "currency_code": "GBP",
        "notes": (
            "This location could not be matched to a UK postcode area, so we "
            "cannot establish which property dataset covers it. No price is "
            "shown rather than guessing."
        ),
        "source_keys": [],
    },
]

UPSERT = """
INSERT INTO provider_coverage (
    provider_key, country_iso2, region_code, region_name,
    transaction_level_data, property_characteristics, market_index,
    forecast_supported, max_precision, coordinate_precision,
    historical_from, historical_to, currency_code, notes, source_keys,
    updated_at
) VALUES (
    %(provider_key)s, %(country_iso2)s, %(region_code)s, %(region_name)s,
    %(transaction_level_data)s, %(property_characteristics)s, %(market_index)s,
    %(forecast_supported)s, %(max_precision)s::precision_level,
    %(coordinate_precision)s::coord_precision, %(historical_from)s,
    %(historical_to)s, %(currency_code)s, %(notes)s, %(source_keys)s, now()
)
ON CONFLICT (provider_key, country_iso2, region_code) DO UPDATE SET
    region_name = EXCLUDED.region_name,
    transaction_level_data = EXCLUDED.transaction_level_data,
    property_characteristics = EXCLUDED.property_characteristics,
    market_index = EXCLUDED.market_index,
    forecast_supported = EXCLUDED.forecast_supported,
    max_precision = EXCLUDED.max_precision,
    coordinate_precision = EXCLUDED.coordinate_precision,
    historical_from = EXCLUDED.historical_from,
    historical_to = EXCLUDED.historical_to,
    currency_code = EXCLUDED.currency_code,
    notes = EXCLUDED.notes,
    source_keys = EXCLUDED.source_keys,
    updated_at = now();
"""

MEASURE = """
SELECT min(transaction_date) AS first_txn,
       max(transaction_date) AS last_txn,
       count(*) AS txn_count,
       count(*) FILTER (WHERE floor_area_sqm IS NOT NULL) AS with_area
FROM transactions WHERE country_iso2 = %s
"""

HAS_INDEX = """
SELECT count(*) AS n FROM market_indices WHERE country_iso2 = %s
"""


def register() -> int:
    rows = 0
    with sync_conn() as conn:
        for declare in DECLARATIONS:
            row = declare()
            country = row["country_iso2"]
            with conn.cursor() as cur:
                cur.execute(MEASURE, (country,))
                measured = cur.fetchone() or {}
                cur.execute(HAS_INDEX, (country,))
                index_rows = (cur.fetchone() or {}).get("n", 0)

            txn_count = measured.get("txn_count") or 0
            # Intersect the declaration with measured reality.
            row["transaction_level_data"] = bool(
                row["transaction_level_data"] and txn_count > 0
            )
            row["market_index"] = bool(row["market_index"] and index_rows > 0)
            row["forecast_supported"] = bool(
                row["forecast_supported"] and index_rows > 0
            )
            row["property_characteristics"] = bool(
                row["property_characteristics"] and (measured.get("with_area") or 0) > 0
            )
            row["historical_from"] = measured.get("first_txn")
            row["historical_to"] = measured.get("last_txn")

            if txn_count == 0:
                log.warning(
                    "%s: no transactions loaded — coverage recorded as "
                    "unsupported for transaction-level data",
                    country,
                )

            with conn.cursor() as cur:
                cur.execute(UPSERT, row)
            rows += 1
            log.info(
                "coverage %s: txns=%s index_rows=%s range=%s..%s",
                country, f"{txn_count:,}", f"{index_rows:,}",
                row["historical_from"], row["historical_to"],
            )
        # Absence rows are inserted verbatim: there is nothing to measure,
        # and their whole purpose is to assert that no data exists.
        for row in ABSENCE_ROWS:
            with conn.cursor() as cur:
                cur.execute(UPSERT, row)
            rows += 1
            log.info(
                "coverage %s/%s: registered as having no property data",
                row["country_iso2"], row["region_code"],
            )
        conn.commit()
    return rows


def purge_unbacked() -> int:
    """Remove coverage rows for countries with nothing loaded at all.

    Guarantees the invariant the whole product rests on: if we claim coverage,
    there is real data behind it.
    """
    with sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM provider_coverage pc
                -- Rows that assert an absence are kept: they are the mechanism
                -- that prevents a broader entry being applied to a region with
                -- no data.
                WHERE pc.max_precision <> 'NONE'
                AND NOT EXISTS (
                    SELECT 1 FROM transactions t WHERE t.country_iso2 = pc.country_iso2
                )
                AND NOT EXISTS (
                    SELECT 1 FROM market_indices m WHERE m.country_iso2 = pc.country_iso2
                )
                """
            )
            removed = cur.rowcount
        conn.commit()
    if removed:
        log.info("removed %s coverage rows with no data behind them", removed)
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    register()
    purge_unbacked()
    print("coverage registered")
