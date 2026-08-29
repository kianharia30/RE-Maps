-- 010_coverage_unique_nulls.sql — make the coverage upsert genuinely idempotent.
--
-- THE BUG
-- `provider_coverage` had UNIQUE (provider_key, country_iso2, region_code), and
-- the country-wide rows carry region_code = NULL. In SQL two NULLs are never
-- equal, so the constraint did not apply to exactly the rows that matter most:
-- every run of `ingest/coverage.py` appended another country-wide row instead
-- of updating the existing one. `ON CONFLICT` had nothing to conflict with.
--
-- Symptom: three duplicate GB rows and two duplicate FR rows, one of them
-- stale (FR showed market_index = false from before the derived index was
-- built). `coverage.lookup()` uses LIMIT 1, so which row won was arbitrary.
--
-- THE FIX
-- PostgreSQL 15+ supports UNIQUE NULLS NOT DISTINCT, which treats NULLs as
-- equal for uniqueness. That is exactly the semantics wanted here: one row per
-- (provider, country, region), with NULL meaning "the whole country".

-- Keep the most recently updated row per key, drop the rest.
DELETE FROM provider_coverage pc
USING provider_coverage newer
WHERE pc.provider_key = newer.provider_key
  AND pc.country_iso2 = newer.country_iso2
  AND pc.region_code IS NOT DISTINCT FROM newer.region_code
  AND (pc.updated_at, pc.id) < (newer.updated_at, newer.id);

ALTER TABLE provider_coverage
    DROP CONSTRAINT IF EXISTS provider_coverage_provider_key_country_iso2_region_code_key;

DROP INDEX IF EXISTS provider_coverage_identity_key;

ALTER TABLE provider_coverage
    ADD CONSTRAINT provider_coverage_identity_key
    UNIQUE NULLS NOT DISTINCT (provider_key, country_iso2, region_code);
