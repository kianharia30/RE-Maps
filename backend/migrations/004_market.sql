-- 004_market.sql — official house-price indices and precomputed area statistics

-- ---------------------------------------------------------------------------
-- market_indices: official published index series (UK HPI etc).
-- One row per (source, area, property segment, month).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_indices (
    id                  bigserial PRIMARY KEY,
    source_key          text NOT NULL,
    country_iso2        char(2) NOT NULL,
    area_code           text NOT NULL,        -- ONS/GSS code, e.g. E06000042
    area_name           text NOT NULL,
    area_level          text NOT NULL,        -- country | region | local_authority
    segment             text NOT NULL,        -- all | detached | semi_detached | terraced | flat
    period              date NOT NULL,        -- first day of month
    average_price       numeric(14,2),
    index_value         numeric(10,3),
    sales_volume        integer,
    pct_change_1m       numeric(8,3),
    pct_change_12m      numeric(8,3),
    currency_code       char(3) NOT NULL DEFAULT 'GBP',
    UNIQUE (source_key, area_code, segment, period)
);
CREATE INDEX IF NOT EXISTS market_indices_lookup_idx
    ON market_indices (country_iso2, area_code, segment, period);
CREATE INDEX IF NOT EXISTS market_indices_name_trgm
    ON market_indices USING GIN (area_name gin_trgm_ops);

-- Maps a transaction's textual district/county onto an HPI area code so the
-- AVM can index-adjust comparables with the correct *local* series.
CREATE TABLE IF NOT EXISTS area_index_links (
    id              bigserial PRIMARY KEY,
    country_iso2    char(2) NOT NULL,
    district        text NOT NULL,
    area_code       text NOT NULL,
    area_name       text NOT NULL,
    match_method    text NOT NULL,        -- exact | normalised | trigram | manual
    match_score     numeric(5,3),
    UNIQUE (country_iso2, district)
);

-- ---------------------------------------------------------------------------
-- area_stats: server-side precomputed aggregation used for zoom-dependent
-- map rendering (§9). Every value here is a real order statistic over real
-- transactions — never a model output.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS area_stats (
    id                  bigserial PRIMARY KEY,
    country_iso2        char(2) NOT NULL,
    area_level          text NOT NULL,   -- country|county|district|outcode|sector
    area_code           text NOT NULL,   -- the key at that level
    area_name           text NOT NULL,
    segment             text NOT NULL DEFAULT 'all',
    year                smallint NOT NULL,

    transaction_count   integer NOT NULL,
    median_price        numeric(14,2) NOT NULL,
    p25_price           numeric(14,2),
    p75_price           numeric(14,2),
    mean_price          numeric(14,2),
    median_price_per_sqm numeric(12,2),
    currency_code       char(3) NOT NULL,

    geom                geometry(Point, 4326),   -- centroid of member sales
    bbox                geometry(Polygon, 4326),
    computed_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (country_iso2, area_level, area_code, segment, year)
);
CREATE INDEX IF NOT EXISTS area_stats_geom_gix ON area_stats USING GIST (geom);
CREATE INDEX IF NOT EXISTS area_stats_tier_idx
    ON area_stats (country_iso2, area_level, year, segment);
CREATE INDEX IF NOT EXISTS area_stats_growth_idx
    ON area_stats (area_level, area_code, segment, year);
