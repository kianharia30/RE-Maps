-- 016_price_statistic.sql — record WHICH statistic a price figure is.
--
-- We compute medians from individual sales (England & Wales, France, Ireland).
-- National statistics offices mostly publish the MEAN instead: CBS reports
-- "gemiddelde verkoopprijs", Danmarks Statistik "gennemsnitlig pris pr.
-- ejendom". For a right-skewed distribution like house prices the mean sits
-- materially above the median — often 10-20% above — so presenting one as the
-- other overstates typical prices.
--
-- Storing which statistic it is lets the map label it truthfully ("average"
-- vs "median") instead of silently mixing two different measures on one
-- screen.
ALTER TABLE area_stats
    ADD COLUMN IF NOT EXISTS price_statistic text NOT NULL DEFAULT 'MEDIAN';

ALTER TABLE area_stats
    DROP CONSTRAINT IF EXISTS area_stats_price_statistic_known;
ALTER TABLE area_stats
    ADD CONSTRAINT area_stats_price_statistic_known
    CHECK (price_statistic IN ('MEDIAN', 'MEAN'));

COMMENT ON COLUMN area_stats.price_statistic IS
    'MEDIAN = middle value, computed by us from individual sale records. '
    'MEAN = arithmetic average, as published by a national statistics office. '
    'The two are not interchangeable for skewed price distributions.';
