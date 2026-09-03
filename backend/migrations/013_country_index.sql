-- 013_country_index.sql — index `transactions.country_iso2`.
--
-- Low cardinality (two values today), which normally argues against an index.
-- It earns its place because the queries that need it ask "does this country
-- have ANY transactions?" for every country we hold an index series for.
-- Without it, registering coverage for 31 statistics-only countries ran 31
-- sequential scans over 5M rows and did not finish in seven minutes.
CREATE INDEX IF NOT EXISTS transactions_country_idx ON transactions (country_iso2);
