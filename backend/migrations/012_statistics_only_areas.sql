-- 012_statistics_only_areas.sql — support areas that have a growth rate but
-- NO price level.
--
-- WHY THIS IS NEEDED
-- Eurostat (31 European countries) and the US FHFA publish a house price
-- *index* — a series based at 100 in a reference year. An index tells you that
-- prices rose 4.2% last year. It does NOT tell you that a house costs
-- EUR 380,000. Deriving a level from an index would require a base-year price
-- we do not have, and inventing one would be fabrication.
--
-- So `area_stats.median_price` becomes nullable. An area may now carry:
--   * a real median price (derived from real recorded sales), and/or
--   * an index level and a growth rate (from an official index series).
--
-- The API exposes `has_price_level` so the map can render a growth chip
-- ("+4.2%/yr") where that is all that honestly exists, and a price only where
-- a price genuinely does.

ALTER TABLE area_stats
    ALTER COLUMN median_price DROP NOT NULL;

ALTER TABLE area_stats
    ADD COLUMN IF NOT EXISTS index_value    numeric(10,3),
    ADD COLUMN IF NOT EXISTS growth_1y_pct  numeric(8,3),
    ADD COLUMN IF NOT EXISTS source_key     text,
    -- What kind of evidence stands behind this row. Kept explicit rather than
    -- inferred from which columns are null, so the API never has to guess.
    ADD COLUMN IF NOT EXISTS basis          text NOT NULL DEFAULT 'TRANSACTIONS';

COMMENT ON COLUMN area_stats.median_price IS
    'Median of real recorded sale prices. NULL for areas covered only by an '
    'official index, which carries no price level.';
COMMENT ON COLUMN area_stats.basis IS
    'TRANSACTIONS = order statistics over real recorded sales. '
    'OFFICIAL_INDEX = an official published index series; growth is real but '
    'there is no price level.';

-- A row must carry at least one real figure, or it has no business existing.
ALTER TABLE area_stats
    DROP CONSTRAINT IF EXISTS area_stats_has_a_figure;
ALTER TABLE area_stats
    ADD CONSTRAINT area_stats_has_a_figure
    CHECK (median_price IS NOT NULL OR index_value IS NOT NULL);

-- Transaction-count semantics differ by basis: for an index row it is the
-- number of observations behind the published figure, which the publisher may
-- not disclose. Allow zero there, but never for a transaction-derived median.
ALTER TABLE area_stats
    DROP CONSTRAINT IF EXISTS area_stats_transaction_basis_needs_sales;
ALTER TABLE area_stats
    ADD CONSTRAINT area_stats_transaction_basis_needs_sales
    CHECK (basis <> 'TRANSACTIONS' OR (median_price IS NOT NULL AND transaction_count > 0));

CREATE INDEX IF NOT EXISTS area_stats_basis_idx ON area_stats (country_iso2, basis);
