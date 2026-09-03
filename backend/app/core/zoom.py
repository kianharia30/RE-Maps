"""Zoom-to-aggregation-tier mapping (§9).

The map asks for a bounding box and a zoom; this decides whether the answer is
a national median or a set of individual dwellings. Keeping the thresholds in
one place means the frontend and backend can never disagree about which tier
a given zoom implies.
"""
from __future__ import annotations

from ..models.enums import MapTier, PrecisionLevel

# (min_zoom_inclusive, tier, the SQL `area_level` that serves it)
#
# The PROPERTY threshold is 14.5, not 16. Measured against the data: at
# neighbourhood zoom a single street rarely has enough sales in one period to
# support a street-level median, whereas individual dwellings — grid-decluttered
# server-side — are dense enough and are what a user at that zoom is looking
# for. Aggregation earns its place further out, where a viewport holds
# thousands of sales.
_LADDER: list[tuple[float, MapTier, str | None]] = [
    (14.5, MapTier.PROPERTY, None),        # individual dwellings
    (13.2, MapTier.STREET, "street"),      # computed live from transactions
    (12.0, MapTier.POSTCODE, "postcode"),  # computed live from transactions
    (10.5, MapTier.NEIGHBOURHOOD, "sector"),
    (9.0, MapTier.CITY, "outcode"),
    (7.0, MapTier.REGION, "district"),
    (4.5, MapTier.COUNTRY, "county"),
    (0.0, MapTier.WORLD, "country"),
]

# How many years of sales a LIVE tier pools to compute its median.
#
# A precomputed tier (sector and coarser) has enough sales in a single calendar
# year to publish a median. A street or a postcode does not: most streets see
# one sale a year or none. Pooling a three-year window is what makes these
# tiers populate at all; the window is reported in the response so the UI can
# state it rather than implying the figure is for the selected year alone.
LIVE_WINDOW_YEARS = 3

_PRECISION_BY_TIER: dict[MapTier, PrecisionLevel] = {
    MapTier.PROPERTY: PrecisionLevel.PROPERTY_ESTIMATE,
    MapTier.STREET: PrecisionLevel.STREET_POSTCODE,
    MapTier.POSTCODE: PrecisionLevel.STREET_POSTCODE,
    MapTier.NEIGHBOURHOOD: PrecisionLevel.NEIGHBOURHOOD,
    MapTier.CITY: PrecisionLevel.CITY_REGIONAL,
    MapTier.REGION: PrecisionLevel.CITY_REGIONAL,
    MapTier.COUNTRY: PrecisionLevel.CITY_REGIONAL,
    MapTier.WORLD: PrecisionLevel.CITY_REGIONAL,
}

# Hard caps on rows returned per tier, so a huge viewport can never try to
# render millions of markers (§8, §31).
_LIMIT_BY_TIER: dict[MapTier, int] = {
    MapTier.PROPERTY: 400,
    MapTier.STREET: 350,
    MapTier.POSTCODE: 300,
    MapTier.NEIGHBOURHOOD: 250,
    MapTier.CITY: 200,
    MapTier.REGION: 200,
    MapTier.COUNTRY: 150,
    MapTier.WORLD: 120,
}


# Not every country's administrative hierarchy has the same number of rungs.
# A country may override which `area_stats.area_level` serves a tier; anything
# not overridden uses the default ladder above.
#
# France has no postcode-outcode equivalent between commune and department, so
# its ladder is one rung shorter and the CITY tier is served by communes.
LEVEL_OVERRIDES: dict[str, dict[MapTier, str]] = {
    "FR": {
        MapTier.REGION: "county",      # departement
        MapTier.CITY: "district",      # commune
        MapTier.NEIGHBOURHOOD: "sector",  # postcode
    },
}

# Tiers answered by grouping the transactions table live, rather than from
# precomputed area_stats. At these zooms the viewport holds at most a few
# hundred sales, so a live GROUP BY is faster than maintaining ~1.7M postcode
# rows per year would be.
LIVE_LEVELS: frozenset[str] = frozenset({"postcode", "street"})


def is_live_level(level: str | None) -> bool:
    return level in LIVE_LEVELS


# The set of levels each country needs precomputing, derived from the ladder
# plus its overrides. Used by ingest/area_stats.py.
def levels_for_country(country_iso2: str) -> list[str]:
    overrides = LEVEL_OVERRIDES.get(country_iso2.upper(), {})
    levels: list[str] = []
    for _, tier, default_level in _LADDER:
        level = overrides.get(tier, default_level)
        if level and level not in levels and level not in LIVE_LEVELS:
            levels.append(level)
    return levels


def tier_for_zoom(zoom: float) -> MapTier:
    for min_zoom, tier, _ in _LADDER:
        if zoom >= min_zoom:
            return tier
    return MapTier.WORLD


# Coarser levels to try when a tier's own level has no rows for a viewport.
#
# Data granularity is wildly uneven between jurisdictions: England has postcode
# sectors, France has communes, the United States has states, and the 30
# countries covered only by Eurostat have nothing below national level. Rather
# than configure a bespoke ladder for each, the map walks this chain until it
# finds real rows, and reports the level it actually used.
COARSENING_CHAIN: tuple[str, ...] = (
    "street", "postcode", "sector", "outcode", "district", "county",
    "state", "country",
)


def coarser_levels(level: str) -> list[str]:
    """Levels at or coarser than `level`, in the order to try them."""
    try:
        start = COARSENING_CHAIN.index(level)
    except ValueError:
        return [level, "country"]
    return list(COARSENING_CHAIN[start:])


def area_level_for_tier(tier: MapTier, country_iso2: str | None = None) -> str | None:
    """The `area_stats.area_level` that serves this tier, or None if the tier is
    answered live from the transactions table."""
    if country_iso2:
        override = LEVEL_OVERRIDES.get(country_iso2.upper(), {}).get(tier)
        if override:
            return override
    for _, t, level in _LADDER:
        if t is tier:
            return level
    return None


def precision_for_tier(tier: MapTier) -> PrecisionLevel:
    return _PRECISION_BY_TIER[tier]


def limit_for_tier(tier: MapTier) -> int:
    return _LIMIT_BY_TIER[tier]


def is_property_tier(tier: MapTier) -> bool:
    return tier is MapTier.PROPERTY
