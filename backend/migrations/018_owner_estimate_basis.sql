-- 018_owner_estimate_basis.sql — record when a "price" is an owner's estimate.
--
-- The US Census ACS variable B25077 is "Median value (dollars)" for
-- owner-occupied housing units. It is NOT a transaction price. The American
-- Community Survey asks the occupant what they believe the property would sell
-- for, and the published median is the middle of those self-reported
-- estimates. It is an official, widely used statistic — and it is a different
-- kind of thing from a recorded sale.
--
-- Dublin's EUR 440,000 is the middle of prices people actually paid. A US
-- county figure is the middle of what owners think their homes are worth.
-- Owner estimates are known to run above market outcomes, and they cover the
-- whole owner-occupied stock rather than the subset that changed hands. Shown
-- side by side with no distinction, the two invite a comparison neither
-- supports.
--
-- So `basis` gains a fourth value and the map labels it "est. value" rather
-- than "median".
ALTER TABLE area_stats
    DROP CONSTRAINT IF EXISTS area_stats_basis_known;
ALTER TABLE area_stats
    ADD CONSTRAINT area_stats_basis_known
    CHECK (basis IN (
        'TRANSACTIONS',       -- order statistic over individual recorded sales
        'OFFICIAL_STATISTIC', -- an aggregate published by a statistics office
        'OWNER_ESTIMATE',     -- self-reported value, not a sale
        'OFFICIAL_INDEX'      -- an index; no price level at all
    ));

COMMENT ON COLUMN area_stats.basis IS
    'What stands behind the figure. TRANSACTIONS = computed from individual '
    'recorded sales. OFFICIAL_STATISTIC = an aggregate published by a '
    'statistics office. OWNER_ESTIMATE = self-reported value of the standing '
    'stock, not a sale price. OFFICIAL_INDEX = an index with no price level.';
