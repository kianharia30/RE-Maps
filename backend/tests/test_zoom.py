"""The zoom -> aggregation-tier ladder (§9)."""
from __future__ import annotations

import pytest

from app.core.zoom import (
    LIVE_LEVELS,
    area_level_for_tier,
    is_live_level,
    levels_for_country,
    limit_for_tier,
    precision_for_tier,
    tier_for_zoom,
)
from app.models.enums import MapTier, PrecisionLevel


class TestLadder:
    @pytest.mark.parametrize(
        "zoom,tier",
        [
            (0, MapTier.WORLD), (2, MapTier.WORLD), (4.4, MapTier.WORLD),
            (4.5, MapTier.COUNTRY), (6, MapTier.COUNTRY),
            (7, MapTier.REGION), (8.9, MapTier.REGION),
            (9, MapTier.CITY), (10.4, MapTier.CITY),
            (10.5, MapTier.NEIGHBOURHOOD), (11.9, MapTier.NEIGHBOURHOOD),
            (12.0, MapTier.POSTCODE), (13.1, MapTier.POSTCODE),
            (13.2, MapTier.STREET), (14.4, MapTier.STREET),
            (14.5, MapTier.PROPERTY), (22, MapTier.PROPERTY),
        ],
    )
    def test_tier_for_zoom(self, zoom, tier):
        assert tier_for_zoom(zoom) == tier

    def test_ladder_is_monotonic(self):
        """Zooming in must never move to a coarser tier."""
        order = [
            MapTier.WORLD, MapTier.COUNTRY, MapTier.REGION, MapTier.CITY,
            MapTier.NEIGHBOURHOOD, MapTier.POSTCODE, MapTier.STREET,
            MapTier.PROPERTY,
        ]
        seen = [tier_for_zoom(z / 10) for z in range(0, 221)]
        indices = [order.index(t) for t in seen]
        assert indices == sorted(indices)

    def test_only_property_tier_claims_property_precision(self):
        for zoom in [0, 5, 8, 10, 12, 13.5, 14.4]:
            tier = tier_for_zoom(zoom)
            assert precision_for_tier(tier) is not PrecisionLevel.EXACT_TRANSACTION
        # Aggregated tiers must never claim to describe one dwelling.
        for tier in (MapTier.CITY, MapTier.REGION, MapTier.COUNTRY, MapTier.WORLD):
            assert precision_for_tier(tier) is PrecisionLevel.CITY_REGIONAL

    def test_every_tier_has_a_row_limit(self):
        for tier in MapTier:
            assert limit_for_tier(tier) > 0

    def test_property_tier_is_answered_live(self):
        assert area_level_for_tier(MapTier.PROPERTY) is None


class TestCountryOverrides:
    def test_france_has_a_shorter_ladder_than_the_uk(self):
        gb = levels_for_country("GB")
        fr = levels_for_country("FR")
        # France has no postcode-outcode rung between commune and department.
        assert "outcode" in gb
        assert "outcode" not in fr
        assert len(fr) < len(gb)

    def test_france_city_tier_uses_communes(self):
        assert area_level_for_tier(MapTier.CITY, "FR") == "district"
        assert area_level_for_tier(MapTier.CITY, "GB") == "outcode"

    def test_precomputed_levels_exclude_live_ones(self):
        for country in ("GB", "FR"):
            for level in levels_for_country(country):
                assert not is_live_level(level)
                assert level not in LIVE_LEVELS

    def test_unknown_country_uses_the_default_ladder(self):
        assert area_level_for_tier(MapTier.CITY, "ZZ") == "outcode"
