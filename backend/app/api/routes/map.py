"""GET /api/map/prices — the endpoint the map itself lives on.

Given a viewport, a zoom and a year, returns exactly what should be drawn:

  low/medium zoom  -> aggregated area medians from `area_stats`
                      (labelled REGIONAL_STATISTIC — never a dwelling value)
  high zoom        -> individual dwellings with a per-dwelling figure

For a FUTURE year the area medians are projected with the market forecast and
flagged `is_forecast`, so the timeline keeps working past the present without
ever presenting a projection as an observation.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Query

from ...core import coverage as coverage_mod
from ...core.currency import currency_for_country
from ...core.errors import AppError
from ...core.zoom import (
    LIVE_WINDOW_YEARS,
    area_level_for_tier,
    coarser_levels,
    is_live_level,
    limit_for_tier,
    precision_for_tier,
    tier_for_zoom,
)
from ...db import fetch_all, fetch_one
from ...forecast import service as forecast_service
from ...models.enums import DataStatus, MapTier
from ...models.geo import AreaStat, BoundingBox, MapResponse
from ...models.property import PropertySummary
from ...providers import registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/map", tags=["map"])

NO_DATA_MESSAGE = (
    "Property price data is not currently available for this location."
)


# --- aggregated tiers -------------------------------------------------------

# Country-level rows need different geometry handling from every other tier.
#
# A country statistic has one stored point (ST_PointOnSurface of its polygon).
# Requiring that point to fall inside the viewport made the figure invisible
# almost everywhere useful: Germany's point-on-surface is near Kassel, so a
# viewport over Berlin contained no marker and the map reported no data for a
# country we genuinely cover.
#
# Instead we select countries whose POLYGON intersects the viewport, and place
# the marker at the centroid of the visible part, so it is always on screen and
# still sits within the country it describes.
_COUNTRY_AREA_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326) AS g
),
-- ONE row per country. A country can legitimately hold both a real
-- transaction-derived median AND an official index for the same year: France
-- has recorded sales for 2021-2023 and a Eurostat series throughout, which
-- produced two markers for France on the same map. Real recorded sales always
-- win over an index, because a measured price beats a measure of change.
picked AS (
    SELECT DISTINCT ON (a.country_iso2)
           a.area_level, a.area_code, a.area_name,
           ST_Y(ST_PointOnSurface(ST_Intersection(c.geom, box.g))) AS latitude,
           ST_X(ST_PointOnSurface(ST_Intersection(c.geom, box.g))) AS longitude,
           a.year, a.median_price::float8 AS median_price,
           a.p25_price::float8 AS p25_price, a.p75_price::float8 AS p75_price,
           a.median_price_per_sqm::float8 AS median_price_per_sqm,
           a.index_value::float8 AS index_value, a.basis, a.price_statistic,
           a.growth_1y_pct::float8 AS stored_growth,
           a.transaction_count, a.currency_code AS currency,
           a.index_area_code, a.index_area_name,
           NULL::float8 AS prev_median,
           ST_Area(ST_Intersection(c.geom, box.g)) AS visible_area
    FROM area_stats a
    JOIN countries c ON c.iso2 = a.country_iso2
    CROSS JOIN box
    -- Deliberately NOT filtered to the single resolved country. At world and
    -- continental zoom the viewport spans many countries, and showing only the
    -- one under the viewport centre made the other covered countries look
    -- absent.
    WHERE a.area_level = 'country'
      AND a.segment = %(segment)s
  -- A real monetary figure or nothing (see above).
  AND a.median_price IS NOT NULL
      -- A real monetary figure or nothing. An index-only row carries
      -- a growth rate and no price, and is not shown.
      AND a.median_price IS NOT NULL
      AND a.year = %(year)s
      AND ST_Intersects(c.geom, box.g)
      AND GeometryType(ST_Intersection(c.geom, box.g)) LIKE '%%POLYGON'
    ORDER BY a.country_iso2,
             (a.basis = 'TRANSACTIONS') DESC,
             a.transaction_count DESC
)
SELECT * FROM picked
-- Largest visible country first, so a LIMIT drops slivers at the map edge
-- rather than the country the viewer is actually looking at.
ORDER BY visible_area DESC
LIMIT %(limit)s
"""

# Region-shaped areas (US states today) are selected by POLYGON intersection
# and their marker is clipped into the visible area, the same treatment
# countries get. Without it, California's figure was unreachable from a view of
# Los Angeles and the map fell back to the national index.
_REGION_AREA_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326) AS g
),
-- DISTINCT ON dictates the sort order, so the pick and the presentation order
-- are separated: choose one row per region here, then order by how much of
-- each region is actually on screen below. Without that split the "first"
-- area was whichever had the lowest region_id -- a viewport over Dublin led
-- with Louth.
picked AS (
    SELECT DISTINCT ON (a.region_id)
           a.area_level, a.area_code, a.area_name,
           ST_Y(ST_PointOnSurface(ST_Intersection(r.geom, box.g))) AS latitude,
           ST_X(ST_PointOnSurface(ST_Intersection(r.geom, box.g))) AS longitude,
           a.year, a.median_price::float8 AS median_price,
           a.p25_price::float8 AS p25_price, a.p75_price::float8 AS p75_price,
           a.median_price_per_sqm::float8 AS median_price_per_sqm,
           a.index_value::float8 AS index_value, a.basis, a.price_statistic,
           a.growth_1y_pct::float8 AS stored_growth,
           a.transaction_count, a.currency_code AS currency,
           a.index_area_code, a.index_area_name,
           NULL::float8 AS prev_median,
           ST_Area(ST_Intersection(r.geom, box.g)) AS visible_area
    FROM area_stats a
    JOIN regions r ON r.id = a.region_id
    CROSS JOIN box
    WHERE a.country_iso2 = %(country)s
      AND a.area_level = %(level)s
      AND a.segment = %(segment)s
  -- A real monetary figure or nothing (see above).
  AND a.median_price IS NOT NULL
      -- A real monetary figure or nothing. An index-only row carries
      -- a growth rate and no price, and is not shown.
      AND a.median_price IS NOT NULL
      AND a.year = %(year)s
      AND ST_Intersects(r.geom, box.g)
      AND GeometryType(ST_Intersection(r.geom, box.g)) LIKE '%%POLYGON'
    -- Real recorded sales beat an official index for the same region.
    ORDER BY a.region_id, (a.basis = 'TRANSACTIONS') DESC,
             a.transaction_count DESC
)
SELECT * FROM picked
ORDER BY visible_area DESC
LIMIT %(limit)s
"""

_AREA_SQL = """
SELECT a.area_level, a.area_code, a.area_name,
       ST_Y(a.geom) AS latitude, ST_X(a.geom) AS longitude,
       a.year, a.median_price::float8 AS median_price,
       a.p25_price::float8 AS p25_price, a.p75_price::float8 AS p75_price,
       a.median_price_per_sqm::float8 AS median_price_per_sqm,
       a.index_value::float8 AS index_value, a.basis, a.price_statistic,
       a.growth_1y_pct::float8 AS stored_growth,
       a.transaction_count, a.currency_code AS currency,
       a.index_area_code, a.index_area_name,
       prev.median_price::float8 AS prev_median
FROM area_stats a
LEFT JOIN area_stats prev
       ON prev.country_iso2 = a.country_iso2
      AND prev.area_level = a.area_level
      AND prev.area_code = a.area_code
      AND prev.segment = a.segment
      AND prev.year = a.year - 1
WHERE a.country_iso2 = %(country)s
  AND a.area_level = %(level)s
  AND a.segment = %(segment)s
  -- A real monetary figure or nothing (see above).
  AND a.median_price IS NOT NULL
  AND a.year = %(year)s
  AND a.geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
ORDER BY a.transaction_count DESC
LIMIT %(limit)s
"""

# Live aggregation for the postcode and street tiers. Grouping in SQL keeps the
# work server-side (§9) — the API never ships raw rows for the client to reduce.
_LIVE_SQL = """
SELECT %(level)s AS area_level,
       {key}::text AS area_code,
       max({name}::text) AS area_name,
       ST_Y(ST_Centroid(ST_Collect(t.geom))) AS latitude,
       ST_X(ST_Centroid(ST_Collect(t.geom))) AS longitude,
       %(year)s::int AS year,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price)::float8 AS median_price,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY t.price)::float8 AS p25_price,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY t.price)::float8 AS p75_price,
       (percentile_cont(0.5) WITHIN GROUP (ORDER BY t.price_per_sqm)
            FILTER (WHERE t.price_per_sqm IS NOT NULL))::float8 AS median_price_per_sqm,
       count(*)::int AS transaction_count,
       max(t.currency_code) AS currency,
       NULL::float8 AS index_value, 'TRANSACTIONS'::text AS basis,
       'MEDIAN'::text AS price_statistic,
       NULL::float8 AS stored_growth,
       -- Live tiers resolve their index from the modal district in the group.
       (SELECT l.area_code FROM area_index_links l
         WHERE l.country_iso2 = %(country)s AND l.district = mode() WITHIN GROUP (ORDER BY t.district)
       ) AS index_area_code,
       mode() WITHIN GROUP (ORDER BY t.district) AS index_area_name,
       NULL::float8 AS prev_median
FROM transactions t
WHERE t.country_iso2 = %(country)s
  AND t.market_value_basis = 'STANDARD'
  AND t.is_residential
  AND t.geom IS NOT NULL
  AND {key} IS NOT NULL
  AND t.geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)
  AND t.transaction_date >= %(date_from)s
  AND t.transaction_date <= %(date_to)s
  AND (%(segment)s = 'all' OR t.property_type = %(segment)s)
GROUP BY {key}
HAVING count(*) >= %(min_count)s
ORDER BY count(*) DESC
LIMIT %(limit)s
"""

_LIVE_KEYS = {
    "postcode": ("t.postcode_norm", "t.postcode_norm", 2),
    "street": ("(coalesce(t.street,'') || ', ' || coalesce(t.outcode,''))",
               "coalesce(t.street, t.postcode_norm)", 2),
}


# --- property tier ----------------------------------------------------------
#
# Two problems have to be solved together here.
#
# 1. DECLUTTERING. A dense urban viewport can hold thousands of dwellings, and
#    drawing them all produces an unreadable wall of overlapping pills. We thin
#    server-side by snapping to a screen-space grid sized from the zoom level,
#    and keeping one dwelling per cell — the one with the most transaction
#    evidence behind it, so the marker you see is the best-supported one in
#    that spot. This is honest thinning: nothing is aggregated or averaged, each
#    surviving marker is still one real dwelling, and `total_available` reports
#    how many were in view.
#
# 2. CO-LOCATION. Price Paid records share a postcode centroid, so many
#    dwellings sit on one exact point. `ROW_NUMBER()` gives each a deterministic
#    slot on a small ring around the centroid purely so they can be
#    distinguished and clicked. The response flags `position_is_approximate` and
#    the panel states that the position is the postcode centroid. The stored
#    geometry is never modified.
_PROPERTY_SQL = """
WITH box AS (
    SELECT ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326) AS g
),
in_view AS (
    SELECT p.id, p.address_line, p.postcode, p.property_type, p.postcode_norm,
           p.district, p.coordinate_precision, p.geom,
           ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon,
           p.floor_area_sqm::float8 AS floor_area_sqm,
           (SELECT count(*) FROM transactions t2 WHERE t2.property_id = p.id) AS txn_count,
           (SELECT max(t3.transaction_date) FROM transactions t3
             WHERE t3.property_id = p.id) AS last_sold
    FROM properties p, box
    WHERE p.country_iso2 = %(country)s
      AND p.is_residential
      AND p.geom IS NOT NULL
      AND p.geom && box.g
      AND (%(segment)s = 'all' OR p.property_type = %(segment)s)
      AND EXISTS (SELECT 1 FROM transactions t WHERE t.property_id = p.id)
    LIMIT %(scan_limit)s
),
thinned AS (
    -- One dwelling per screen-space grid cell, best-evidenced first.
    SELECT DISTINCT ON (ST_SnapToGrid(geom, %(cell_x)s, %(cell_y)s))
           *
    FROM in_view
    ORDER BY ST_SnapToGrid(geom, %(cell_x)s, %(cell_y)s),
             txn_count DESC, last_sold DESC NULLS LAST, id
),
ranked AS (
    SELECT t.*,
           ROW_NUMBER() OVER (PARTITION BY t.postcode_norm ORDER BY t.id) - 1 AS slot,
           COUNT(*)    OVER (PARTITION BY t.postcode_norm)                   AS in_postcode
    FROM thinned t
)
SELECT ranked.*,
       -- Ring radius grows with how many dwellings share the centroid, capped
       -- at ~40 m so a marker never leaves its own postcode.
       CASE WHEN in_postcode > 1
            THEN LEAST(0.00036, 0.00010 * sqrt(in_postcode))
            ELSE 0 END AS ring_deg,
       (SELECT count(*) FROM in_view) AS total_in_view
FROM ranked
ORDER BY txn_count DESC, id
LIMIT %(limit)s
"""


def _grid_cell(zoom: float, lat: float) -> tuple[float, float]:
    """Grid cell size in degrees that corresponds to one marker footprint.

    A price pill is roughly 70x46 CSS pixels including its pin. Converting that
    to degrees at the current zoom gives a cell in which two markers would
    visually collide, so keeping one per cell removes overlap without thinning
    more than necessary. Longitude degrees shrink with latitude, hence the
    cosine term.
    """
    import math

    deg_per_px = 360.0 / (256.0 * (2.0**zoom))
    lat_scale = max(math.cos(math.radians(lat)), 0.15)
    return deg_per_px * 70.0 / lat_scale, deg_per_px * 46.0


def _spread(lat: float, lon: float, slot: int, count: int, ring_deg: float
            ) -> tuple[float, float]:
    """Deterministic de-collision offset for markers sharing a centroid."""
    if count <= 1 or ring_deg <= 0:
        return lat, lon
    import math

    angle = 2 * math.pi * (slot / count)
    # Longitude degrees shrink with latitude; scale so the ring stays circular.
    lon_scale = max(math.cos(math.radians(lat)), 0.2)
    return lat + ring_deg * math.sin(angle), lon + ring_deg * math.cos(angle) / lon_scale


@router.get("/prices", response_model=MapResponse)
async def map_prices(
    bbox: str = Query(..., description="west,south,east,north in WGS84 degrees"),
    zoom: float = Query(..., ge=0, le=22),
    year: int = Query(..., ge=1900, le=2100),
    segment: str = Query("all", pattern="^(all|detached|semi_detached|terraced|flat|house)$"),
) -> MapResponse:
    try:
        box = BoundingBox.parse(bbox).clamped()
    except ValueError as exc:
        from ...core.errors import BadRequest

        raise BadRequest(str(exc)) from exc

    tier = tier_for_zoom(zoom)
    today = date.today()
    is_future = year > today.year

    jurisdiction, provider = await registry.for_bbox(
        box.west, box.south, box.east, box.north
    )
    if jurisdiction is None:
        return MapResponse(
            status=DataStatus.UNSUPPORTED_LOCATION,
            message=NO_DATA_MESSAGE,
            tier=tier, year=year, is_future=is_future,
            is_historical=year < today.year,
        )

    country = jurisdiction.country_iso2
    # Region-aware: a sub-national row that records an absence must win over
    # the country-wide entry, so Scotland is not served England's dataset.
    entry = await coverage_mod.lookup(country, jurisdiction.region_code)

    if not coverage_mod.is_usable(entry):
        # The jurisdiction is resolved from the viewport CENTRE, which is the
        # right question for one place and the wrong one for a continent. A
        # view of Europe centred a few degrees off can land on Switzerland —
        # which Eurostat's series excludes — and that single point used to
        # blank a map showing 27 countries we do cover.
        #
        # So an uncovered centre is not fatal to an area tier: ask the data
        # whether anything in view is covered, and only report the absence if
        # nothing is. The property tier keeps the strict gate, because there
        # the centre really is the subject of the request.
        #
        # The guard here is about WHICH KIND of absence has been recorded.
        #
        # A SUB-NATIONAL absence (Scotland, Northern Ireland — region_code set)
        # sits inside a country that does have data, so falling through would
        # serve Scotland England and Wales's median. That is precisely the
        # substitution those rows exist to prevent, and an earlier version of
        # this fallback did exactly it. Those must always refuse.
        #
        # A COUNTRY-WIDE absence (Germany, Spain — region_code NULL) is
        # different: there is no parent dataset to leak from, and a viewport
        # over Europe centred on Germany still shows the UK, Ireland, the
        # Netherlands, Sweden and Denmark. Blanking all of them because the
        # centre pixel fell on an uncovered country is the bug this fallback
        # was written to fix in the first place.
        country_wide_absence = entry is not None and entry.region_code is None
        if tier is not MapTier.PROPERTY and (entry is None or country_wide_absence):
            spanning = await _multi_country_area_tier(
                box, tier, year, segment, is_future, today
            )
            if spanning is not None:
                return spanning
        return MapResponse(
            status=DataStatus.UNSUPPORTED_LOCATION,
            # Prefer the registry's specific reason over the generic sentence.
            message=(entry.notes if entry and entry.notes else None) or NO_DATA_MESSAGE,
            tier=tier, year=year, is_future=is_future,
            is_historical=year < today.year,
        )

    # A jurisdiction can be genuinely covered by official area statistics while
    # having no individual-sale data at all — 30 European countries via Eurostat
    # and the United States via FHFA are in exactly that position. Area tiers
    # are served from `area_stats`, which is provider-independent; the property
    # tier requires a provider AND transaction-level coverage, so a request for
    # individual dwellings in Germany is refused with the reason rather than
    # answered with a national figure dressed up as a house valuation.
    dwelling_level = provider is not None and coverage_mod.supports_individual_properties(
        entry
    )
    if tier is MapTier.PROPERTY and not dwelling_level:
        return MapResponse(
            status=DataStatus.NO_DATA,
            message=(
                "Individual property prices are not available here. "
                f"{entry.region_name or jurisdiction.country_name} is covered by "
                "official area statistics only — zoom out to see them."
                if entry else NO_DATA_MESSAGE
            ),
            tier=tier, year=year, is_future=is_future,
            is_historical=year < today.year,
            currency=(entry.currency_code if entry else None),
            attributions=[s.attribution for s in (entry.sources if entry else [])],
        )

    currency = entry.currency_code or currency_for_country(country) or "GBP"
    attributions = [s.attribution for s in entry.sources]

    try:
        if tier is MapTier.PROPERTY and dwelling_level:
            return await _property_tier(
                box, tier, year, segment, country, provider, currency,
                attributions, is_future, today, zoom,
            )
        return await _area_tier(
            box, tier, year, segment, country, currency, attributions,
            is_future, today,
        )
    except AppError:
        raise
    except Exception:
        log.exception("map query failed for %s tier=%s year=%s", country, tier, year)
        from ...core.errors import ProviderError

        raise ProviderError() from None


# --- tier implementations ---------------------------------------------------


async def _multi_country_area_tier(
    box: BoundingBox,
    tier: MapTier,
    year: int,
    segment: str,
    is_future: bool,
    today: date,
) -> MapResponse | None:
    """Country-level figures for every covered country in view.

    Used when the viewport centre is in a country we do not cover but the
    viewport itself spans countries we do. Returns None when nothing in view is
    covered, so the caller can report the absence properly.

    Country level only, deliberately: a viewport this wide is showing whole
    countries, and reaching for sub-national data would require a jurisdiction
    we have just established is the wrong one to ask about.
    """
    if is_future:
        # A future year needs a price level to project, and the countries
        # reachable this way are index-only.
        return None

    row = await fetch_one(
        """
        SELECT max(year) AS y FROM area_stats
        WHERE area_level = 'country' AND segment = %s AND year <= %s
        """,
        (segment, year),
    )
    stats_year = (row or {}).get("y") or year

    rows = await fetch_all(
        _COUNTRY_AREA_SQL,
        {
            "segment": segment, "year": stats_year,
            "w": box.west, "s": box.south, "e": box.east, "n": box.north,
            "limit": limit_for_tier(tier),
        },
    )
    if not rows:
        return None

    areas: list[AreaStat] = []
    for r in rows:
        growth = r.get("stored_growth")
        has_level = r["median_price"] is not None
        if not has_level and growth is None:
            continue
        areas.append(
            AreaStat(
                area_level=r["area_level"], area_code=r["area_code"],
                area_name=r["area_name"], latitude=r["latitude"],
                longitude=r["longitude"], year=r["year"],
                median_price=(
                    round(r["median_price"]) if has_level else None
                ),
                p25_price=r["p25_price"], p75_price=r["p75_price"],
                median_price_per_sqm=r["median_price_per_sqm"],
                transaction_count=r["transaction_count"],
                currency=r["currency"],
                growth_1y_pct=(round(float(growth), 1) if growth is not None else None),
                precision_level=precision_for_tier(tier),
                basis=r.get("basis") or "TRANSACTIONS",
                price_statistic=r.get("price_statistic") or "MEDIAN",
                index_value=r.get("index_value"),
                has_price_level=has_level,
            )
        )
    if not areas:
        return None

    # Each marker carries its own currency; a single top-level one would be
    # meaningless across a continent, so it is left unset.
    attributions = await _attributions_for_countries(
        [r["area_code"] for r in rows]
    )
    return MapResponse(
        status=DataStatus.OK, tier=tier, year=year, data_year=stats_year,
        is_future=is_future, is_historical=year < today.year,
        currency=None, areas=areas,
        truncated=len(rows) >= limit_for_tier(tier),
        attributions=attributions,
    )


async def _attributions_for_countries(area_codes: list[str]) -> list[str]:
    """Attributions for the sources actually behind the returned rows."""
    rows = await fetch_all(
        """
        SELECT DISTINCT ds.attribution
        FROM area_stats a
        JOIN data_sources ds ON ds.key = a.source_key
        WHERE a.area_code = ANY(%s::text[]) AND ds.attribution IS NOT NULL
        """,
        (area_codes,),
    )
    return [r["attribution"] for r in rows]


async def _area_tier(
    box: BoundingBox, tier: MapTier, year: int, segment: str, country: str,
    currency: str, attributions: list[str], is_future: bool, today: date,
) -> MapResponse:
    level = area_level_for_tier(tier, country)
    if not level:
        return MapResponse(
            status=DataStatus.NO_DATA, message=NO_DATA_MESSAGE, tier=tier,
            year=year, is_future=is_future, currency=currency,
        )

    limit = limit_for_tier(tier)
    # A future year has no observations, so aggregate the latest observed year
    # and project it forward.
    stats_year = year
    if is_future:
        row = await fetch_one(
            "SELECT max(year) AS y FROM area_stats WHERE country_iso2=%s",
            (country,),
        )
        stats_year = (row or {}).get("y") or today.year
    else:
        # Snap to the nearest year actually present. A dataset can have a hole
        # in it (an interrupted ingest, or a publisher's reporting gap), and a
        # selectable year that silently returns nothing reads as a broken map
        # rather than as missing data. The response reports the year used.
        row = await fetch_one(
            """
            SELECT year FROM area_stats
            WHERE country_iso2 = %s AND segment = %s
            ORDER BY abs(year - %s), year DESC
            LIMIT 1
            """,
            (country, segment, year),
        )
        if row and row["year"] != year:
            stats_year = row["year"]

    params = {
        "country": country, "level": level, "segment": segment,
        "year": stats_year, "w": box.west, "s": box.south, "e": box.east,
        "n": box.north, "limit": limit,
    }

    # Try the tier's own level, then progressively coarser ones. A viewport over
    # Germany asks for a postcode sector and gets the national figure, because
    # that is the finest granularity Eurostat publishes — reported as such
    # rather than returned empty.
    rows: list[dict] = []
    level_used = level
    for candidate in coarser_levels(level):
        if is_live_level(candidate):
            key, name, min_count = _LIVE_KEYS[candidate]
            # Pool several years so a street or postcode has enough sales to
            # produce a median at all (see LIVE_WINDOW_YEARS).
            window_from = stats_year - (LIVE_WINDOW_YEARS - 1)
            attempt = await fetch_all(
                _LIVE_SQL.format(key=key, name=name),
                params | {
                    "level": candidate,
                    "min_count": min_count,
                    "date_from": date(window_from, 1, 1),
                    "date_to": date(stats_year, 12, 31),
                },
            )
            for row in attempt:
                row["window_from_year"] = window_from
                row["window_to_year"] = stats_year
        elif candidate == "country":
            attempt = await fetch_all(_COUNTRY_AREA_SQL, params)
        else:
            # Prefer the polygon-clipped query wherever the rows carry a real
            # shape (US states, Irish counties). It is what makes a large
            # area's figure reachable from a viewport inside it, rather than
            # only from one that happens to contain a stored point. Levels
            # whose rows have no region_id — UK districts and sectors, which
            # are positioned at the centroid of their own sales — fall through
            # to the point query.
            attempt = await fetch_all(
                _REGION_AREA_SQL, params | {"level": candidate}
            )
            if not attempt:
                attempt = await fetch_all(
                    _AREA_SQL, params | {"level": candidate}
                )
        if attempt:
            rows, level_used = attempt, candidate
            break
    level = level_used

    if not rows:
        return MapResponse(
            status=DataStatus.NO_DATA,
            message=(
                f"No recorded residential sales for {stats_year} in this area."
                if not is_future else
                "No recent sales in this area to project forward."
            ),
            tier=tier, year=year, is_future=is_future,
            is_historical=year < today.year, currency=currency,
            attributions=attributions,
        )

    precision = precision_for_tier(tier)
    areas: list[AreaStat] = []

    # For a future year, one forecast per distinct area (cheap: a handful).
    forecasts: dict[str, object] = {}
    if is_future:
        for row in rows:
            code = row["area_code"]
            if code in forecasts:
                continue
            # Each area is projected with ITS OWN local index, resolved when
            # the statistics were computed (migration 011). Where no local
            # series could be found we deliberately produce no forecast rather
            # than applying a national growth rate to a local median.
            index_area = row.get("index_area_code")
            if not index_area:
                forecasts[code] = None
                continue
            fc = await forecast_service.forecast_market(
                country_iso2=country,
                area_code=index_area,
                segment=segment,
                target=date(year, 6, 30),
            )
            forecasts[code] = fc

    for row in rows:
        median = row["median_price"]
        basis = row.get("basis") or "TRANSACTIONS"
        has_level = median is not None

        # Growth from real order statistics where we have prices; otherwise the
        # growth published with the index itself.
        growth = row.get("stored_growth")
        if has_level and row.get("prev_median") and row["prev_median"] > 0:
            growth = round((median / row["prev_median"] - 1) * 100, 1)
        elif growth is not None:
            growth = round(float(growth), 1)

        # An index-only row with no growth figure says nothing at all.
        if not has_level and growth is None:
            continue

        confidence = None
        forecast_area = None
        if is_future:
            # A forecast projects a price level forward. With no level to
            # project, there is nothing defensible to show for a future year.
            if not has_level:
                continue
            fc = forecasts.get(row["area_code"])
            if fc is None:
                continue          # no defensible projection -> omit, never guess
            median = median * fc.ratio          # type: ignore[union-attr]
            confidence = fc.confidence          # type: ignore[union-attr]
            forecast_area = fc.area_name        # type: ignore[union-attr]

        areas.append(
            AreaStat(
                area_level=row["area_level"],
                area_code=row["area_code"],
                area_name=row["area_name"] or row["area_code"],
                latitude=row["latitude"], longitude=row["longitude"],
                year=year,
                median_price=round(median) if median is not None else None,
                p25_price=row["p25_price"], p75_price=row["p75_price"],
                median_price_per_sqm=row["median_price_per_sqm"],
                transaction_count=row["transaction_count"],
                currency=row["currency"] or currency,
                precision_level=precision,
                basis=basis,
                price_statistic=row.get("price_statistic") or "MEDIAN",
                index_value=row.get("index_value"),
                has_price_level=has_level,
                window_from_year=row.get("window_from_year") or year,
                window_to_year=row.get("window_to_year") or year,
                growth_1y_pct=growth,
                is_forecast=is_future,
                confidence=confidence,
                forecast_market=forecast_area,
            )
        )

    if not areas:
        return MapResponse(
            status=DataStatus.OUT_OF_RANGE,
            message=(
                f"A {year} projection is beyond what the available price index "
                "supports for this area."
                if is_future else
                f"No usable figures for {stats_year} in this area."
            ),
            tier=tier, year=year, data_year=stats_year, is_future=is_future,
            is_historical=year < today.year, currency=currency,
            attributions=attributions,
        )

    return MapResponse(
        status=DataStatus.OK, tier=tier, year=year, data_year=stats_year,
        is_future=is_future,
        is_historical=year < today.year, currency=currency, areas=areas,
        truncated=len(rows) >= limit, attributions=attributions,
    )


async def _property_tier(
    box: BoundingBox, tier: MapTier, year: int, segment: str, country: str,
    provider, currency: str, attributions: list[str], is_future: bool,
    today: date, zoom: float,
) -> MapResponse:
    lat_centre, _lon_centre = box.centre
    cell_x, cell_y = _grid_cell(zoom, lat_centre)
    rows = await fetch_all(
        _PROPERTY_SQL,
        {
            "country": country, "segment": segment, "w": box.west,
            "s": box.south, "e": box.east, "n": box.north,
            "cell_x": cell_x, "cell_y": cell_y,
            # Bound on how many candidates we consider before thinning, so a
            # very dense viewport cannot turn into an unbounded scan.
            "scan_limit": 6000,
            "limit": limit_for_tier(tier),
        },
    )
    if not rows:
        return MapResponse(
            status=DataStatus.NO_DATA,
            message="No recorded residential sales for any property in this view.",
            tier=tier, year=year, is_future=is_future,
            is_historical=year < today.year, currency=currency,
            attributions=attributions,
        )

    properties: list[PropertySummary] = []
    for row in rows:
        price, status, _msg = await provider.price_for_year(row["id"], year)
        if price is None or status is not DataStatus.OK:
            # Honest omission: a dwelling with no defensible figure for this
            # year simply has no marker (§20).
            continue

        lat, lon = _spread(
            row["lat"], row["lon"], row["slot"], row["in_postcode"], row["ring_deg"]
        )
        properties.append(
            PropertySummary(
                id=row["id"], latitude=lat, longitude=lon,
                coordinate_precision=row["coordinate_precision"],
                position_is_approximate=(
                    row["coordinate_precision"] != "PROPERTY"
                    or row["in_postcode"] > 1
                ),
                address_short=row["address_line"], postcode=row["postcode"],
                property_type=row["property_type"], price=price,
            )
        )

    if not properties:
        return MapResponse(
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            message=(
                f"No property here has enough evidence for a {year} value. "
                "Zoom out for area statistics."
            ),
            tier=tier, year=year, is_future=is_future,
            is_historical=year < today.year, currency=currency,
            attributions=attributions,
        )

    total_in_view = int(rows[0].get("total_in_view") or len(rows))
    return MapResponse(
        status=DataStatus.OK, tier=tier, year=year, is_future=is_future,
        is_historical=year < today.year, currency=currency,
        properties=properties, total_available=total_in_view,
        truncated=total_in_view > len(properties), attributions=attributions,
    )
