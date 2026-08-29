-- 007_index_pruning.sql — remove redundant indexes on `transactions`.
--
-- Measured during the initial bulk load: three overlapping spatial indexes on
-- the same column tripled write amplification (checkpoints every 29 s, each
-- taking 26-77 s) without improving any query plan.
--
--   transactions_comparable_idx  — multi-column GIST (geom, property_type,
--       transaction_date). PostGIS cannot use the trailing btree_gist columns
--       to accelerate a ST_DistanceSphere radius search, so the leading geom
--       key was doing all the work and duplicating transactions_geom_gix.
--   transactions_evidence_gix    — partial GIST on geom for evidence-eligible
--       rows. ~85% of rows qualify, so it duplicated transactions_geom_gix
--       almost entirely; the residual filter is a cheap recheck instead.
--
-- Kept: transactions_geom_gix (all map + comparable searches ride this),
-- the BRIN date index, and the btree area/property indexes.

DROP INDEX IF EXISTS transactions_comparable_idx;
DROP INDEX IF EXISTS transactions_evidence_gix;

-- Retained: the *btree* evidence index still earns its place, because the
-- sector/type/date lookup it serves is not a spatial query.
