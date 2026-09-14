"""Singapore: real median resale prices from HDB transaction records.

data.gov.sg publishes every Housing & Development Board resale transaction —
240,000 of them since 2017, each with the actual price paid. That is real
money from real sales, so Singapore gets a median rather than a growth rate.

WHAT THIS COVERS, AND WHAT IT DOES NOT
--------------------------------------
HDB flats are public housing and house roughly four out of five Singapore
residents, so this is a large and meaningful slice of the market. It is NOT the
whole market: private condominiums and landed property are sold under a
different regime and are absent from this dataset entirely. Those are the
expensive end, so a Singapore figure derived from HDB resales sits well below
an all-market figure and must never be presented as "the price of a home in
Singapore".

The coverage note and the source registry both state this, because the number
is only honest with that qualification attached.

WHY IT IS NATIONAL RATHER THAN PER TOWN
---------------------------------------
The records name an HDB town (26 of them) and a block and street. Placing those
would need town or planning-area boundaries, which are not in the Natural Earth
admin-1 dataset held here — it has five regions for Singapore that do not
correspond to HDB towns. Rather than guess a town-to-region mapping from
memory, the figure is published for Singapore as a whole, which the country
polygon can place exactly.
"""
from __future__ import annotations

import json
import logging
import statistics
import urllib.parse
import urllib.request

from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import register_sources

log = logging.getLogger(__name__)

SOURCE_KEY = "sg_hdb_resale"
RESOURCE_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
BASE = "https://data.gov.sg/api/action/datastore_search"
PAGE = 10_000
MIN_SALES = 30

UPSERT = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, p25_price, p75_price,
    median_price_per_sqm, currency_code, growth_1y_pct, source_key,
    basis, price_statistic, geom, computed_at
)
SELECT 'SG', 'country', 'SG', 'Singapore', 'all', %(year)s,
       %(count)s, %(median)s, %(p25)s, %(p75)s, %(per_sqm)s, 'SGD',
       %(growth)s, %(source_key)s, 'TRANSACTIONS', 'MEDIAN',
       ST_PointOnSurface(geom), now()
FROM countries WHERE iso2 = 'SG'
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET transaction_count = EXCLUDED.transaction_count,
              median_price = EXCLUDED.median_price,
              p25_price = EXCLUDED.p25_price,
              p75_price = EXCLUDED.p75_price,
              median_price_per_sqm = EXCLUDED.median_price_per_sqm,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              currency_code = EXCLUDED.currency_code,
              basis = EXCLUDED.basis,
              price_statistic = EXCLUDED.price_statistic,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""


def _fetch_page(offset: int) -> list[dict]:
    query = urllib.parse.urlencode(
        {"resource_id": RESOURCE_ID, "limit": PAGE, "offset": offset}
    )
    req = urllib.request.Request(
        f"{BASE}?{query}",
        headers={"User-Agent": "RE-Maps/0.1 (property price map)"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["result"].get("records", [])


def ingest() -> dict[str, int]:
    register_sources()
    run_id = start_run(SOURCE_KEY, RESOURCE_ID)
    read = written = rejected = 0

    try:
        # year -> (prices, price per square metre)
        buckets: dict[int, tuple[list[float], list[float]]] = {}
        offset = 0
        while True:
            records = _fetch_page(offset)
            if not records:
                break
            for record in records:
                read += 1
                month = (record.get("month") or "").strip()
                try:
                    year = int(month.split("-")[0])
                    price = float(record.get("resale_price") or 0)
                    area = float(record.get("floor_area_sqm") or 0)
                except ValueError:
                    rejected += 1
                    continue
                if price <= 0 or year < 1990:
                    rejected += 1
                    continue
                prices, per_sqm = buckets.setdefault(year, ([], []))
                prices.append(price)
                if area > 0:
                    per_sqm.append(price / area)
            offset += PAGE
            if offset % 50_000 == 0:
                log.info("  read %s records", f"{offset:,}")

        log.info(
            "%s resale records across %s years", f"{read:,}", len(buckets)
        )

        medians = {
            year: statistics.median(prices)
            for year, (prices, _) in buckets.items()
            if len(prices) >= MIN_SALES
        }

        with sync_conn() as conn:
            for year, (prices, per_sqm) in sorted(buckets.items()):
                if len(prices) < MIN_SALES:
                    continue
                quartiles = statistics.quantiles(sorted(prices), n=4)
                prior = medians.get(year - 1)
                median = medians[year]
                growth = (
                    round((median / prior - 1) * 100, 3)
                    if prior and prior > 0 else None
                )
                with conn.cursor() as cur:
                    cur.execute(
                        UPSERT,
                        {
                            "year": year,
                            "count": len(prices),
                            "median": round(median, 2),
                            "p25": round(quartiles[0], 2),
                            "p75": round(quartiles[2], 2),
                            "per_sqm": (
                                round(statistics.median(per_sqm), 2)
                                if per_sqm else None
                            ),
                            "growth": growth,
                            "source_key": SOURCE_KEY,
                        },
                    )
                    # Read inside the block: a closed cursor reports -1.
                    written += max(cur.rowcount, 0)
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    log.info("Singapore HDB: %s yearly medians from %s sales",
             written, f"{read:,}")
    return {"read": read, "written": written, "rejected": rejected}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(ingest())
