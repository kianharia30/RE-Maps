-- 003_properties.sql — normalised properties and transactions

-- ---------------------------------------------------------------------------
-- properties: one row per distinct dwelling we can identify.
--   property_key is a deterministic, normalised address identity (see
--   ingest/address.py) so that "12 High Street" / "12 HIGH STREET" /
--   "Flat 2, 12 High St" resolve correctly instead of duplicating (§52).
--   Where an authoritative external identifier exists we keep it too.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS properties (
    id                  bigserial PRIMARY KEY,
    country_iso2        char(2) NOT NULL,
    property_key        text    NOT NULL,
    external_id         text,                  -- authoritative id if any (e.g. FR id_parcelle)

    -- address components (normalised, display form)
    address_line        text,
    saon                text,                  -- secondary addressable object (flat/apt)
    paon                text,                  -- primary addressable object (house no/name)
    street              text,
    locality            text,
    town                text,
    district            text,
    county              text,
    postcode            text,
    postcode_norm       text,

    -- geometry + honest precision (§33)
    geom                geometry(Point, 4326),
    coordinate_precision coord_precision,

    -- characteristics (may be NULL — not every provider supplies these)
    property_type       text,                  -- detached | semi_detached | terraced | flat | other
    tenure              text,                  -- freehold | leasehold
    new_build_at_sale   boolean,
    bedrooms            smallint,
    bathrooms           smallint,
    floor_area_sqm      numeric(10,2),
    lot_area_sqm        numeric(12,2),
    year_built          smallint,
    habitable_rooms     smallint,

    -- provenance
    source_keys         text[] NOT NULL DEFAULT '{}',
    characteristics_source text,
    first_seen          date,
    last_seen           date,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (country_iso2, property_key)
);
CREATE INDEX IF NOT EXISTS properties_geom_gix     ON properties USING GIST (geom);
CREATE INDEX IF NOT EXISTS properties_postcode_idx ON properties (country_iso2, postcode_norm);
CREATE INDEX IF NOT EXISTS properties_street_idx   ON properties (country_iso2, postcode_norm, street);
CREATE INDEX IF NOT EXISTS properties_type_idx     ON properties (property_type);

-- ---------------------------------------------------------------------------
-- transactions: genuine recorded sales. Never modelled values.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id                  bigserial PRIMARY KEY,
    property_id         bigint REFERENCES properties(id) ON DELETE CASCADE,
    country_iso2        char(2) NOT NULL,
    source_key          text NOT NULL,
    source_record_id    text NOT NULL,          -- dedup anchor (§32)

    transaction_date    date        NOT NULL,
    price               numeric(14,2) NOT NULL,
    currency_code       char(3)     NOT NULL,   -- ORIGINAL currency, never converted

    property_type       text,
    tenure              text,
    new_build           boolean,
    floor_area_sqm      numeric(10,2),
    rooms               smallint,

    postcode_norm       text,
    outcode             text,
    sector              text,
    street              text,
    town                text,
    district            text,
    county              text,

    geom                geometry(Point, 4326),
    coordinate_precision coord_precision,

    price_per_sqm       numeric(12,2) GENERATED ALWAYS AS (
                            CASE WHEN floor_area_sqm > 0
                                 THEN price / floor_area_sqm END) STORED,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_key, source_record_id)
);
CREATE INDEX IF NOT EXISTS transactions_geom_gix    ON transactions USING GIST (geom);
CREATE INDEX IF NOT EXISTS transactions_date_brin   ON transactions USING BRIN (transaction_date);
CREATE INDEX IF NOT EXISTS transactions_property_idx ON transactions (property_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS transactions_sector_idx  ON transactions (country_iso2, sector, transaction_date);
CREATE INDEX IF NOT EXISTS transactions_outcode_idx ON transactions (country_iso2, outcode, transaction_date);
CREATE INDEX IF NOT EXISTS transactions_district_idx ON transactions (country_iso2, district, transaction_date);
-- Composite index that drives the comparable-sales search: geography +
-- property type + date, so the AVM never falls back to a sequential scan.
CREATE INDEX IF NOT EXISTS transactions_comparable_idx
    ON transactions USING GIST (geom, property_type, transaction_date);
