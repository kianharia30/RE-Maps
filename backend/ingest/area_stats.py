"""Precompute the area statistics that drive zoom-dependent map rendering (§9).

Every number written here is a real order statistic (median, quartiles) over
real recorded sales — never a model output. That matters because these are the
figures shown at low zoom, where the UI labels them REGIONAL_STATISTIC.

Two rules keep them honest:

* Only evidence-grade transactions are counted: `market_value_basis =
  'STANDARD'` and `is_residential`.
* Each tier has a **minimum sample size**. An area with fewer sales than its
  threshold gets no row at all, so the map shows nothing rather than a median
  computed from two houses.
"""
from __future__ import annotations

import logging

from app.core.zoom import levels_for_country
from app.db import sync_conn

log = logging.getLogger(__name__)

# (area_level, SQL expression for the area key, display-name expression,
#  minimum transactions required to publish a median)
TIER_SQL: dict[str, tuple[str, str, int]] = {
    "country":  ("t.country_iso2", "c.name",      50),
    "county":   ("t.county",       "t.county",    25),
    "district": ("t.district",     "t.district",  15),
    "outcode":  ("t.outcode",      "t.outcode",    8),
    "sector":   ("t.sector",       "t.sector",     5),
}

SEGMENTS = ["all", "detached", "semi_detached", "terraced", "flat", "house"]

# Resolve each area's local price-index series from the modal district of its
# member sales. Without this, coarser tiers have no district to forecast with
# and fall back to a national growth rate (see migration 011).
LINK_INDEX_SQL = """
WITH modal AS (
    SELECT DISTINCT ON (level_key)
           level_key, district, n
    FROM (
        SELECT {key}::text AS level_key, t.district, count(*) AS n
        FROM transactions t
        WHERE t.country_iso2 = %(country)s
          AND t.market_value_basis = 'STANDARD'
          AND t.is_residential
          AND {key} IS NOT NULL
          AND t.district IS NOT NULL
        GROUP BY 1, 2
    ) counts
    ORDER BY level_key, n DESC, district
)
UPDATE area_stats a
SET index_area_code = l.area_code,
    index_area_name = l.area_name
FROM modal m
JOIN area_index_links l
     ON l.country_iso2 = %(country)s AND l.district = m.district
WHERE a.country_iso2 = %(country)s
  AND a.area_level = %(level)s
  AND a.area_code = m.level_key
  AND a.index_area_code IS DISTINCT FROM l.area_code;
"""

STATS_SQL = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, p25_price, p75_price, mean_price,
    median_price_per_sqm, currency_code, geom, bbox, computed_at
)
SELECT
    t.country_iso2,
    %(level)s,
    {key}::text,
    max({name}::text),
    %(segment)s,
    extract(year FROM t.transaction_date)::smallint,
    count(*)::int,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price),
    percentile_cont(0.25) WITHIN GROUP (ORDER BY t.price),
    percentile_cont(0.75) WITHIN GROUP (ORDER BY t.price),
    avg(t.price),
    percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price_per_sqm)
        FILTER (WHERE t.price_per_sqm IS NOT NULL),
    max(t.currency_code),
    -- Centroid of the member sales: a real, data-derived location for the
    -- marker, not an administrative centroid we would have to source separately.
    ST_Centroid(ST_Collect(t.geom)),
    -- ST_Envelope degenerates to a Point (or LineString) when every member
    -- sale shares one coordinate -- common at postcode-centroid precision,
    -- where a whole small area can collapse to a single point. Guard on the
    -- resulting geometry type rather than on the row count.
    CASE WHEN GeometryType(ST_Envelope(ST_Collect(t.geom))) = 'POLYGON'
         THEN ST_Envelope(ST_Collect(t.geom))::geometry(Polygon,4326) END,
    now()
FROM transactions t
LEFT JOIN countries c ON c.iso2 = t.country_iso2
WHERE t.country_iso2 = %(country)s
  AND t.market_value_basis = 'STANDARD'
  AND t.is_residential
  AND t.geom IS NOT NULL
  AND {key} IS NOT NULL
  AND (%(segment)s = 'all' OR t.property_type = %(segment)s)
GROUP BY t.country_iso2, {key}, extract(year FROM t.transaction_date)
HAVING count(*) >= %(min_count)s
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET
    transaction_count = EXCLUDED.transaction_count,
    median_price = EXCLUDED.median_price,
    p25_price = EXCLUDED.p25_price,
    p75_price = EXCLUDED.p75_price,
    mean_price = EXCLUDED.mean_price,
    median_price_per_sqm = EXCLUDED.median_price_per_sqm,
    area_name = EXCLUDED.area_name,
    geom = EXCLUDED.geom,
    bbox = EXCLUDED.bbox,
    computed_at = now();
"""


def compute(country_iso2: str, levels: list[str] | None = None) -> int:
    levels = levels or levels_for_country(country_iso2)
    total = 0
    with sync_conn() as conn:
        for level in levels:
            if level not in TIER_SQL:
                log.warning("no SQL defined for area level %r — skipping", level)
                continue
            key, name, min_count = TIER_SQL[level]
            sql = STATS_SQL.format(key=key, name=name)
            for segment in SEGMENTS:
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        {
                            "country": country_iso2,
                            "level": level,
                            "segment": segment,
                            "min_count": min_count,
                        },
                    )
                    written = cur.rowcount
                conn.commit()
                total += written
                if written:
                    log.info(
                        "  %s/%s/%s: %s rows", country_iso2, level, segment,
                        f"{written:,}",
                    )
        # Attach the local index series to every tier.
        for level in levels:
            if level not in TIER_SQL:
                continue
            key, _name, _min_count = TIER_SQL[level]
            if level == "country":
                continue          # a country has no more-local index than itself
            with conn.cursor() as cur:
                cur.execute(
                    LINK_INDEX_SQL.format(key=key),
                    {"country": country_iso2, "level": level},
                )
                linked = cur.rowcount
            conn.commit()
            if linked:
                log.info("  %s/%s: linked %s rows to a local index",
                         country_iso2, level, f"{linked:,}")

        with conn.cursor() as cur:
            cur.execute("ANALYZE area_stats")
        conn.commit()
    log.info("area_stats for %s: %s rows", country_iso2, f"{total:,}")
    return total


def compute_all() -> int:
    with sync_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT country_iso2 FROM transactions ORDER BY 1")
        countries = [r["country_iso2"] for r in cur.fetchall()]
    return sum(compute(c) for c in countries)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Precompute map area statistics")
    ap.add_argument("--country", help="ISO2 code; default = all ingested countries")
    args = ap.parse_args()
    print(f"{compute(args.country) if args.country else compute_all():,} rows")
