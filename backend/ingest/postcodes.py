"""Ingest the UK postcode centroid gazetteer (Open Postcode Geo).

Why this matters: HM Land Registry Price Paid Data contains an address and a
postcode but NO coordinates. This gazetteer is what lets us put a transaction
on the map — at postcode-centroid precision, which we record honestly as
``coordinate_precision = 'POSTCODE'`` and surface in the UI (§33).

Loaded with server-side COPY into an UNLOGGED staging table, then transformed
in SQL. 2.6M rows never enter Python memory as a whole.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from app.config import get_settings
from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import mark_ingested, register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "uk_open_postcode_geo"

# Column order of open_postcode_geo.csv (headerless). Verified against the
# published file: postcode, status, usertype, easting, northing,
# positional_quality, country, latitude, longitude, postcode_no_space, ...
COL_POSTCODE, COL_STATUS = 0, 1
COL_QUALITY, COL_COUNTRY = 5, 6
COL_LAT, COL_LON = 7, 8
COL_NOSPACE = 9
COL_AREA, COL_DISTRICT, COL_SECTOR = 12, 13, 14

STAGING_DDL = """
DROP TABLE IF EXISTS stg_postcodes;
CREATE UNLOGGED TABLE stg_postcodes (
    postcode_nospace text,
    postcode_pretty  text,
    status           text,
    quality          smallint,
    country          text,
    lat              double precision,
    lon              double precision,
    area             text,
    outcode          text,
    sector           text
);
"""

# Positional quality 1-6 are surveyed/matched positions; 7-9 are increasingly
# coarse imputations. We keep 1-6 at POSTCODE precision and demote the rest to
# NEIGHBOURHOOD so downstream code can treat them differently.
TRANSFORM = """
INSERT INTO postcodes (
    country_iso2, postcode, postcode_pretty, area, outcode, sector,
    geom, precision, status, source_id
)
SELECT
    'GB',
    s.postcode_nospace,
    s.postcode_pretty,
    s.area,
    s.outcode,
    s.sector,
    ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326),
    CASE WHEN s.quality <= 6 THEN 'POSTCODE'::coord_precision
         ELSE 'NEIGHBOURHOOD'::coord_precision END,
    s.status,
    %(source_id)s
FROM stg_postcodes s
WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
  AND s.lat BETWEEN -90 AND 90 AND s.lon BETWEEN -180 AND 180
  AND s.postcode_nospace <> ''
ON CONFLICT (country_iso2, postcode) DO UPDATE SET
    geom = EXCLUDED.geom,
    precision = EXCLUDED.precision,
    status = EXCLUDED.status,
    postcode_pretty = EXCLUDED.postcode_pretty;
"""


def ingest(path: Path | None = None, batch: int = 100_000) -> int:
    settings = get_settings()
    source_ids = register_sources()
    path = path or settings.raw_dir / "postcodes" / "open_postcode_geo.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `make data-download` first.")

    run_id = start_run(SOURCE_KEY, path.name, file_bytes=path.stat().st_size)
    read = written = rejected = 0

    try:
        with sync_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(STAGING_DDL)
            conn.commit()

            with conn.cursor() as cur, open(path, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                copy_sql = (
                    "COPY stg_postcodes (postcode_nospace, postcode_pretty, status, "
                    "quality, country, lat, lon, area, outcode, sector) FROM STDIN"
                )
                with cur.copy(copy_sql) as cp:
                    for row in reader:
                        read += 1
                        if len(row) < 15:
                            rejected += 1
                            continue
                        try:
                            lat = float(row[COL_LAT])
                            lon = float(row[COL_LON])
                            quality = int(row[COL_QUALITY] or 9)
                        except ValueError:
                            rejected += 1
                            continue
                        cp.write_row(
                            (
                                row[COL_NOSPACE].strip().upper(),
                                row[COL_POSTCODE].strip().upper(),
                                row[COL_STATUS],
                                quality,
                                row[COL_COUNTRY],
                                lat,
                                lon,
                                row[COL_AREA],
                                row[COL_DISTRICT],
                                row[COL_SECTOR],
                            )
                        )
                        if read % 500_000 == 0:
                            log.info("  staged %s rows", f"{read:,}")
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("ANALYZE stg_postcodes")
                cur.execute(TRANSFORM, {"source_id": source_ids[SOURCE_KEY]})
                written = cur.rowcount
                cur.execute("DROP TABLE IF EXISTS stg_postcodes")
                cur.execute("ANALYZE postcodes")
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    mark_ingested(SOURCE_KEY, written)
    log.info("postcodes: read=%s written=%s rejected=%s", read, written, rejected)
    return written


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(f"{ingest():,} postcodes loaded")
