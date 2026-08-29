"""Ingest France's geo-DVF (Demandes de valeurs foncières géolocalisées).

Two properties of this dataset drive the whole ingester:

1. **A "mutation" is a transfer, not a dwelling.** One declared value
   (`valeur_fonciere`) can cover several lots — a house plus two fields, or a
   whole apartment block. Attributing the full value to any one dwelling in
   those cases would be wrong. We therefore keep only mutations containing
   *exactly one* residential building and no commercial premises, which is the
   standard basis for per-dwelling DVF analysis. Everything else is counted and
   reported as rejected, not silently dropped.

2. **Coordinates are real.** `longitude`/`latitude` locate the cadastral
   parcel, so French records get `coordinate_precision = 'PARCEL'` — genuinely
   more precise than the UK's postcode centroids.
"""
from __future__ import annotations

import csv
import gzip
import logging
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.db import sync_conn

from .address import normalise_street, normalise_token
from .runs import finish_run, is_complete, start_run
from .sources import mark_ingested, register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "fr_dvf"
COUNTRY = "FR"
CURRENCY = "EUR"

# DVF `type_local` -> our normalised type. `house` deliberately, because DVF
# does not distinguish detached / semi-detached / terraced.
TYPE_LOCAL = {
    "Maison": "house",
    "Appartement": "flat",
}
# Present in a mutation, these mean it is not a clean single-dwelling sale.
COMMERCIAL_TYPES = {"Local industriel. commercial ou assimilé"}

# Plausibility bounds. These reject records that are broken rather than
# unusual: a declared value below EUR 5,000 or a floor area under 8 m2 is a
# data error, and EUR/m2 outside [150, 60000] is outside anything the French
# market produces. Every rejection is counted in the ingestion run.
MIN_PRICE = 5_000.0
MIN_SQM = 8.0
MIN_PPSM = 150.0
MAX_PPSM = 60_000.0

# Optional department restriction. geo-DVF covers all of France, but a full
# five-year load is ~4M mutations; on a disk-constrained machine you can load a
# subset and the coverage registry will record exactly what is present. Pass
# `--departments 75 69 13` to restrict. Two-character INSEE codes ('2A'/'2B'
# for Corsica, '971'-'976' for the overseas departments).
DEFAULT_DEPARTMENTS: list[str] | None = None

STAGING_DDL = """
DROP TABLE IF EXISTS stg_dvf;
CREATE UNLOGGED TABLE stg_dvf (
    id_mutation      text,
    id_parcelle      text,
    date_mutation    date,
    price            numeric(14,2),
    type_local       text,
    property_type    text,
    surface          numeric(10,2),
    rooms            smallint,
    lot_area         numeric(12,2),
    numero           text,
    voie             text,
    code_postal      text,
    commune          text,
    departement      text,
    lon              double precision,
    lat              double precision,
    is_commercial    boolean
);
"""

# One row per mutation that contains exactly one residential building and no
# commercial premises. `bool_or`/`count(... filter ...)` do the qualification in
# a single pass over the staged rows.
# Split into three statements: a parameterised statement cannot contain
# multiple commands, and this one has to be parameterised because the
# plausibility bounds are configurable.
CLEAN_DROP_SQL = "DROP TABLE IF EXISTS stg_dvf_clean"

CLEAN_SQL = """
CREATE UNLOGGED TABLE stg_dvf_clean AS
WITH qualified AS (
    SELECT id_mutation,
           count(*) FILTER (WHERE property_type IS NOT NULL) AS dwellings,
           bool_or(is_commercial) AS has_commercial
    FROM stg_dvf
    GROUP BY id_mutation
)
SELECT d.*
FROM stg_dvf d
JOIN qualified q USING (id_mutation)
WHERE q.dwellings = 1
  AND NOT q.has_commercial
  AND d.property_type IS NOT NULL
  AND d.lat IS NOT NULL AND d.lon IS NOT NULL
  AND d.price >= %(min_price)s
  AND d.surface >= %(min_sqm)s
  AND (d.price / d.surface) BETWEEN %(min_ppsm)s AND %(max_ppsm)s
"""

CLEAN_INDEX_SQL = "CREATE INDEX ON stg_dvf_clean (id_mutation, id_parcelle)"

UPSERT_PROPERTIES = """
INSERT INTO properties (
    country_iso2, property_key, external_id, address_line, paon, street,
    town, district, county, postcode, postcode_norm, geom,
    coordinate_precision, property_type, habitable_rooms, floor_area_sqm,
    lot_area_sqm, is_residential, source_keys, characteristics_source,
    first_seen, last_seen
)
SELECT DISTINCT ON (c.pkey)
    %(country)s, c.pkey, c.id_parcelle, c.address_line, c.numero, c.voie,
    c.commune, c.commune, c.departement, c.code_postal, c.code_postal,
    ST_SetSRID(ST_MakePoint(c.lon, c.lat), 4326), 'PARCEL'::coord_precision,
    c.property_type, c.rooms, c.surface, c.lot_area, true,
    ARRAY[%(source_key)s]::text[], %(source_key)s,
    min(c.date_mutation) OVER (PARTITION BY c.pkey),
    max(c.date_mutation) OVER (PARTITION BY c.pkey)
FROM (
    SELECT *,
           md5(coalesce(id_parcelle,'') || '|' || coalesce(property_type,'') || '|' ||
               coalesce(surface::text,'') || '|' || coalesce(rooms::text,'')) AS pkey,
           nullif(trim(coalesce(numero,'') || ' ' || coalesce(voie,'')), '') AS address_line
    FROM stg_dvf_clean
) c
ORDER BY c.pkey, c.date_mutation DESC
ON CONFLICT (country_iso2, property_key) DO UPDATE SET
    geom = COALESCE(EXCLUDED.geom, properties.geom),
    floor_area_sqm = COALESCE(EXCLUDED.floor_area_sqm, properties.floor_area_sqm),
    habitable_rooms = COALESCE(EXCLUDED.habitable_rooms, properties.habitable_rooms),
    lot_area_sqm = COALESCE(EXCLUDED.lot_area_sqm, properties.lot_area_sqm),
    property_type = COALESCE(EXCLUDED.property_type, properties.property_type),
    first_seen = LEAST(properties.first_seen, EXCLUDED.first_seen),
    last_seen  = GREATEST(properties.last_seen, EXCLUDED.last_seen),
    updated_at = now();
"""

UPSERT_TRANSACTIONS = """
INSERT INTO transactions (
    property_id, country_iso2, source_key, source_record_id, transaction_date,
    price, currency_code, property_type, floor_area_sqm, rooms,
    postcode_norm, outcode, street, town, district, county, geom,
    coordinate_precision, market_value_basis, is_residential
)
SELECT DISTINCT ON (c.id_mutation, c.id_parcelle)
    p.id, %(country)s, %(source_key)s,
    c.id_mutation || ':' || coalesce(c.id_parcelle, ''),
    c.date_mutation, c.price, %(currency)s, c.property_type, c.surface,
    c.rooms, c.code_postal, left(c.code_postal, 2), c.voie, c.commune,
    c.commune, c.departement,
    ST_SetSRID(ST_MakePoint(c.lon, c.lat), 4326), 'PARCEL'::coord_precision,
    'STANDARD', true
FROM (
    SELECT *,
           md5(coalesce(id_parcelle,'') || '|' || coalesce(property_type,'') || '|' ||
               coalesce(surface::text,'') || '|' || coalesce(rooms::text,'')) AS pkey
    FROM stg_dvf_clean
) c
JOIN properties p ON p.country_iso2 = %(country)s AND p.property_key = c.pkey
ORDER BY c.id_mutation, c.id_parcelle, c.date_mutation DESC
ON CONFLICT (source_key, source_record_id) DO UPDATE SET
    price = EXCLUDED.price,
    transaction_date = EXCLUDED.transaction_date,
    floor_area_sqm = EXCLUDED.floor_area_sqm,
    geom = COALESCE(EXCLUDED.geom, transactions.geom);
"""

COPY_SQL = (
    "COPY stg_dvf (id_mutation, id_parcelle, date_mutation, price, type_local, "
    "property_type, surface, rooms, lot_area, numero, voie, code_postal, "
    "commune, departement, lon, lat, is_commercial) FROM STDIN"
)


def _f(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _i(value: str | None) -> int | None:
    f = _f(value)
    return int(f) if f is not None else None


def ingest_file(path: Path, departments: list[str] | None = None) -> dict[str, int]:
    stat = path.stat()
    dept_tag = ",".join(sorted(departments)) if departments else "all"
    signature = f"{stat.st_size}:{int(stat.st_mtime)}:{dept_tag}"
    if is_complete(SOURCE_KEY, path.name, signature):
        log.info("%s already ingested — skipping", path.name)
        return {"read": 0, "written": 0, "rejected": 0, "skipped": 1}

    run_id = start_run(SOURCE_KEY, path.name, stat.st_size, signature)
    read = rejected = 0
    props = txns = kept = 0

    try:
        with sync_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(STAGING_DDL)
            conn.commit()

            with conn.cursor() as cur, gzip.open(path, "rt", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                with cur.copy(COPY_SQL) as cp:
                    for row in reader:
                        read += 1
                        if (row.get("nature_mutation") or "").strip() != "Vente":
                            rejected += 1
                            continue
                        try:
                            mutation_date = date.fromisoformat(
                                (row.get("date_mutation") or "")[:10]
                            )
                        except ValueError:
                            rejected += 1
                            continue

                        dept = (row.get("code_departement") or "").strip()
                        if departments and dept not in departments:
                            rejected += 1
                            continue

                        type_local = (row.get("type_local") or "").strip()
                        ptype = TYPE_LOCAL.get(type_local)
                        voie = normalise_street(row.get("adresse_nom_voie"))
                        cp.write_row(
                            (
                                row.get("id_mutation"),
                                row.get("id_parcelle"),
                                mutation_date,
                                _f(row.get("valeur_fonciere")),
                                type_local or None,
                                ptype,
                                _f(row.get("surface_reelle_bati")),
                                _i(row.get("nombre_pieces_principales")),
                                _f(row.get("surface_terrain")),
                                normalise_token(row.get("adresse_numero")) or None,
                                voie.title() or None,
                                (row.get("code_postal") or "").strip() or None,
                                normalise_token(row.get("nom_commune")).title() or None,
                                (row.get("code_departement") or "").strip() or None,
                                _f(row.get("longitude")),
                                _f(row.get("latitude")),
                                type_local in COMMERCIAL_TYPES,
                            )
                        )
                        if read % 500_000 == 0:
                            log.info("  staged %s rows", f"{read:,}")
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("CREATE INDEX ON stg_dvf (id_mutation)")
                cur.execute("ANALYZE stg_dvf")
                cur.execute(CLEAN_DROP_SQL)
                cur.execute(
                    CLEAN_SQL,
                    {"min_price": MIN_PRICE, "min_sqm": MIN_SQM,
                     "min_ppsm": MIN_PPSM, "max_ppsm": MAX_PPSM},
                )
                cur.execute(CLEAN_INDEX_SQL)
                cur.execute("ANALYZE stg_dvf_clean")
                cur.execute("SELECT count(*) AS n FROM stg_dvf_clean")
                kept = cur.fetchone()["n"]
            conn.commit()

            params = {"country": COUNTRY, "currency": CURRENCY, "source_key": SOURCE_KEY}
            with conn.cursor() as cur:
                cur.execute(UPSERT_PROPERTIES, params)
                props = cur.rowcount
                cur.execute(UPSERT_TRANSACTIONS, params)
                txns = cur.rowcount
                cur.execute("DROP TABLE IF EXISTS stg_dvf_clean")
                cur.execute("DROP TABLE IF EXISTS stg_dvf")
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, txns, rejected, error=str(exc))
        raise

    # Rows discarded by the single-dwelling qualification are legitimate
    # rejections and are reported as such.
    rejected += max(0, read - rejected - kept)
    finish_run(run_id, "complete", read, txns, rejected)
    log.info(
        "%s: read=%s single-dwelling=%s properties=%s transactions=%s",
        path.name, f"{read:,}", f"{kept:,}", f"{props:,}", f"{txns:,}",
    )
    return {"read": read, "written": txns, "rejected": rejected, "skipped": 0}


def ingest(
    years: list[int] | None = None, departments: list[str] | None = None
) -> dict[str, int]:
    settings = get_settings()
    register_sources()
    dvf_dir = settings.raw_dir / "dvf"
    files = sorted(dvf_dir.glob("dvf-*.csv.gz"))
    if years:
        wanted = {f"dvf-{y}.csv.gz" for y in years}
        files = [f for f in files if f.name in wanted]
    if not files:
        raise FileNotFoundError(f"No dvf-*.csv.gz in {dvf_dir}. Run `make data-download`.")

    totals = {"read": 0, "written": 0, "rejected": 0, "skipped": 0}
    for path in files:
        result = ingest_file(path, departments)
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
    log.info("geo-DVF: %s transactions in database", f"{total:,}")
    return totals


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Ingest France geo-DVF")
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument(
        "--departments", nargs="*",
        help="INSEE department codes to load (default: every department)",
    )
    args = ap.parse_args()
    print(ingest(args.years, args.departments or DEFAULT_DEPARTMENTS))
