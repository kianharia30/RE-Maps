"""SQL for the map endpoints.

Kept apart from the route so `map.py` reads as control flow rather than as a
wall of SQL. These are queries, not an ORM layer: the map's cost is almost
entirely spatial filtering and ordering, and expressing that directly in
PostGIS is both clearer and faster than assembling it through a query builder.

Every query here obeys the same two rules:

  * a row without `median_price` is never returned — a growth rate is not a
    price, and the map shows money or nothing;
  * one row per area, preferring real recorded sales over a published
    aggregate, so an area that has both does not appear twice.
"""
from __future__ import annotations

_COUNTRY_AREA_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326) AS g
),
-- ONE row per country. A country can legitimately hold both a real
-- transaction-derived median AND an official index for the same year: France
-- has recorded sales for 2021-2023 and a Eurostat series throughout, which
-- produced two markers for France on the same map. Real recorded sales always
-- win over an index, because a measured price beats a measure of change.
picked AS (
    SELECT DISTINCT ON (a.country_iso2)
           a.area_level, a.area_code, a.area_name,
           ST_Y(ST_PointOnSurface(ST_Intersection(c.geom, box.g))) AS latitude,
           ST_X(ST_PointOnSurface(ST_Intersection(c.geom, box.g))) AS longitude,
           a.year, a.median_price::float8 AS median_price,
           a.p25_price::float8 AS p25_price, a.p75_price::float8 AS p75_price,
           a.median_price_per_sqm::float8 AS median_price_per_sqm,
           a.index_value::float8 AS index_value, a.basis, a.price_statistic,
           a.growth_1y_pct::float8 AS stored_growth,
           a.transaction_count, a.currency_code AS currency,
           a.index_area_code, a.index_area_name,
           NULL::float8 AS prev_median,
           ST_Area(ST_Intersection(c.geom, box.g)) AS visible_area
    FROM area_stats a
    JOIN countries c ON c.iso2 = a.country_iso2
    CROSS JOIN box
    -- Deliberately NOT filtered to the single resolved country. At world and
    -- continental zoom the viewport spans many countries, and showing only the
    -- one under the viewport centre made the other covered countries look
    -- absent.
    WHERE a.area_level = 'country'
      AND a.segment = %(segment)s
  -- A real monetary figure or nothing (see above).
  AND a.median_price IS NOT NULL
      -- A real monetary figure or nothing. An index-only row carries
      -- a growth rate and no price, and is not shown.
      AND a.median_price IS NOT NULL
      AND a.year = %(year)s
      AND ST_Intersects(c.geom, box.g)
      AND GeometryType(ST_Intersection(c.geom, box.g)) LIKE '%%POLYGON'
    ORDER BY a.country_iso2,
             (a.basis = 'TRANSACTIONS') DESC,
             a.transaction_count DESC
)
SELECT * FROM picked
-- Largest visible country first, so a LIMIT drops slivers at the map edge
-- rather than the country the viewer is actually looking at.
ORDER BY visible_area DESC
LIMIT %(limit)s
"""


_REGION_AREA_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326) AS g
),
-- DISTINCT ON dictates the sort order, so the pick and the presentation order
-- are separated: choose one row per region here, then order by how much of
-- each region is actually on screen below. Without that split the "first"
-- area was whichever had the lowest region_id -- a viewport over Dublin led
-- with Louth.
picked AS (
    SELECT DISTINCT ON (a.region_id)
           a.area_level, a.area_code, a.area_name,
           ST_Y(ST_PointOnSurface(ST_Intersection(r.geom, box.g))) AS latitude,
           ST_X(ST_PointOnSurface(ST_Intersection(r.geom, box.g))) AS longitude,
           a.year, a.median_price::float8 AS median_price,
           a.p25_price::float8 AS p25_price, a.p75_price::float8 AS p75_price,
           a.median_price_per_sqm::float8 AS median_price_per_sqm,
           a.index_value::float8 AS index_value, a.basis, a.price_statistic,
           a.growth_1y_pct::float8 AS stored_growth,
           a.transaction_count, a.currency_code AS currency,
           a.index_area_code, a.index_area_name,
           NULL::float8 AS prev_median,
           ST_Area(ST_Intersection(r.geom, box.g)) AS visible_area
    FROM area_stats a
    JOIN regions r ON r.id = a.region_id
    CROSS JOIN box
    WHERE a.country_iso2 = %(country)s
      AND a.area_level = %(level)s
      AND a.segment = %(segment)s
  -- A real monetary figure or nothing (see above).
  AND a.median_price IS NOT NULL
      -- A real monetary figure or nothing. An index-only row carries
      -- a growth rate and no price, and is not shown.
      AND a.median_price IS NOT NULL
      AND a.year = %(year)s
      AND ST_Intersects(r.geom, box.g)
      AND GeometryType(ST_Intersection(r.geom, box.g)) LIKE '%%POLYGON'
    -- Real recorded sales beat an official index for the same region.
    ORDER BY a.region_id, (a.basis = 'TRANSACTIONS') DESC,
             a.transaction_count DESC
)
SELECT * FROM picked
ORDER BY visible_area DESC
LIMIT %(limit)s
"""


_AREA_SQL = """
SELECT a.area_level, a.area_code, a.area_name,
       ST_Y(a.geom) AS latitude, ST_X(a.geom) AS longitude,
       a.year, a.median_price::float8 AS median_price,
       a.p25_price::float8 AS p25_price, a.p75_price::float8 AS p75_price,
       a.median_price_per_sqm::float8 AS median_price_per_sqm,
       a.index_value::float8 AS index_value, a.basis, a.price_statistic,
       a.growth_1y_pct::float8 AS stored_growth,
       a.transaction_count, a.currency_code AS currency,
       a.index_area_code, a.index_area_name,
       prev.median_price::float8 AS prev_median
FROM area_stats a
LEFT JOIN area_stats prev
       ON prev.country_iso2 = a.country_iso2
      AND prev.area_level = a.area_level
      AND prev.area_code = a.area_code
      AND prev.segment = a.segment
      AND prev.year = a.year - 1
WHERE a.country_iso2 = %(country)s
  AND a.area_level = %(level)s
  AND a.segment = %(segment)s
  -- A real monetary figure or nothing (see above).
  AND a.median_price IS NOT NULL
  AND a.year = %(year)s
  AND a.geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
ORDER BY a.transaction_count DESC
LIMIT %(limit)s
"""


_LIVE_SQL = """
SELECT %(level)s AS area_level,
       {key}::text AS area_code,
       max({name}::text) AS area_name,
       ST_Y(ST_Centroid(ST_Collect(t.geom))) AS latitude,
       ST_X(ST_Centroid(ST_Collect(t.geom))) AS longitude,
       %(year)s::int AS year,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price)::float8 AS median_price,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY t.price)::float8 AS p25_price,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY t.price)::float8 AS p75_price,
       (percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price_per_sqm)
            FILTER (WHERE t.price_per_sqm IS NOT NULL))::float8 AS median_price_per_sqm,
       count(*)::int AS transaction_count,
       max(t.currency_code) AS currency,
       NULL::float8 AS index_value, 'TRANSACTIONS'::text AS basis,
       'MEDIAN'::text AS price_statistic,
       NULL::float8 AS stored_growth,
       -- Live tiers resolve their index from the modal district in the group.
       (SELECT l.area_code FROM area_index_links l
         WHERE l.country_iso2 = %(country)s AND l.district = mode() WITHIN GROUP (ORDER BY t.district)
       ) AS index_area_code,
       mode() WITHIN GROUP (ORDER BY t.district) AS index_area_name,
       NULL::float8 AS prev_median
FROM transactions t
WHERE t.country_iso2 = %(country)s
  AND t.market_value_basis = 'STANDARD'
  AND t.is_residential
  AND t.geom IS NOT NULL
  AND {key} IS NOT NULL
  AND t.geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
  AND t.transaction_date >= %(date_from)s
  AND t.transaction_date <= %(date_to)s
  AND (%(segment)s = 'all' OR t.property_type = %(segment)s)
GROUP BY {key}
HAVING count(*) >= %(min_count)s
ORDER BY count(*) DESC
LIMIT %(limit)s
"""


_PROPERTY_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326) AS g
),
in_view AS (
    SELECT p.id, p.address_line, p.postcode, p.property_type, p.postcode_norm,
           p.district, p.coordinate_precision, p.geom,
           ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon,
           p.floor_area_sqm::float8 AS floor_area_sqm,
           (SELECT count(*) FROM transactions t2 WHERE t2.property_id = p.id) AS txn_count,
           (SELECT max(t3.transaction_date) FROM transactions t3
             WHERE t3.property_id = p.id) AS last_sold
    FROM properties p, box
    WHERE p.country_iso2 = %(country)s
      AND p.is_residential
      AND p.geom IS NOT NULL
      AND p.geom && box.g
      AND (%(segment)s = 'all' OR p.property_type = %(segment)s)
      AND EXISTS (SELECT 1 FROM transactions t WHERE t.property_id = p.id)
    LIMIT %(scan_limit)s
),
thinned AS (
    -- One dwelling per screen-space grid cell, best-evidenced first.
    SELECT DISTINCT ON (ST_SnapToGrid(geom, %(cell_x)s, %(cell_y)s))
           *
    FROM in_view
    ORDER BY ST_SnapToGrid(geom, %(cell_x)s, %(cell_y)s),
             txn_count DESC, last_sold DESC NULLS LAST, id
),
ranked AS (
    SELECT t.*,
           ROW_NUMBER() OVER (PARTITION BY t.postcode_norm ORDER BY t.id) - 1 AS slot,
           COUNT(*)    OVER (PARTITION BY t.postcode_norm)                   AS in_postcode
    FROM thinned t
)
SELECT ranked.*,
       -- Ring radius grows with how many dwellings share the centroid, capped
       -- at ~40 m so a marker never leaves its own postcode.
       CASE WHEN in_postcode > 1
            THEN LEAST(0.00036, 0.00010 * sqrt(in_postcode))
            ELSE 0 END AS ring_deg,
       (SELECT count(*) FROM in_view) AS total_in_view
FROM ranked
ORDER BY txn_count DESC, id
LIMIT %(limit)s
"""
