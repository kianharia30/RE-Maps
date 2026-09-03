"""Ingest official house-price statistics for countries beyond the
transaction-level providers.

WHAT THIS IS FOR
----------------
Most countries do not publish individual property sales. Many DO publish an
official house price index through their statistics office. That is real data,
and showing it is far better than showing nothing — provided we are exact about
what it is.

An index is NOT a price. Eurostat's series is based at 100 in 2015; it says
prices rose 4.2% last year, not that a house costs EUR 380,000. Turning an
index into a price level would require a base-year price we do not have, so
these rows are stored with `median_price = NULL` and
`basis = 'OFFICIAL_INDEX'`, and the API tells the frontend there is no price
level via `has_price_level = false`.

Sources implemented here
------------------------
  Eurostat prc_hpi_q   31 European countries, quarterly index (no key)
  US FHFA HPI          51 states + DC, monthly index      (no key)

Both are index-only. Sources that publish real price LEVELS live in their own
modules (`ingest/ie_ppr.py`, `ingest/sg_hdb.py`) because they are derived from
real transactions and go through the normal transaction pipeline.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import urllib.request
from datetime import date

from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import register_sources

log = logging.getLogger(__name__)

EUROSTAT_KEY = "eurostat_hpi"
FHFA_KEY = "us_fhfa_hpi"

EUROSTAT_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    "prc_hpi_q?format=JSON&lang=EN&purchase=TOTAL&unit=I15_Q"
)
FHFA_URL = "https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv"

# Eurostat `geo` codes that are aggregates, not countries. Including them would
# put a marker for "the Euro area" in the middle of Europe.
EUROSTAT_AGGREGATES = {
    "EU", "EU27_2020", "EU28", "EU15", "EU25", "EA", "EA11", "EA12", "EA13",
    "EA15", "EA16", "EA17", "EA18", "EA19", "EA20", "EA21",
}
# Eurostat uses two codes that differ from ISO 3166-1 alpha-2.
EUROSTAT_ISO_FIXUP = {"EL": "GR", "UK": "GB"}


def _fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "RE-Maps/0.1 (property price map)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _quarter_to_date(period: str) -> date | None:
    """Eurostat periods look like '2026-Q1'."""
    try:
        year_s, q_s = period.split("-Q")
        return date(int(year_s), (int(q_s) - 1) * 3 + 1, 1)
    except (ValueError, AttributeError):
        return None


UPSERT_INDEX = """
INSERT INTO market_indices (
    source_key, country_iso2, area_code, area_name, area_level, segment,
    period, average_price, index_value, sales_volume, pct_change_12m,
    currency_code
) VALUES (
    %(source_key)s, %(country)s, %(area_code)s, %(area_name)s, %(area_level)s,
    'all', %(period)s, NULL, %(index_value)s, NULL, %(pct_12m)s, %(currency)s
)
ON CONFLICT (source_key, area_code, segment, period) DO UPDATE SET
    index_value = EXCLUDED.index_value,
    pct_change_12m = EXCLUDED.pct_change_12m,
    area_name = EXCLUDED.area_name,
    -- Must be refreshed: omitting it left a stale, wrong currency in place
    -- (Hungarian figures stayed labelled EUR after the mapping was corrected).
    currency_code = EXCLUDED.currency_code;
"""


# ---------------------------------------------------------------------------
# Eurostat: 31 European countries
# ---------------------------------------------------------------------------

def ingest_eurostat() -> dict[str, int]:
    register_sources()
    run_id = start_run(EUROSTAT_KEY, "prc_hpi_q")
    read = written = rejected = 0

    try:
        payload = json.loads(_fetch(EUROSTAT_URL))
        geo_index = payload["dimension"]["geo"]["category"]["index"]
        geo_label = payload["dimension"]["geo"]["category"]["label"]
        time_index = payload["dimension"]["time"]["category"]["index"]
        values = payload["value"]

        # JSON-stat flattens a multi-dimensional cube into one array. The only
        # dimensions with more than one category here are geo and time, so the
        # linear position is geo_position * n_time + time_position.
        n_time = len(time_index)
        time_by_pos = {pos: period for period, pos in time_index.items()}

        # index_value keyed by (iso2, period) so we can compute growth after.
        series: dict[str, dict[date, float]] = {}
        names: dict[str, str] = {}

        for geo_code, geo_pos in geo_index.items():
            if geo_code in EUROSTAT_AGGREGATES:
                continue
            iso2 = EUROSTAT_ISO_FIXUP.get(geo_code, geo_code)
            if len(iso2) != 2:
                continue
            names[iso2] = geo_label.get(geo_code, iso2)
            for time_pos in range(n_time):
                read += 1
                raw = values.get(str(geo_pos * n_time + time_pos))
                if raw is None:
                    continue
                period = _quarter_to_date(time_by_pos[time_pos])
                if period is None:
                    rejected += 1
                    continue
                series.setdefault(iso2, {})[period] = float(raw)

        with sync_conn() as conn:
            for iso2, points in series.items():
                # Only claim a country we can actually place on the map.
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT currency_code FROM countries WHERE iso2 = %s", (iso2,)
                    )
                    row = cur.fetchone()
                if row is None:
                    log.warning("eurostat: %s is not in the countries table", iso2)
                    continue
                # No fallback: a wrong currency is a fabricated fact. If we do
                # not know it, the row carries NULL and the UI omits it.
                currency = row["currency_code"]

                for period, index_value in sorted(points.items()):
                    # Year-on-year change from the same real series, four
                    # quarters back. Left NULL when that quarter is missing.
                    prior = points.get(date(period.year - 1, period.month, 1))
                    pct = (
                        round((index_value / prior - 1) * 100, 3)
                        if prior and prior > 0 else None
                    )
                    with conn.cursor() as cur:
                        cur.execute(
                            UPSERT_INDEX,
                            {
                                "source_key": EUROSTAT_KEY,
                                "country": iso2,
                                "area_code": f"EUROSTAT-{iso2}",
                                "area_name": names[iso2],
                                "area_level": "country",
                                "period": period,
                                "index_value": index_value,
                                "pct_12m": pct,
                                "currency": currency,
                            },
                        )
                    written += 1
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    log.info(
        "Eurostat HPI: %s countries, %s index points",
        f"{len(series):,}", f"{written:,}",
    )
    return {"countries": len(series), "rows": written}


# ---------------------------------------------------------------------------
# US FHFA: 51 states + DC
# ---------------------------------------------------------------------------

# The FHFA master file carries several index flavours. "purchase-only" is the
# repeat-sales index FHFA itself headlines, and "all-transactions" reaches
# further back; we take purchase-only where present and fall back per place.
FHFA_PREFERRED_FLAVOURS = ("purchase-only", "all-transactions")

# Two-letter state codes are what FHFA uses in `place_id` at State level.
def ingest_fhfa() -> dict[str, int]:
    register_sources()
    run_id = start_run(FHFA_KEY, "hpi_master.csv")
    read = written = rejected = 0

    try:
        raw = _fetch(FHFA_URL, timeout=180)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace")))

        # (place_id, place_name) -> {period: index}
        chosen_flavour: dict[str, str] = {}
        series: dict[tuple[str, str], dict[date, float]] = {}

        for row in reader:
            read += 1
            level = row.get("level")
            place_id_raw = (row.get("place_id") or "").strip()
            # Keep the 51 state series AND the single national series. Without
            # the national row, a viewport too wide to contain any state's
            # point had nothing to fall back to and the whole United States
            # reported no data.
            if level == "State":
                area_level = "state"
            elif level == "USA or Census Division" and place_id_raw == "USA":
                area_level = "country"
            else:
                continue
            flavour = row.get("hpi_flavor", "")
            if flavour not in FHFA_PREFERRED_FLAVOURS:
                continue
            place_id = place_id_raw
            place_name = (row.get("place_name") or "").strip()
            if not place_id or not place_name:
                rejected += 1
                continue

            # One flavour per place, preferring purchase-only.
            best = chosen_flavour.get(place_id)
            if best is None or FHFA_PREFERRED_FLAVOURS.index(flavour) < \
                    FHFA_PREFERRED_FLAVOURS.index(best):
                chosen_flavour[place_id] = flavour
            if flavour != chosen_flavour[place_id]:
                continue

            index_raw = row.get("index_nsa") or row.get("index_sa") or ""
            try:
                index_value = float(index_raw)
            except ValueError:
                continue

            year_s = row.get("yr") or ""
            period_s = row.get("period") or ""
            frequency = row.get("frequency") or ""
            try:
                year = int(year_s)
                num = int(period_s)
            except ValueError:
                rejected += 1
                continue
            if frequency == "monthly":
                month = num
            elif frequency == "quarterly":
                month = (num - 1) * 3 + 1
            else:
                continue
            if not 1 <= month <= 12:
                rejected += 1
                continue

            series.setdefault((place_id, place_name, area_level), {})[
                date(year, month, 1)
            ] = index_value

        with sync_conn() as conn:
            for (place_id, place_name, area_level), points in series.items():
                for period, index_value in sorted(points.items()):
                    prior = points.get(date(period.year - 1, period.month, 1))
                    pct = (
                        round((index_value / prior - 1) * 100, 3)
                        if prior and prior > 0 else None
                    )
                    with conn.cursor() as cur:
                        cur.execute(
                            UPSERT_INDEX,
                            {
                                "source_key": FHFA_KEY,
                                "country": "US",
                                "area_code": f"FHFA-{place_id}",
                                "area_name": (
                                    "United States" if area_level == "country"
                                    else place_name
                                ),
                                "area_level": area_level,
                                "period": period,
                                "index_value": index_value,
                                "pct_12m": pct,
                                "currency": "USD",
                            },
                        )
                    written += 1
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    log.info("FHFA HPI: %s series, %s index points", len(series), f"{written:,}")
    return {"series": len(series), "rows": written}


def ingest() -> dict:
    import asyncio

    result = {"eurostat": ingest_eurostat(), "fhfa": ingest_fhfa()}
    result["map_rows"] = asyncio.run(build_area_stats())
    return result




# ---------------------------------------------------------------------------
# Turning index series into map-renderable area statistics
# ---------------------------------------------------------------------------
#
# `area_stats` is what the map reads. Index-only areas need a row per year with
# the index level and the growth rate, and a coordinate to place the marker on.
#
# Coordinates come from two places, neither of them invented:
#   * country level — ST_PointOnSurface of the country polygon we already hold
#     (PointOnSurface rather than Centroid, so a crescent-shaped country's
#     marker still lands on its own territory);
#   * sub-national — geocoded once through the normal Nominatim client, which
#     is throttled and caches every result in Postgres.

AREA_STATS_FROM_INDEX = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, currency_code, index_value,
    growth_1y_pct, source_key, basis, geom, computed_at
)
SELECT m.country_iso2, %(area_level)s, m.area_code, max(m.area_name), 'all',
       extract(year FROM m.period)::smallint,
       0,                       -- publishers do not disclose the sample size
       NULL,                    -- an index carries NO price level
       max(m.currency_code),
       -- The year's figure is its final published period.
       (array_agg(m.index_value    ORDER BY m.period DESC))[1],
       (array_agg(m.pct_change_12m ORDER BY m.period DESC))[1],
       %(source_key)s, 'OFFICIAL_INDEX',
       ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), now()
FROM market_indices m
WHERE m.source_key = %(source_key)s AND m.area_code = %(area_code)s
  AND m.index_value IS NOT NULL
GROUP BY m.country_iso2, m.area_code, extract(year FROM m.period)
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET index_value = EXCLUDED.index_value,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              area_name = EXCLUDED.area_name,
              geom = EXCLUDED.geom,
              basis = EXCLUDED.basis,
              source_key = EXCLUDED.source_key,
              currency_code = EXCLUDED.currency_code,
              computed_at = now();
"""


async def _area_point(
    source_key: str, area_code: str, area_name: str, country_iso2: str, level: str
) -> tuple[float, float] | None:
    """A real coordinate for an index area, or None if we cannot place it."""
    from app.db import fetch_one

    if level == "country":
        row = await fetch_one(
            "SELECT ST_Y(p) AS lat, ST_X(p) AS lon FROM "
            "(SELECT ST_PointOnSurface(geom) AS p FROM countries WHERE iso2 = %s) q",
            (country_iso2,),
        )
        return (row["lat"], row["lon"]) if row else None

    # Sub-national: ask the geocoder once; the result is cached in Postgres.
    #
    # The query is disambiguated and the result type is checked, because many
    # US states share a name with a city inside them. Asking plainly for
    # "New York, United States" returned New York CITY, which put the marker
    # for New York State on Manhattan. Requesting "New York State" and then
    # requiring a state-shaped result fixes it.
    from app.geocode.nominatim import get_geocoder

    country_row = await fetch_one(
        "SELECT name FROM countries WHERE iso2 = %s", (country_iso2,)
    )
    country_name = (country_row or {}).get("name", country_iso2)

    expected_kinds = {"state", "region", "county"} if level == "state" else None
    queries = (
        [f"{area_name} State, {country_name}", f"{area_name}, {country_name}"]
        if level == "state"
        else [f"{area_name}, {country_name}"]
    )

    fallback: tuple[float, float] | None = None
    for query in queries:
        results = await get_geocoder().search(query, limit=5)
        in_country = [
            r for r in results
            if (r.country_iso2 or "").upper() == country_iso2
        ]
        for result in in_country:
            if expected_kinds is None or result.kind in expected_kinds:
                return (result.latitude, result.longitude)
        if fallback is None and in_country:
            fallback = (in_country[0].latitude, in_country[0].longitude)

    if fallback is not None:
        log.warning(
            "%s (%s): no %s-level match, using the best available result",
            area_name, area_code, level,
        )
        return fallback
    log.warning("could not place %s (%s) on the map", area_name, area_code)
    return None


async def build_area_stats() -> dict[str, int]:
    """Create map rows for every index-only area we hold."""
    from app.db import close_async_pool, fetch_all

    placed = skipped = 0
    try:
        areas = await fetch_all(
            """
            SELECT DISTINCT source_key, area_code, area_level, country_iso2,
                   max(area_name) AS area_name
            FROM market_indices
            WHERE source_key IN (%s, %s)
            GROUP BY source_key, area_code, area_level, country_iso2
            ORDER BY source_key, area_code
            """,
            (EUROSTAT_KEY, FHFA_KEY),
        )
        log.info("placing %s index areas on the map", len(areas))

        with sync_conn() as conn:
            for area in areas:
                point = await _area_point(
                    area["source_key"], area["area_code"], area["area_name"],
                    area["country_iso2"], area["area_level"],
                )
                if point is None:
                    skipped += 1
                    continue
                lat, lon = point
                with conn.cursor() as cur:
                    cur.execute(
                        AREA_STATS_FROM_INDEX,
                        {
                            "source_key": area["source_key"],
                            "area_code": area["area_code"],
                            "area_level": area["area_level"],
                            "lat": lat, "lon": lon,
                        },
                    )
                conn.commit()
                placed += 1
                if placed % 20 == 0:
                    log.info("  placed %s/%s", placed, len(areas))

            with conn.cursor() as cur:
                cur.execute("ANALYZE area_stats")
            conn.commit()
    finally:
        await close_async_pool()

    log.info("index area statistics: %s placed, %s unplaceable", placed, skipped)
    return {"placed": placed, "skipped": skipped}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(ingest())
