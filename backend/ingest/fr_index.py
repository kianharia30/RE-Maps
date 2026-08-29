"""Derive a local price index for France from the DVF transactions themselves.

Why this is needed
------------------
Index adjustment is what makes a historical estimate defensible: a 2021 sale is
only evidence about 2023 if you can move it through the local market. For the
UK that comes from the official UK House Price Index. France publishes the
Notaires-INSEE indices, but they are not available as an open bulk download in
the same way, so RE-Maps derives its own from the DVF data it already holds.

What it is, stated plainly
--------------------------
A **median price per square metre**, by department and property type, per
quarter, computed over the same real transactions the map shows.

What it is NOT
--------------
It is not mix-adjusted or hedonic. If the mix of properties sold in a
department shifts (a quarter with unusually many small city-centre flats), the
index moves even when no individual property changed value. That is a genuine
weakness, and it is why:

  * the source is registered separately as `fr_dvf_derived`, never presented as
    an official index,
  * a minimum sale count per quarter is required before a point is published,
  * France's coverage entry states that its index is derived, and
  * forecasting stays disabled for France — three years of a mix-unadjusted
    index is nowhere near enough to fit a trend model to.
"""
from __future__ import annotations

import logging

from app.db import sync_conn

from .sources import register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "fr_dvf_derived"
DERIVED_SOURCE = {
    "key": SOURCE_KEY,
    "name": "RE-Maps derived French price-per-m² index (from geo-DVF)",
    "owner": "RE-Maps (derived), from DGFiP/Etalab source data",
    "url": "https://files.data.gouv.fr/geo-dvf/latest/csv/",
    "documentation_url": "https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/",
    "licence": "Licence Ouverte / Open Licence 2.0 (Etalab) — derived work",
    "licence_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
    "attribution": (
        "Derived index computed by RE-Maps from Direction générale des "
        "Finances publiques (DVF) data géolocalisées par Etalab, Licence "
        "Ouverte 2.0. Not an official index."
    ),
    "allowed_use": (
        "Internal use for index-adjusting comparable sales. NOT an official "
        "house price index: it is a median price per square metre and is not "
        "mix-adjusted, so it moves when the composition of sales changes."
    ),
    "update_frequency": "Recomputed whenever geo-DVF is re-ingested",
    "geographic_coverage": "The French departments present in the database",
    "historical_coverage": "Derived from the ingested DVF years",
    "source_published_at": None,
}

# Minimum sales in a department-quarter before a median is published. Below
# this the median is noise, and an unstable index would corrupt every
# adjustment that used it.
MIN_SALES_PER_QUARTER = 30

# The index is normalised to 100 at each series' own first published quarter,
# matching how the UK HPI is expressed so `index_adjust` needs no special case.
BUILD_SQL = """
WITH quarterly AS (
    SELECT t.county AS dept,
           date_trunc('quarter', t.transaction_date)::date AS period,
           CASE WHEN %(segment)s = 'all' THEN 'all' ELSE t.property_type END AS segment,
           count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price_per_sqm) AS median_ppsm,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price) AS median_price
    FROM transactions t
    WHERE t.country_iso2 = 'FR'
      AND t.market_value_basis = 'STANDARD'
      AND t.is_residential
      AND t.price_per_sqm IS NOT NULL
      AND t.county IS NOT NULL
      AND (%(segment)s = 'all' OR t.property_type = %(segment)s)
    GROUP BY 1, 2, 3
    HAVING count(*) >= %(min_sales)s
),
based AS (
    SELECT q.*,
           first_value(q.median_ppsm) OVER (
               PARTITION BY q.dept, q.segment ORDER BY q.period
           ) AS base_ppsm
    FROM quarterly q
),
-- The index is quarterly, but `index_adjust` looks up months. Expanding each
-- quarter to its three months keeps that lookup exact instead of relying on
-- the module's nearest-month tolerance.
monthly AS (
    SELECT b.dept, b.segment, b.n, b.median_price, b.median_ppsm, b.base_ppsm,
           (b.period + (offs || ' month')::interval)::date AS period
    FROM based b
    CROSS JOIN generate_series(0, 2) AS offs
)
INSERT INTO market_indices (
    source_key, country_iso2, area_code, area_name, area_level, segment,
    period, average_price, index_value, sales_volume, currency_code
)
SELECT %(source_key)s, 'FR', 'FR-' || m.dept, 'Département ' || m.dept,
       'local_authority', m.segment, m.period, m.median_price,
       100.0 * m.median_ppsm / NULLIF(m.base_ppsm, 0), m.n, 'EUR'
FROM monthly m
WHERE m.base_ppsm > 0
ON CONFLICT (source_key, area_code, segment, period) DO UPDATE SET
    average_price = EXCLUDED.average_price,
    index_value   = EXCLUDED.index_value,
    sales_volume  = EXCLUDED.sales_volume;
"""

# Map each French commune to its department's derived series, so the AVM
# adjusts with the most local index available for France.
LINK_SQL = """
INSERT INTO area_index_links (country_iso2, district, area_code, area_name,
                              match_method, match_score)
SELECT DISTINCT ON (t.district)
    'FR', t.district, 'FR-' || t.county, 'Département ' || t.county,
    'manual', 1.0
FROM transactions t
WHERE t.country_iso2 = 'FR' AND t.district IS NOT NULL AND t.county IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM market_indices m
      WHERE m.area_code = 'FR-' || t.county AND m.source_key = %(source_key)s
  )
ORDER BY t.district, t.county
ON CONFLICT (country_iso2, district) DO NOTHING;
"""

SEGMENTS = ["all", "house", "flat"]


def build() -> dict[str, int]:
    # Register the derived source so nothing it produces can be displayed
    # without provenance.
    from .sources import SOURCES

    if not any(src["key"] == SOURCE_KEY for src in SOURCES):
        SOURCES.append(DERIVED_SOURCE)
    register_sources()

    written = links = 0
    with sync_conn() as conn:
        for segment in SEGMENTS:
            with conn.cursor() as cur:
                cur.execute(
                    BUILD_SQL,
                    {
                        "segment": segment,
                        "min_sales": MIN_SALES_PER_QUARTER,
                        "source_key": SOURCE_KEY,
                    },
                )
                n = cur.rowcount
            conn.commit()
            written += n
            log.info("  FR derived index / %s: %s monthly rows", segment, f"{n:,}")

        with conn.cursor() as cur:
            cur.execute(LINK_SQL, {"source_key": SOURCE_KEY})
            links = cur.rowcount
            cur.execute("ANALYZE market_indices")
            cur.execute("ANALYZE area_index_links")
        conn.commit()

    log.info("FR derived index: %s rows, %s commune links", f"{written:,}", f"{links:,}")
    return {"rows": written, "links": links}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(build())
