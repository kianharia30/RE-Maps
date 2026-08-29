"""Resolve coordinates to a jurisdiction (§25).

    coordinates -> country -> jurisdiction -> provider

This is the single gate through which every map/property request passes. If it
cannot name a country, no provider is selected and the honest NO_DATA path is
taken — there is no default provider to fall through to.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..db import fetch_one


@dataclass(frozen=True, slots=True)
class Jurisdiction:
    country_iso2: str
    country_name: str
    currency_code: str | None
    # Sub-national jurisdiction, where the country's data coverage is not
    # uniform. For GB this is the constituent country, because Price Paid Data
    # covers England and Wales but not Scotland or Northern Ireland.
    region_code: str | None = None
    region_name: str | None = None


_POINT_SQL = """
SELECT iso2, name, currency_code
FROM countries
WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
ORDER BY ST_Area(geom) ASC   -- prefer the smallest containing polygon
LIMIT 1
"""

# For a bounding box we take the country at the viewport centre. If the centre
# falls in the sea (a coastal view), fall back to whichever country covers the
# largest share of the box, so panning along a coastline still resolves.
_BBOX_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326) AS g
)
SELECT c.iso2, c.name, c.currency_code
FROM countries c, box b
WHERE ST_Intersects(c.geom, b.g)
ORDER BY ST_Area(ST_Intersection(c.geom, b.g)) DESC
LIMIT 1
"""


# Nearest live postcode centroid, used to resolve a UK point to its
# constituent country. `<->` is the PostGIS KNN operator, so this is an
# index-ordered nearest-neighbour scan rather than a distance sort over 2.6M
# rows. The distance guard matters: Open Postcode Geo excludes Northern Ireland
# (BT) for licensing reasons, so a point in Belfast has no nearby postcode and
# must NOT be silently attributed to the nearest Scottish or English one.
_UK_REGION_SQL = """
SELECT uk_country,
       ST_DistanceSphere(geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)) AS distance_m
FROM postcodes
WHERE country_iso2 = 'GB' AND status = 'live' AND uk_country IS NOT NULL
ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)
LIMIT 1
"""

# Beyond this, the nearest postcode says nothing useful about the point.
_MAX_REGION_MATCH_M = 25_000

# UK constituent country -> the region_code used in `provider_coverage`.
_UK_REGION_CODES = {
    "England": None,            # covered by the country-wide entry
    "Wales": None,              # ditto: Price Paid covers England AND Wales
    "Scotland": "GB-SCT",
    "Northern Ireland": "GB-NIR",
}


# Northern Ireland envelope.
#
# NI cannot be resolved from the postcode gazetteer, because Open Postcode Geo
# excludes BT postcodes for licensing reasons. It can be resolved
# geographically: Northern Ireland lies on the island of Ireland, separated
# from Great Britain by the Irish Sea, so this envelope contains all of NI and
# none of Great Britain (the nearest GB land, the Mull of Kintyre and Islay,
# sits north of 55.28 degrees).
#
# This coarse test is used ONLY to choose which "no data available" explanation
# to show. It can never affect a price, because Northern Ireland has no
# property data in the first place — the alternative would be the vaguer
# "could not be matched to a postcode area", which tells the user less.
_NI_ENVELOPE = (-8.20, 53.95, -5.43, 55.28)   # west, south, east, north


def _in_northern_ireland(lon: float, lat: float) -> bool:
    west, south, east, north = _NI_ENVELOPE
    return west <= lon <= east and south <= lat <= north


async def _uk_region(lon: float, lat: float) -> tuple[str | None, str | None]:
    """(region_code, region_name) for a GB point.

    Great Britain is resolved from the postcode gazetteer; Northern Ireland
    geographically, because its postcodes are absent from that gazetteer.
    """
    if _in_northern_ireland(lon, lat):
        return "GB-NIR", "Northern Ireland"

    row = await fetch_one(_UK_REGION_SQL, {"lon": lon, "lat": lat})
    if not row:
        return None, None
    if row["distance_m"] is not None and row["distance_m"] > _MAX_REGION_MATCH_M:
        # No postcode near this point. Northern Ireland is the expected case,
        # since its postcodes are absent from the open gazetteer.
        return "GB-UNKNOWN", "an area with no postcode coverage"
    name = row["uk_country"]
    return _UK_REGION_CODES.get(name, "GB-UNKNOWN"), name


async def resolve_point(lon: float, lat: float) -> Jurisdiction | None:
    row = await fetch_one(_POINT_SQL, (lon, lat))
    if not row:
        return None
    region_code = region_name = None
    if row["iso2"] == "GB":
        region_code, region_name = await _uk_region(lon, lat)
    return Jurisdiction(
        row["iso2"], row["name"], row["currency_code"], region_code, region_name
    )


async def resolve_bbox(
    west: float, south: float, east: float, north: float
) -> Jurisdiction | None:
    lat = (south + north) / 2
    lon = (west + east) / 2
    at_centre = await resolve_point(lon, lat)
    if at_centre:
        return at_centre
    row = await fetch_one(
        _BBOX_SQL, {"west": west, "south": south, "east": east, "north": north}
    )
    if not row:
        return None
    region_code = region_name = None
    if row["iso2"] == "GB":
        region_code, region_name = await _uk_region(lon, lat)
    return Jurisdiction(
        row["iso2"], row["name"], row["currency_code"], region_code, region_name
    )


async def country_name(iso2: str) -> str | None:
    row = await fetch_one("SELECT name FROM countries WHERE iso2 = %s", (iso2.upper(),))
    return row["name"] if row else None
