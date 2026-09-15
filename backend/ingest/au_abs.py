"""Australia: median dwelling prices from the ABS.

The Australian Bureau of Statistics publishes RES_DWELL, "Residential
Dwellings: Unstratified Medians and Transfer Counts" — the median price of
actual property transfers, quarterly, by state and territory. That is a real
median of real sales, the same kind of figure as England and Wales or Ireland.

TWO THINGS THE SOURCE DOES THAT WILL BITE IF IGNORED
----------------------------------------------------
1. Values are published in THOUSANDS of dollars. Greater Sydney's 1522 means
   A$1,522,000. Ingesting the raw number would understate every Australian
   price by a factor of a thousand, and it would look plausible enough to
   survive a casual glance.

2. The REGION dimension mixes two geographies in one list: the eight states and
   territories, and the "Greater <capital>" / "Rest of <state>" split that sits
   inside them. Taking everything would double-count and would also mean
   markers for areas we hold no boundary for, so only the state codes are used.

SCOPE
-----
The figure is the MEAN price of the residential dwelling stock — total value
divided by number of dwellings — not a median and not a transaction price. It
describes what the existing housing stock is worth rather than what changed
hands, and means run above medians for skewed distributions. Labelled as a mean
throughout.

Quarterly figures are not averaged into an annual one; each year takes its
final published quarter.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import register_sources

log = logging.getLogger(__name__)

SOURCE_KEY = "au_abs_res_dwell"
# RES_DWELL_ST, measure 5: "Mean price of residential dwellings", by state.
#
# The sibling dataflow RES_DWELL publishes true MEDIANS, which would be
# preferable, but only for "Greater <capital>" and "Rest of <state>" areas --
# the state codes exist in its dimension list and carry no data. We hold no
# boundaries for that geography, and two medians cannot be combined into one
# (a median of medians is not a median), so the state-level mean is used
# instead and labelled as a mean.
URL = (
    "https://data.api.abs.gov.au/rest/data/ABS,RES_DWELL_ST,1.0.0/5..Q"
    "?format=jsondata"
)
# Published in thousands of dollars.
UNIT_MULTIPLIER = 1000

# State and territory codes, and the Natural Earth names they correspond to.
# The "Greater <capital>" and "Rest of <state>" codes in the same dimension are
# deliberately excluded: they nest inside these and we hold no boundaries for
# them.
NATIONAL_CODE = "AUS"
STATE_REGIONS = {
    "1": "New South Wales",
    "2": "Victoria",
    "3": "Queensland",
    "4": "South Australia",
    "5": "Western Australia",
    "6": "Tasmania",
    "7": "Northern Territory",
    "8": "Australian Capital Territory",
}

# The national row has no admin-1 shape to join to; the country polygon places
# it. Needed so a world-zoom viewport, which asks for a country-level figure,
# finds one.
UPSERT_NATIONAL = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, currency_code, growth_1y_pct,
    source_key, basis, price_statistic, geom, computed_at
)
SELECT 'AU', 'country', 'AU', 'Australia', 'all', %(year)s,
       NULL, %(price)s, 'AUD', %(growth)s,
       %(source_key)s, 'OFFICIAL_STATISTIC', 'MEAN',
       ST_PointOnSurface(geom), now()
FROM countries WHERE iso2 = 'AU'
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET median_price = EXCLUDED.median_price,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              basis = EXCLUDED.basis,
              price_statistic = EXCLUDED.price_statistic,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""

UPSERT = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, currency_code, growth_1y_pct,
    source_key, basis, price_statistic, region_id, geom, computed_at
)
SELECT 'AU', 'county', %(code)s, %(name)s, 'all', %(year)s,
       NULL, %(price)s, 'AUD', %(growth)s,
       %(source_key)s, 'OFFICIAL_STATISTIC', 'MEAN', r.id,
       ST_PointOnSurface(r.geom), now()
FROM regions r
WHERE r.country_iso2 = 'AU' AND r.name = %(name)s
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET median_price = EXCLUDED.median_price,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              currency_code = EXCLUDED.currency_code,
              basis = EXCLUDED.basis,
              price_statistic = EXCLUDED.price_statistic,
              region_id = EXCLUDED.region_id,
              geom = EXCLUDED.geom,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""


def ingest() -> dict[str, int]:
    register_sources()
    run_id = start_run(SOURCE_KEY, "RES_DWELL")
    written = skipped = 0

    try:
        req = urllib.request.Request(
            URL,
            headers={
                "Accept": "application/vnd.sdmx.data+json",
                "User-Agent": "RE-Maps/0.1 (property price map)",
            },
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read())

        structure = (
            payload["data"]["structures"][0]
            if "structures" in payload["data"]
            else payload["data"]["structure"]
        )
        series_dims = structure["dimensions"]["series"]
        periods = structure["dimensions"]["observation"][0]["values"]
        region_axis = next(
            i for i, d in enumerate(series_dims) if d["id"] == "REGION"
        )
        region_values = series_dims[region_axis]["values"]

        # (state name, year) -> (quarter ordinal, price), keeping the latest
        # quarter seen for each year.
        latest: dict[tuple[str, int], tuple[str, float]] = {}
        for key, series in payload["data"]["dataSets"][0]["series"].items():
            idx = [int(i) for i in key.split(":")]
            code = region_values[idx[region_axis]]["id"]
            name = (
                "Australia" if code == NATIONAL_CODE else STATE_REGIONS.get(code)
            )
            if name is None:
                continue        # an unmapped code
            for pos, observation in series["observations"].items():
                value = observation[0]
                if value is None:
                    continue
                period = periods[int(pos)]["id"]      # e.g. "2026-Q2"
                year = int(period[:4])
                current = latest.get((name, year))
                if current is None or period > current[0]:
                    latest[(name, year)] = (period, float(value))

        prices = {
            key: price * UNIT_MULTIPLIER for key, (_, price) in latest.items()
        }

        with sync_conn() as conn:
            for (name, year), price in sorted(prices.items()):
                prior = prices.get((name, year - 1))
                growth = (
                    round((price / prior - 1) * 100, 3)
                    if prior and prior > 0 else None
                )
                national = name == "Australia"
                with conn.cursor() as cur:
                    cur.execute(
                        UPSERT_NATIONAL if national else UPSERT,
                        {
                            "code": f"AU-{name[:18].upper().replace(' ', '-')}",
                            "name": name, "year": year,
                            "price": round(price, 2), "growth": growth,
                            "source_key": SOURCE_KEY,
                        },
                    )
                    if cur.rowcount == 0:
                        skipped += 1
                    else:
                        written += 1
            conn.commit()

        if skipped:
            log.error("no polygon matched for %s state-year rows", skipped)
    except Exception as exc:
        finish_run(run_id, "failed", len(prices), written, skipped, error=str(exc))
        raise

    finish_run(run_id, "complete", len(prices), written, skipped)
    log.info("Australia ABS: %s state-year mean prices", f"{written:,}")
    return {"written": written, "skipped": skipped}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(ingest())
