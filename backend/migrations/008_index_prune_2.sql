-- 008_index_prune_2.sql — second index-pruning pass.
--
-- Measured on a 2.3M-row load: these three btree indexes cost 179 MB and are
-- not read by any query in the application.
--
--   transactions_sector_idx    — subsumed by transactions_evidence_sector_idx,
--       which has the same leading columns plus property_type and the
--       evidence-eligibility filter the AVM always applies.
--   transactions_outcode_idx   — the only consumer is ingest/area_stats.py,
--   transactions_district_idx  — which aggregates the whole table with a
--       GROUP BY and is planned as a parallel sequential scan regardless.
--
-- properties_street_idx is kept: it is the lookup for street-tier queries.

DROP INDEX IF EXISTS transactions_sector_idx;
DROP INDEX IF EXISTS transactions_outcode_idx;
DROP INDEX IF EXISTS transactions_district_idx;
