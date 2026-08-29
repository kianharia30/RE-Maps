-- 002_reference.sql — provenance, licensing, coverage, jurisdictions, gazetteer

-- Shared enums ---------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE precision_level AS ENUM (
        'EXACT_TRANSACTION',   -- Level A: a genuine recorded sale
        'PROPERTY_ESTIMATE',   -- Level B: modelled value for one dwelling
        'STREET_POSTCODE',     -- Level C
        'NEIGHBOURHOOD',       -- Level D
        'CITY_REGIONAL',       -- Level E
        'NONE'                 -- Level F: no reliable data
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE coord_precision AS ENUM (
        'PROPERTY',       -- rooftop
        'PARCEL',         -- cadastral parcel (e.g. France DVF)
        'ADDRESS',
        'POSTCODE',       -- postcode centroid — NOT the actual building
        'NEIGHBOURHOOD',
        'REGION'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE price_type AS ENUM (
        'TRANSACTION',
        'HISTORICAL_ESTIMATE',
        'CURRENT_ESTIMATE',
        'FORECAST',
        'REGIONAL_STATISTIC'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE confidence_level AS ENUM ('HIGH','MEDIUM','LOW','VERY_LOW');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Every external dataset, with licence + required attribution. -------------
CREATE TABLE IF NOT EXISTS data_sources (
    id                      serial PRIMARY KEY,
    key                     text NOT NULL UNIQUE,
    name                    text NOT NULL,
    owner                   text NOT NULL,
    url                     text NOT NULL,
    documentation_url       text,
    licence                 text NOT NULL,
    licence_url             text,
    attribution             text NOT NULL,
    allowed_use             text NOT NULL,
    update_frequency        text,
    geographic_coverage     text,
    historical_coverage     text,
    source_published_at     date,
    last_ingested_at        timestamptz,
    record_count            bigint NOT NULL DEFAULT 0,
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- Country polygons for the jurisdiction resolver. --------------------------
CREATE TABLE IF NOT EXISTS countries (
    iso2            char(2) PRIMARY KEY,
    iso3            char(3),
    name            text NOT NULL,
    currency_code   char(3),
    geom            geometry(MultiPolygon, 4326) NOT NULL,
    source_id       integer REFERENCES data_sources(id)
);
CREATE INDEX IF NOT EXISTS countries_geom_gix ON countries USING GIST (geom);

-- Coverage registry (§5). --------------------------------------------------
CREATE TABLE IF NOT EXISTS provider_coverage (
    id                          serial PRIMARY KEY,
    provider_key                text NOT NULL,
    country_iso2                char(2) NOT NULL,
    region_code                 text,
    region_name                 text,
    transaction_level_data      boolean NOT NULL DEFAULT false,
    property_characteristics    boolean NOT NULL DEFAULT false,
    market_index                boolean NOT NULL DEFAULT false,
    forecast_supported          boolean NOT NULL DEFAULT false,
    max_precision               precision_level NOT NULL DEFAULT 'NONE',
    coordinate_precision        coord_precision,
    historical_from             date,
    historical_to               date,
    currency_code               char(3),
    notes                       text,
    source_keys                 text[] NOT NULL DEFAULT '{}',
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider_key, country_iso2, region_code)
);
CREATE INDEX IF NOT EXISTS provider_coverage_country_idx ON provider_coverage (country_iso2);

-- Postcode centroid gazetteer. --------------------------------------------
CREATE TABLE IF NOT EXISTS postcodes (
    id              bigserial PRIMARY KEY,
    country_iso2    char(2) NOT NULL,
    postcode        text NOT NULL,            -- normalised: upper, no spaces
    postcode_pretty text NOT NULL,
    area            text,                     -- MK
    outcode         text,                     -- MK9
    sector          text,                     -- MK9 2
    geom            geometry(Point, 4326) NOT NULL,
    precision       coord_precision NOT NULL DEFAULT 'POSTCODE',
    status          text,
    admin_district  text,
    admin_county    text,
    source_id       integer REFERENCES data_sources(id),
    UNIQUE (country_iso2, postcode)
);
CREATE INDEX IF NOT EXISTS postcodes_geom_gix ON postcodes USING GIST (geom);
CREATE INDEX IF NOT EXISTS postcodes_outcode_idx ON postcodes (country_iso2, outcode);
CREATE INDEX IF NOT EXISTS postcodes_sector_idx ON postcodes (country_iso2, sector);

-- Geocoding cache — keeps us within the Nominatim usage policy. ------------
CREATE TABLE IF NOT EXISTS geocode_cache (
    id              bigserial PRIMARY KEY,
    provider        text NOT NULL,
    kind            text NOT NULL DEFAULT 'search',   -- search | reverse
    query_norm      text NOT NULL,
    payload         jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, kind, query_norm)
);

-- Resumable ingestion bookkeeping (§32). ----------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              bigserial PRIMARY KEY,
    source_key      text NOT NULL,
    unit            text NOT NULL,
    status          text NOT NULL,           -- running | complete | failed
    rows_read       bigint NOT NULL DEFAULT 0,
    rows_written    bigint NOT NULL DEFAULT 0,
    rows_rejected   bigint NOT NULL DEFAULT 0,
    file_bytes      bigint,
    file_signature  text,
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    UNIQUE (source_key, unit)
);
