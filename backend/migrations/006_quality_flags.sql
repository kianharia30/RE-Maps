-- 006_quality_flags.sql — transaction quality flags
--
-- HM Land Registry splits Price Paid Data into two categories:
--   A ("standard price paid")   — a sale at full market value.
--   B ("additional price paid") — repossessions, buy-to-let where it can be
--                                 identified, transfers to non-private
--                                 individuals, and sales at other than full
--                                 market value.
-- HM Land Registry explicitly cautions that category B prices may not reflect
-- market value. They are therefore stored (they are real, recorded sales and
-- the property panel shows them) but EXCLUDED from every derived figure:
-- comparables, AVM inputs and area medians.
--
-- Likewise property type "O" (Other) is largely non-residential; this product
-- is about residential prices, so it is flagged and excluded from statistics.

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS market_value_basis text NOT NULL DEFAULT 'UNKNOWN',
    ADD COLUMN IF NOT EXISTS is_residential     boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN transactions.market_value_basis IS
    'STANDARD = arm''s-length sale at full market value (safe to use as '
    'valuation evidence); ADDITIONAL = repossession/BTL/non-market transfer '
    '(displayed but never used as evidence); UNKNOWN = provider does not say.';

-- The index that every comparable-sales query rides on: it is partial, so only
-- the rows the AVM is allowed to use are in it.
CREATE INDEX IF NOT EXISTS transactions_evidence_gix
    ON transactions USING GIST (geom)
    WHERE market_value_basis = 'STANDARD' AND is_residential;

CREATE INDEX IF NOT EXISTS transactions_evidence_sector_idx
    ON transactions (country_iso2, sector, property_type, transaction_date)
    WHERE market_value_basis = 'STANDARD' AND is_residential;

ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS is_residential boolean NOT NULL DEFAULT true;
