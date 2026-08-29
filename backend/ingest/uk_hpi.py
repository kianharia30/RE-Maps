"""Ingest the UK House Price Index.

The HPI is what makes honest time-travel possible. It is used for three things:

  1. Index-adjusting comparable sales to the valuation date (§13).
  2. Back-casting a value to a past year the property did not sell in (§12).
  3. Providing the real historical series the forecaster is fitted to (§15).

The published file is wide (one row per area-month, with a block of columns per
market segment). We unpivot it into a long, queryable series.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import mark_ingested, register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "uk_hpi"

# Segment name -> (price column, index column, 1m%, 12m%, volume column)
SEGMENTS: dict[str, tuple[str, str, str, str, str | None]] = {
    "all":           ("AveragePrice", "Index", "1m%Change", "12m%Change", "SalesVolume"),
    "detached":      ("DetachedPrice", "DetachedIndex", "Detached1m%Change", "Detached12m%Change", None),
    "semi_detached": ("SemiDetachedPrice", "SemiDetachedIndex", "SemiDetached1m%Change", "SemiDetached12m%Change", None),
    "terraced":      ("TerracedPrice", "TerracedIndex", "Terraced1m%Change", "Terraced12m%Change", None),
    "flat":          ("FlatPrice", "FlatIndex", "Flat1m%Change", "Flat12m%Change", None),
}

# GSS/ONS area code prefixes tell us the geographic level without a lookup.
#   K = UK-wide,  E92/W92/S92/N92 = country,  E12 = English region,
#   E06/E07/E08/E09/W06/S12/N09 = local authority.
def _area_level(code: str) -> str:
    code = (code or "").strip().upper()
    if code.startswith("K"):
        return "country"
    if code[:3] in {"E92", "W92", "S92", "N92"}:
        return "country"
    if code[:3] == "E12":
        return "region"
    if code[:3] in {"E06", "E07", "E08", "E09", "W06", "S12", "N09"}:
        return "local_authority"
    return "other"


STAGING_DDL = """
DROP TABLE IF EXISTS stg_hpi;
CREATE UNLOGGED TABLE stg_hpi (
    area_code text, area_name text, area_level text, segment text,
    period date, average_price numeric(14,2), index_value numeric(10,3),
    sales_volume integer, pct_1m numeric(8,3), pct_12m numeric(8,3)
);
"""

TRANSFORM = """
INSERT INTO market_indices (
    source_key, country_iso2, area_code, area_name, area_level, segment,
    period, average_price, index_value, sales_volume, pct_change_1m,
    pct_change_12m, currency_code
)
SELECT DISTINCT ON (area_code, segment, period)
    %(source_key)s, 'GB', area_code, area_name, area_level, segment, period,
    average_price, index_value, sales_volume, pct_1m, pct_12m, 'GBP'
FROM stg_hpi
WHERE index_value IS NOT NULL OR average_price IS NOT NULL
ORDER BY area_code, segment, period, area_name
ON CONFLICT (source_key, area_code, segment, period) DO UPDATE SET
    average_price = EXCLUDED.average_price,
    index_value   = EXCLUDED.index_value,
    sales_volume  = EXCLUDED.sales_volume,
    pct_change_1m = EXCLUDED.pct_change_1m,
    pct_change_12m= EXCLUDED.pct_change_12m,
    area_name     = EXCLUDED.area_name;
"""

# ---------------------------------------------------------------------------
# Link Price Paid `district` text to an HPI area code, so the AVM adjusts each
# comparable with its own LOCAL index rather than a national one.
#
# Stage 1 exact match on a normalised name; stage 2 trigram similarity for the
# residual (e.g. "Kingston Upon Hull, City Of" vs "Kingston upon Hull, City of").
# Anything that still fails to match above a similarity floor is left unlinked
# and falls back to the region/national series — never silently mismatched.
# ---------------------------------------------------------------------------
LINK_EXACT = """
INSERT INTO area_index_links (country_iso2, district, area_code, area_name,
                              match_method, match_score)
SELECT DISTINCT ON (d.district)
    'GB', d.district, m.area_code, m.area_name, 'normalised', 1.0
FROM (SELECT DISTINCT district FROM transactions
      WHERE country_iso2='GB' AND district IS NOT NULL) d
JOIN (SELECT DISTINCT area_code, area_name FROM market_indices
      WHERE country_iso2='GB' AND area_level='local_authority') m
  ON lower(regexp_replace(m.area_name, '[^a-zA-Z0-9]', '', 'g'))
   = lower(regexp_replace(d.district, '[^a-zA-Z0-9]', '', 'g'))
ORDER BY d.district, m.area_code
ON CONFLICT (country_iso2, district) DO NOTHING;
"""

LINK_TRIGRAM = """
INSERT INTO area_index_links (country_iso2, district, area_code, area_name,
                              match_method, match_score)
SELECT DISTINCT ON (d.district)
    'GB', d.district, m.area_code, m.area_name, 'trigram', s.score
FROM (SELECT DISTINCT district FROM transactions
      WHERE country_iso2='GB' AND district IS NOT NULL
        AND district NOT IN (SELECT district FROM area_index_links
                             WHERE country_iso2='GB')) d
CROSS JOIN LATERAL (
    SELECT area_code, area_name,
           similarity(lower(area_name), lower(d.district)) AS score
    FROM (SELECT DISTINCT area_code, area_name FROM market_indices
          WHERE country_iso2='GB' AND area_level='local_authority') x
    WHERE similarity(lower(area_name), lower(d.district)) >= 0.55
    ORDER BY score DESC
    LIMIT 1
) m
JOIN LATERAL (SELECT m.score) s ON true
ORDER BY d.district, s.score DESC
ON CONFLICT (country_iso2, district) DO NOTHING;
"""


# ---------------------------------------------------------------------------
# Abolished-district successors.
#
# HM Land Registry Price Paid Data records the district that existed at the
# time of sale, while the UK HPI publishes series for the authorities that
# exist now. English local-government reorganisation therefore leaves a set of
# districts that will never match by name. These are the published successor
# authorities for each — real boundary changes, not guesses:
#
#   2019  Somerset West and Taunton created (Taunton Deane + West Somerset)
#   2021  Northamptonshire districts -> North / West Northamptonshire
#   2023  North Yorkshire, Somerset, Cumberland, and Westmorland and Furness
#         unitary authorities created, absorbing their former districts
#
# "Wrekin" is Price Paid's short form for Telford and Wrekin.
# The Isles of Scilly has its own HPI series under a distinct GSS code.
#
# A district mapped here is index-adjusted with its successor authority's
# series, which is the closest genuine local index available for it.
# ---------------------------------------------------------------------------
DISTRICT_SUCCESSORS: dict[str, str] = {
    # -> North Yorkshire (E06000065)
    "Harrogate": "North Yorkshire",
    "Scarborough": "North Yorkshire",
    "Selby": "North Yorkshire",
    "Hambleton": "North Yorkshire",
    "Craven": "North Yorkshire",
    "Ryedale": "North Yorkshire",
    "Richmondshire": "North Yorkshire",
    # -> Somerset (E06000066)
    "Somerset West And Taunton": "Somerset",
    "Sedgemoor": "Somerset",
    "Mendip": "Somerset",
    "South Somerset": "Somerset",
    "Taunton Deane": "Somerset",
    "West Somerset": "Somerset",
    # -> Cumberland (E06000063)
    "Carlisle": "Cumberland",
    "Allerdale": "Cumberland",
    "Copeland": "Cumberland",
    # -> Westmorland and Furness (E06000064)
    "South Lakeland": "Westmorland and Furness",
    "Barrow In Furness": "Westmorland and Furness",
    "Eden": "Westmorland and Furness",
    # -> North Northamptonshire (E06000061)
    "Kettering": "North Northamptonshire",
    "Wellingborough": "North Northamptonshire",
    "Corby": "North Northamptonshire",
    "East Northamptonshire": "North Northamptonshire",
    # -> West Northamptonshire (E06000062)
    "Daventry": "West Northamptonshire",
    "South Northamptonshire": "West Northamptonshire",
    "Northampton": "West Northamptonshire",
    # naming differences rather than reorganisations
    "Wrekin": "Telford and Wrekin",
    "Isles Of Scilly": "Isles of Scilly",
}

LINK_MANUAL = """
INSERT INTO area_index_links (country_iso2, district, area_code, area_name,
                              match_method, match_score)
SELECT DISTINCT ON (m.district)
    'GB', m.district, x.area_code, x.area_name, 'manual', 1.0
FROM (SELECT unnest(%(districts)s::text[]) AS district,
             unnest(%(successors)s::text[]) AS successor) m
JOIN (SELECT DISTINCT area_code, area_name FROM market_indices
      WHERE country_iso2='GB' AND area_level='local_authority') x
  ON lower(regexp_replace(x.area_name, '[^a-zA-Z0-9]', '', 'g'))
   = lower(regexp_replace(m.successor, '[^a-zA-Z0-9]', '', 'g'))
ORDER BY m.district, x.area_code
ON CONFLICT (country_iso2, district) DO NOTHING;
"""


def _num(value: str) -> float | None:
    v = (value or "").strip()
    if not v or v in {"NA", "N/A", ":", "-"}:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(value: str) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _parse_date(value: str) -> date | None:
    """HPI publishes dd/mm/yyyy; accept ISO too for robustness."""
    v = (value or "").strip()
    m = _DATE_RE.match(v)
    if m:
        d, mo, y = m.groups()
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _find_file(raw_dir: Path) -> Path:
    candidates = sorted(raw_dir.glob("UK-HPI-full-file-*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No UK-HPI-full-file-*.csv in {raw_dir}. Run `make data-download`."
        )
    return candidates[-1]  # most recent release


def ingest(path: Path | None = None) -> int:
    settings = get_settings()
    register_sources()
    path = path or _find_file(settings.raw_dir / "hpi")
    stat = path.stat()
    run_id = start_run(SOURCE_KEY, path.name, stat.st_size)
    read = written = rejected = 0

    try:
        with sync_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(STAGING_DDL)
            conn.commit()

            with conn.cursor() as cur, open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                copy_sql = (
                    "COPY stg_hpi (area_code, area_name, area_level, segment, period, "
                    "average_price, index_value, sales_volume, pct_1m, pct_12m) FROM STDIN"
                )
                with cur.copy(copy_sql) as cp:
                    for row in reader:
                        read += 1
                        period = _parse_date(row.get("Date", ""))
                        code = (row.get("AreaCode") or "").strip()
                        name = (row.get("RegionName") or "").strip()
                        if not period or not code or not name:
                            rejected += 1
                            continue
                        level = _area_level(code)
                        for segment, (c_price, c_index, c_1m, c_12m, c_vol) in SEGMENTS.items():
                            price = _num(row.get(c_price, ""))
                            index_value = _num(row.get(c_index, ""))
                            if price is None and index_value is None:
                                continue
                            cp.write_row(
                                (
                                    code, name, level, segment, period, price,
                                    index_value,
                                    _int(row.get(c_vol, "")) if c_vol else None,
                                    _num(row.get(c_1m, "")),
                                    _num(row.get(c_12m, "")),
                                )
                            )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("ANALYZE stg_hpi")
                cur.execute(TRANSFORM, {"source_key": SOURCE_KEY})
                written = cur.rowcount
                cur.execute("DROP TABLE IF EXISTS stg_hpi")
                cur.execute("ANALYZE market_indices")
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    mark_ingested(SOURCE_KEY, written)
    log.info("UK HPI: read=%s series-rows=%s rejected=%s", read, written, rejected)
    return written


def link_districts() -> dict[str, int]:
    """Build the district -> HPI area code mapping. Requires PPD to be loaded."""
    with sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(LINK_EXACT)
            exact = cur.rowcount
            # Documented successors take precedence over fuzzy matching.
            cur.execute(
                LINK_MANUAL,
                {
                    "districts": list(DISTRICT_SUCCESSORS.keys()),
                    "successors": list(DISTRICT_SUCCESSORS.values()),
                },
            )
            manual = cur.rowcount
            cur.execute(LINK_TRIGRAM)
            trigram = cur.rowcount
            cur.execute(
                "SELECT count(DISTINCT district) AS n FROM transactions "
                "WHERE country_iso2='GB' AND district IS NOT NULL"
            )
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT count(*) AS n FROM area_index_links WHERE country_iso2='GB'"
            )
            linked = cur.fetchone()["n"]
        conn.commit()
    log.info(
        "district->HPI links: exact=%s manual=%s trigram=%s linked=%s/%s districts",
        exact, manual, trigram, linked, total,
    )
    return {"exact": exact, "manual": manual, "trigram": trigram,
            "linked": linked, "districts": total}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(f"{ingest():,} index rows")
