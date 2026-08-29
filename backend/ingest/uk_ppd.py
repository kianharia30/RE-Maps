"""Ingest HM Land Registry Price Paid Data (England & Wales).

Pipeline per yearly file:

    CSV  --stream-->  COPY into UNLOGGED stg_ppd  --SQL-->  properties
                                                   --SQL-->  transactions

Nothing is ever held in RAM in bulk: rows are normalised one at a time as they
are written into the COPY stream, and all set-based work (dedup, joining to
postcode centroids, upserting) happens inside PostgreSQL.

Coordinates: PPD has none. Each record is joined to its postcode centroid and
recorded as ``coordinate_precision = 'POSTCODE'``. This is a genuine
limitation, not a modelling choice — building-level coordinates for England &
Wales require OS AddressBase, which is licensed and not open data.
"""
from __future__ import annotations

import csv
import gzip
import logging
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.db import sync_conn

from .address import (
    display_address,
    normalise_postcode,
    normalise_street,
    normalise_token,
    pretty_uk_postcode,
    property_key,
    uk_postcode_parts,
)
from .runs import finish_run, is_complete, start_run
from .sources import mark_ingested, register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "uk_land_registry_ppd"
COUNTRY = "GB"
CURRENCY = "GBP"

# Column indexes in the (headerless) Price Paid CSV.
C_ID, C_PRICE, C_DATE, C_POSTCODE = 0, 1, 2, 3
C_TYPE, C_NEWBUILD, C_DURATION = 4, 5, 6
C_PAON, C_SAON, C_STREET, C_LOCALITY = 7, 8, 9, 10
C_TOWN, C_DISTRICT, C_COUNTY = 11, 12, 13
C_PPD_CATEGORY, C_RECORD_STATUS = 14, 15

PROPERTY_TYPE = {
    "D": "detached",
    "S": "semi_detached",
    "T": "terraced",
    "F": "flat",
    "O": "other",
}
TENURE = {"F": "freehold", "L": "leasehold", "U": "unknown"}

STAGING_DDL = """
DROP TABLE IF EXISTS stg_ppd;
CREATE UNLOGGED TABLE stg_ppd (
    source_record_id text,
    property_key     text,
    price            numeric(14,2),
    transaction_date date,
    postcode_norm    text,
    postcode_pretty  text,
    outcode          text,
    sector           text,
    property_type    text,
    tenure           text,
    new_build        boolean,
    paon             text,
    saon             text,
    street           text,
    address_line     text,
    locality         text,
    town             text,
    district         text,
    county           text,
    basis            text,
    is_residential   boolean
);
"""

# ---------------------------------------------------------------------------
# Properties. DISTINCT ON collapses the many transactions of one dwelling into
# a single row; the ORDER BY makes "latest record wins" deterministic.
# ---------------------------------------------------------------------------
UPSERT_PROPERTIES = """
INSERT INTO properties (
    country_iso2, property_key, address_line, saon, paon, street, locality,
    town, district, county, postcode, postcode_norm, geom,
    coordinate_precision, property_type, tenure, new_build_at_sale,
    is_residential, source_keys, first_seen, last_seen
)
SELECT DISTINCT ON (s.property_key)
    %(country)s, s.property_key, s.address_line, s.saon, s.paon, s.street,
    s.locality, s.town, s.district, s.county, s.postcode_pretty,
    s.postcode_norm, pc.geom, pc.precision, s.property_type, s.tenure,
    s.new_build, s.is_residential, ARRAY[%(source_key)s]::text[],
    min(s.transaction_date) OVER (PARTITION BY s.property_key),
    max(s.transaction_date) OVER (PARTITION BY s.property_key)
FROM stg_ppd s
LEFT JOIN postcodes pc
       ON pc.country_iso2 = %(country)s AND pc.postcode = s.postcode_norm
ORDER BY s.property_key, s.transaction_date DESC
ON CONFLICT (country_iso2, property_key) DO UPDATE SET
    address_line  = COALESCE(properties.address_line, EXCLUDED.address_line),
    geom          = COALESCE(properties.geom, EXCLUDED.geom),
    coordinate_precision = COALESCE(properties.coordinate_precision,
                                    EXCLUDED.coordinate_precision),
    property_type = COALESCE(EXCLUDED.property_type, properties.property_type),
    tenure        = COALESCE(EXCLUDED.tenure, properties.tenure),
    is_residential= EXCLUDED.is_residential,
    source_keys   = (SELECT array_agg(DISTINCT k)
                     FROM unnest(properties.source_keys || EXCLUDED.source_keys) k),
    first_seen    = LEAST(properties.first_seen, EXCLUDED.first_seen),
    last_seen     = GREATEST(properties.last_seen, EXCLUDED.last_seen),
    updated_at    = now();
"""

# ---------------------------------------------------------------------------
# Transactions. Deduped on (source_key, source_record_id): re-running a file,
# or loading an overlapping monthly update, cannot create duplicates.
# ---------------------------------------------------------------------------
UPSERT_TRANSACTIONS = """
INSERT INTO transactions (
    property_id, country_iso2, source_key, source_record_id, transaction_date,
    price, currency_code, property_type, tenure, new_build, postcode_norm,
    outcode, sector, street, town, district, county, geom,
    coordinate_precision, market_value_basis, is_residential
)
SELECT DISTINCT ON (s.source_record_id)
    p.id, %(country)s, %(source_key)s, s.source_record_id, s.transaction_date,
    s.price, %(currency)s, s.property_type, s.tenure, s.new_build,
    s.postcode_norm, s.outcode, s.sector, s.street, s.town, s.district,
    s.county, pc.geom, pc.precision, s.basis, s.is_residential
FROM stg_ppd s
JOIN properties p
      ON p.country_iso2 = %(country)s AND p.property_key = s.property_key
LEFT JOIN postcodes pc
      ON pc.country_iso2 = %(country)s AND pc.postcode = s.postcode_norm
ORDER BY s.source_record_id, s.transaction_date DESC
ON CONFLICT (source_key, source_record_id) DO UPDATE SET
    price            = EXCLUDED.price,
    transaction_date = EXCLUDED.transaction_date,
    property_type    = EXCLUDED.property_type,
    tenure           = EXCLUDED.tenure,
    market_value_basis = EXCLUDED.market_value_basis,
    geom             = COALESCE(EXCLUDED.geom, transactions.geom);
"""


def _parse_row(row: list[str]) -> tuple | None:
    """Normalise one CSV row, or return None if it must be rejected."""
    if len(row) < 16:
        return None
    # 'D' = the record has been deleted upstream; never load it.
    if row[C_RECORD_STATUS].strip().upper() == "D":
        return None

    try:
        price = float(row[C_PRICE])
    except ValueError:
        return None
    # £0/£1 placeholder consideration is not a price.
    if price < 100:
        return None

    raw_date = row[C_DATE].strip()
    try:
        txn_date = date.fromisoformat(raw_date[:10])
    except ValueError:
        return None

    postcode_norm = normalise_postcode(row[C_POSTCODE])
    _area, outcode, sector = uk_postcode_parts(postcode_norm)

    ptype = PROPERTY_TYPE.get(row[C_TYPE].strip().upper(), "other")
    paon = normalise_token(row[C_PAON]) or None
    saon = normalise_token(row[C_SAON]) or None
    street = normalise_street(row[C_STREET]) or None
    town = normalise_token(row[C_TOWN]) or None

    category = row[C_PPD_CATEGORY].strip().upper()
    basis = {"A": "STANDARD", "B": "ADDITIONAL"}.get(category, "UNKNOWN")

    return (
        row[C_ID].strip(),
        property_key(
            postcode=postcode_norm, paon=paon, saon=saon, street=street, town=town
        ),
        price,
        txn_date,
        postcode_norm or None,
        pretty_uk_postcode(postcode_norm) if postcode_norm else None,
        outcode or None,
        sector or None,
        ptype,
        TENURE.get(row[C_DURATION].strip().upper(), "unknown"),
        row[C_NEWBUILD].strip().upper() == "Y",
        paon,
        saon,
        (street or "").title() or None,
        display_address(saon, paon, street),
        normalise_token(row[C_LOCALITY]).title() or None,
        (town or "").title() or None,
        normalise_token(row[C_DISTRICT]).title() or None,
        normalise_token(row[C_COUNTY]).title() or None,
        basis,
        ptype != "other",
    )


COPY_SQL = (
    "COPY stg_ppd (source_record_id, property_key, price, transaction_date, "
    "postcode_norm, postcode_pretty, outcode, sector, property_type, tenure, "
    "new_build, paon, saon, street, address_line, locality, town, district, "
    "county, basis, is_residential) FROM STDIN"
)


def _open_csv(path: Path):
    """Open a Price Paid file, transparently handling gzip.

    Raw downloads are large (~160 MB/year uncompressed, ~40 MB gzipped), so the
    download step keeps them compressed and ingestion streams straight out of
    the archive.
    """
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return open(path, newline="", encoding="utf-8-sig")


def ingest_file(path: Path, source_ids: dict[str, int]) -> dict[str, int]:
    """Load one Price Paid CSV (.csv or .csv.gz). Idempotent and safe to re-run."""
    stat = path.stat()
    signature = f"{stat.st_size}:{int(stat.st_mtime)}"
    if is_complete(SOURCE_KEY, path.name, signature):
        log.info("%s already ingested — skipping", path.name)
        return {"read": 0, "written": 0, "rejected": 0, "skipped": 1}

    run_id = start_run(SOURCE_KEY, path.name, stat.st_size, signature)
    read = rejected = 0
    props = txns = 0

    try:
        with sync_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(STAGING_DDL)
            conn.commit()

            with conn.cursor() as cur, _open_csv(path) as fh, cur.copy(COPY_SQL) as cp:
                for row in csv.reader(fh):
                    read += 1
                    parsed = _parse_row(row)
                    if parsed is None:
                        rejected += 1
                        continue
                    cp.write_row(parsed)
            conn.commit()

            params = {
                "country": COUNTRY,
                "currency": CURRENCY,
                "source_key": SOURCE_KEY,
            }
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE INDEX ON stg_ppd (property_key); "
                    "CREATE INDEX ON stg_ppd (postcode_norm); "
                    "ANALYZE stg_ppd"
                )
                cur.execute(UPSERT_PROPERTIES, params)
                props = cur.rowcount
                cur.execute(UPSERT_TRANSACTIONS, params)
                txns = cur.rowcount
                cur.execute("DROP TABLE IF EXISTS stg_ppd")
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, txns, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, txns, rejected)
    log.info(
        "%s: read=%s rejected=%s properties=%s transactions=%s",
        path.name, f"{read:,}", f"{rejected:,}", f"{props:,}", f"{txns:,}",
    )
    return {"read": read, "written": txns, "rejected": rejected, "skipped": 0}


def ingest(years: list[int] | None = None) -> dict[str, int]:
    settings = get_settings()
    source_ids = register_sources()
    ppd_dir = settings.raw_dir / "ppd"
    files = sorted(
        [*ppd_dir.glob("pp-*.csv"), *ppd_dir.glob("pp-*.csv.gz")],
        key=lambda p: p.name,
    )
    # Prefer the plain CSV when both forms of the same year are present.
    by_year: dict[str, Path] = {}
    for path in files:
        year_key = path.name.split(".")[0]
        if year_key not in by_year or path.suffix == ".csv":
            by_year[year_key] = path
    if years:
        wanted = {f"pp-{y}" for y in years}
        by_year = {k: v for k, v in by_year.items() if k in wanted}
    files = [by_year[k] for k in sorted(by_year)]
    if not files:
        raise FileNotFoundError(
            f"No pp-*.csv[.gz] files in {ppd_dir}. Run `make data-download` first."
        )

    totals = {"read": 0, "written": 0, "rejected": 0, "skipped": 0}
    for path in files:
        result = ingest_file(path, source_ids)
        for k in totals:
            totals[k] += result[k]

    with sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("ANALYZE properties")
            cur.execute("ANALYZE transactions")
            cur.execute(
                "SELECT count(*) AS n FROM transactions WHERE source_key = %s",
                (SOURCE_KEY,),
            )
            total = cur.fetchone()["n"]
        conn.commit()
    mark_ingested(SOURCE_KEY, total)
    log.info("Price Paid Data: %s transactions in database", f"{total:,}")
    return totals


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Ingest HM Land Registry Price Paid Data")
    ap.add_argument("--years", type=int, nargs="*", help="restrict to these years")
    args = ap.parse_args()
    print(ingest(args.years))
