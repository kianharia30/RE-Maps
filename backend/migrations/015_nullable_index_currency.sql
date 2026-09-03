-- 015_nullable_index_currency.sql — do not force a currency on index rows.
--
-- `market_indices.currency_code` was NOT NULL DEFAULT 'GBP', which pushed the
-- Eurostat ingester into inventing one: unknown countries were labelled 'EUR',
-- so Hungarian figures appeared in euros. An index has no price level, so the
-- currency is descriptive only — and a wrong one is still a false statement.
ALTER TABLE market_indices ALTER COLUMN currency_code DROP NOT NULL;
ALTER TABLE market_indices ALTER COLUMN currency_code DROP DEFAULT;
ALTER TABLE area_stats ALTER COLUMN currency_code DROP NOT NULL;
