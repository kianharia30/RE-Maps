"""United States: median home value per county, from the Census ACS.

WHAT THIS FIGURE IS — AND IS NOT
--------------------------------
The variable is B25077_001E, "Median value (dollars)" for owner-occupied
housing units, from the American Community Survey 5-year estimates. The ACS
asks the occupant what they believe the property would sell for; the published
median is the middle of those self-reported answers.

That is NOT a transaction price. England and Wales, Ireland and Singapore
figures are the middle of what people actually paid. A US county figure is the
middle of what owners think their homes are worth, across the whole
owner-occupied stock rather than the subset that changed hands. Owner estimates
are known to run above market outcomes.

Both are legitimate official statistics and neither is a substitute for the
other, so rows written here carry `basis = 'OWNER_ESTIMATE'` and the map labels
them "est. value" rather than "median". Renters are excluded by construction,
which matters most in the dense urban counties where owner-occupation is
lowest.

MARGIN OF ERROR
---------------
The ACS is a survey, so every estimate has a margin of error, fetched here as
B25077_001M. Counties whose margin exceeds a third of the estimate are rejected
outright: at that width the figure carries no usable information, and a
sparsely populated county can easily be in that state.

A KEY IS REQUIRED
-----------------
Without CENSUS_API_KEY the endpoint returns an HTML "Missing Key" page with
HTTP 200 — not a JSON error — so a naive client silently ingests nothing. The
key is free: https://api.census.gov/data/key_signup.html
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import register_sources

log = logging.getLogger(__name__)

SOURCE_KEY = "us_census_acs"
BASE = "https://api.census.gov/data"
VALUE_VAR = "B25077_001E"     # median value, dollars
MARGIN_VAR = "B25077_001M"    # margin of error at 90% confidence

# The ACS 5-year release lags the calendar; the newest is tried first and the
# ingester walks back until one responds, rather than hard-coding a year that
# expires.
CANDIDATE_YEARS = (2023, 2022, 2021)

# Census null sentinels. -666666666 means "estimate not available".
NULL_SENTINELS = {"-666666666", "-999999999", "", "null", None}

# Reject an estimate this uncertain: the interval is too wide to mean anything.
MAX_MARGIN_RATIO = 1 / 3


def _api_key() -> str:
    # Read through Settings, not os.environ: the key lives in backend/.env,
    # which only the settings loader reads. Reading the environment directly
    # would report "not set" for a key that is present in the file.
    from app.config import get_settings

    key = (get_settings().census_api_key or os.environ.get("CENSUS_API_KEY", "")).strip()
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is not set. The Census endpoint returns an HTML "
            '"Missing Key" page with HTTP 200 rather than an error, so this '
            "check exists to avoid silently ingesting nothing. Get a free key "
            "at https://api.census.gov/data/key_signup.html and add it to "
            "backend/.env as CENSUS_API_KEY=..."
        )
    return key


def _fetch(year: int, key: str) -> list[list[str]] | None:
    query = urllib.parse.urlencode({
        "get": f"NAME,{VALUE_VAR},{MARGIN_VAR}",
        "for": "county:*",
        "key": key,
    })
    url = f"{BASE}/{year}/acs/acs5?{query}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "RE-Maps/0.1 (property price map)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        log.info("ACS %s unavailable (HTTP %s)", year, exc.code)
        return None
    # An invalid or unactivated key yields HTML with a 200 status.
    if raw.lstrip()[:1] != b"[":
        snippet = raw[:200].decode("utf-8", errors="replace")
        if "key" in snippet.lower():
            raise RuntimeError(
                "The Census API rejected the key. If it was just requested, "
                "the activation link in the email must be clicked within 48 "
                f"hours. Response began: {snippet[:120]!r}"
            )
        log.info("ACS %s returned no JSON", year)
        return None
    return json.loads(raw)


UPSERT = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, currency_code, source_key,
    basis, price_statistic, region_id, geom, computed_at
)
SELECT 'US', 'county', %(fips)s, %(name)s, 'all', %(year)s,
       NULL, %(value)s, 'USD', %(source_key)s,
       'OWNER_ESTIMATE', 'MEDIAN', r.id, ST_PointOnSurface(r.geom), now()
FROM regions r
WHERE r.country_iso2 = 'US' AND r.postal_code = %(fips)s
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET median_price = EXCLUDED.median_price,
              area_name = EXCLUDED.area_name,
              basis = EXCLUDED.basis,
              price_statistic = EXCLUDED.price_statistic,
              region_id = EXCLUDED.region_id,
              geom = EXCLUDED.geom,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""


def ingest() -> dict[str, int]:
    register_sources()
    key = _api_key()
    run_id = start_run(SOURCE_KEY, "acs5/B25077")
    read = written = rejected = unplaced = 0
    year_used = None

    try:
        payload = None
        for year in CANDIDATE_YEARS:
            payload = _fetch(year, key)
            if payload:
                year_used = year
                break
        if not payload:
            raise RuntimeError(
                f"No ACS 5-year release responded for {CANDIDATE_YEARS}"
            )

        header, *rows = payload
        col = {name: i for i, name in enumerate(header)}
        log.info("ACS %s: %s counties returned", year_used, f"{len(rows):,}")

        with sync_conn() as conn:
            for row in rows:
                read += 1
                raw_value = row[col[VALUE_VAR]]
                raw_margin = row[col[MARGIN_VAR]]
                if raw_value in NULL_SENTINELS:
                    rejected += 1
                    continue
                try:
                    value = float(raw_value)
                except ValueError:
                    rejected += 1
                    continue
                if value <= 0:
                    rejected += 1
                    continue

                # Discard estimates too uncertain to mean anything.
                try:
                    margin = float(raw_margin)
                except (ValueError, TypeError):
                    margin = 0.0
                if margin > 0 and margin / value > MAX_MARGIN_RATIO:
                    rejected += 1
                    continue

                fips = f"{row[col['state']]}{row[col['county']]}"
                # "Autauga County, Alabama" -> "Autauga County"
                name = (row[col["NAME"]] or "").split(",")[0].strip()

                with conn.cursor() as cur:
                    cur.execute(
                        UPSERT,
                        {
                            "fips": fips, "name": name, "year": year_used,
                            "value": round(value, 2), "source_key": SOURCE_KEY,
                        },
                    )
                    if cur.rowcount == 0:
                        # No boundary for this FIPS (territories, or a county
                        # created since the boundary file was published).
                        unplaced += 1
                    else:
                        written += 1
                if written % 500 == 0 and written:
                    conn.commit()
            with conn.cursor() as cur:
                cur.execute("ANALYZE area_stats")
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    log.info(
        "US Census ACS %s: %s counties written, %s rejected (missing or too "
        "uncertain), %s had no boundary",
        year_used, f"{written:,}", f"{rejected:,}", f"{unplaced:,}",
    )
    return {
        "year": year_used, "read": read, "written": written,
        "rejected": rejected, "unplaced": unplaced,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(ingest())
