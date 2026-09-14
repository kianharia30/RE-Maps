-- 017_undisclosed_sample_size.sql — distinguish "not disclosed" from "none".
--
-- `transaction_count` was NOT NULL, so a source that publishes a price without
-- a sample size had to be recorded as 0 sales. Zero means "no sales happened",
-- which is a different and false claim.
--
-- Statistics Netherlands publishes an average purchase price per province with
-- no accompanying count; Eurostat and the FHFA publish index levels the same
-- way. NULL now means "the publisher does not disclose it", and the API
-- reports it as unknown rather than as zero.
ALTER TABLE area_stats ALTER COLUMN transaction_count DROP NOT NULL;

-- Rows previously forced to 0 by the constraint were index rows, where the
-- sample size is genuinely undisclosed. Correct them to NULL so the two cases
-- are not conflated.
UPDATE area_stats
SET transaction_count = NULL
WHERE transaction_count = 0 AND basis <> 'TRANSACTIONS';

COMMENT ON COLUMN area_stats.transaction_count IS
    'Sales behind the figure. NULL where the publisher does not disclose it '
    '(an official index or a published average), never 0 — zero would assert '
    'that no sales took place.';

-- A transaction-derived median must still have a real, positive sample.
ALTER TABLE area_stats
    DROP CONSTRAINT IF EXISTS area_stats_transaction_basis_needs_sales;
ALTER TABLE area_stats
    ADD CONSTRAINT area_stats_transaction_basis_needs_sales
    CHECK (
        basis <> 'TRANSACTIONS'
        OR (median_price IS NOT NULL AND transaction_count > 0)
    );
