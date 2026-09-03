-- 014_regions.sql — sub-national administrative polygons.
--
-- WHY
-- An area statistic needs a shape, not just a point. A single stored point
-- makes a large area's figure invisible unless the viewport happens to contain
-- that exact coordinate: at neighbourhood zoom over Los Angeles, California's
-- point (36.7N, 118.8W) lay outside the view, so the map fell back to the US
-- NATIONAL figure and Californian data appeared not to exist.
--
-- With polygons we can do for regions what migration 012's country query does
-- for countries: select regions whose shape intersects the viewport, and place
-- the marker inside the visible part. The figure is then always reachable and
-- always sits within the area it describes.
--
-- Source: Natural Earth admin-1 states/provinces (10m), public domain. 4,596
-- regions worldwide, so this also prepares the ground for sub-national
-- coverage in other countries.

CREATE TABLE IF NOT EXISTS regions (
    id              bigserial PRIMARY KEY,
    country_iso2    char(2) NOT NULL,
    -- ISO 3166-2 where Natural Earth supplies it (e.g. 'US-NY').
    iso_3166_2      text,
    -- Postal/short code. For US states this equals FHFA's `place_id`, which is
    -- what lets an index series be joined to its shape.
    postal_code     text,
    name            text NOT NULL,
    name_local      text,
    region_type     text,
    geom            geometry(MultiPolygon, 4326) NOT NULL,
    source_id       integer REFERENCES data_sources(id)
);

CREATE INDEX IF NOT EXISTS regions_geom_gix ON regions USING GIST (geom);
CREATE INDEX IF NOT EXISTS regions_country_idx ON regions (country_iso2);
CREATE INDEX IF NOT EXISTS regions_postal_idx ON regions (country_iso2, postal_code);
CREATE UNIQUE INDEX IF NOT EXISTS regions_identity_key
    ON regions (country_iso2, coalesce(iso_3166_2, ''), name);

COMMENT ON TABLE regions IS
    'Sub-national administrative polygons (Natural Earth admin-1, public '
    'domain). Used to give area statistics a shape so they can be clipped '
    'into the viewport, and to resolve a point to a region.';

-- Link an area statistic to its shape, so the map can clip it into view.
ALTER TABLE area_stats
    ADD COLUMN IF NOT EXISTS region_id bigint REFERENCES regions(id);

CREATE INDEX IF NOT EXISTS area_stats_region_idx ON area_stats (region_id)
    WHERE region_id IS NOT NULL;
