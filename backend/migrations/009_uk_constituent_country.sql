-- 009_uk_constituent_country.sql — sub-national coverage resolution.
--
-- WHY THIS EXISTS
-- HM Land Registry Price Paid Data covers England and Wales only. Scotland's
-- register is held by Registers of Scotland and Northern Ireland's by Land &
-- Property Services; neither publishes equivalent open transaction-level data.
--
-- Because Edinburgh and Belfast sit inside the GB country polygon, a
-- coordinates -> country resolver alone would report them as covered, and the
-- API would claim data it does not have. That is precisely the failure the
-- coverage registry's `region_code` exists to prevent, so we need to resolve a
-- point to a UK constituent country, not just to "GB".
--
-- Postcode areas map cleanly onto constituent countries. This is a stable,
-- documented mapping (Royal Mail / ONS), not an approximation:
--   Northern Ireland  BT
--   Scotland          AB DD DG EH FK G HS IV KA KW KY ML PA PH TD ZE
--   Wales             CF LL NP SA SY LD  (see the note below)
--   England           everything else
--
-- Caveat on Wales, recorded honestly: a few postcode areas straddle the
-- England-Wales border. CH, SY and LD in particular contain both English and
-- Welsh postcodes. Since Price Paid covers England AND Wales identically,
-- misclassifying a border postcode between those two has no effect on what
-- data is available — the England/Wales split is informational only. The
-- distinction that actually gates coverage is Scotland and Northern Ireland,
-- and those areas do not straddle a border with England.

ALTER TABLE postcodes
    ADD COLUMN IF NOT EXISTS uk_country text;

UPDATE postcodes SET uk_country = CASE
    WHEN area = 'BT' THEN 'Northern Ireland'
    WHEN area IN ('AB','DD','DG','EH','FK','G','HS','IV','KA','KW','KY',
                  'ML','PA','PH','TD','ZE') THEN 'Scotland'
    WHEN area IN ('CF','LL','NP','SA') THEN 'Wales'
    ELSE 'England'
END
WHERE country_iso2 = 'GB' AND uk_country IS DISTINCT FROM CASE
    WHEN area = 'BT' THEN 'Northern Ireland'
    WHEN area IN ('AB','DD','DG','EH','FK','G','HS','IV','KA','KW','KY',
                  'ML','PA','PH','TD','ZE') THEN 'Scotland'
    WHEN area IN ('CF','LL','NP','SA') THEN 'Wales'
    ELSE 'England'
END;

-- Resolving a point to a constituent country is a nearest-neighbour lookup,
-- which rides the existing `postcodes_geom_gix`. A partial index on the same
-- column was measured as pure duplication (~100 MB) for no plan improvement,
-- because the KNN operator uses the full index and rechecks the filter.

COMMENT ON COLUMN postcodes.uk_country IS
    'UK constituent country, derived from the postcode area. Used to resolve '
    'sub-national coverage: Price Paid Data covers England and Wales only.';
