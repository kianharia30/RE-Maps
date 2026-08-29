-- 011_area_stats_index_link.sql — give every aggregated area its own local
-- price-index series.
--
-- WHY
-- The future-year map multiplies an area's real median by a forecast market
-- movement. The forecast is resolved from a district name, but only the
-- `district` tier actually has one: at outcode, sector and county level the
-- lookup had nothing to pass and silently fell back to the NATIONAL series.
-- Every outcode in the country would then be projected with the same UK growth
-- rate, which is a national average dressed up as a local figure — exactly what
-- the product must not do.
--
-- Storing the resolved index area on each `area_stats` row fixes it once, at
-- computation time, so the map can forecast each area with its own local index.

ALTER TABLE area_stats
    ADD COLUMN IF NOT EXISTS index_area_code text,
    ADD COLUMN IF NOT EXISTS index_area_name text;

COMMENT ON COLUMN area_stats.index_area_code IS
    'The local price-index series to project this area with, resolved from the '
    'modal district of its member sales. NULL means no local index could be '
    'found, in which case no forecast is offered for this area.';

CREATE INDEX IF NOT EXISTS area_stats_index_area_idx
    ON area_stats (index_area_code)
    WHERE index_area_code IS NOT NULL;
