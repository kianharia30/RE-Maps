"""Ireland: real median prices from the Residential Property Price Register.

WHY THIS IS AGGREGATED, NOT TRANSACTION-LEVEL
---------------------------------------------
The register publishes every declared residential sale since 2010 — 805,000 of
them, with a real price in euro. That is the same grade of evidence as HM Land
Registry's Price Paid Data, and it is why Ireland can show actual numbers
rather than a growth rate.

What it does NOT publish is a usable location. Addresses are free text
("5 Braemor Drive, Churchtown, Co.Dublin"), the Eircode column is populated for
only a small minority of rows, and the Eircode-to-coordinate database is
commercially licensed - we cannot legally or practically place an individual
Irish dwelling on the map.

So the records are aggregated to county at ingest time and the individual rows
are not stored. That yields a real, defensible county median while making it
structurally impossible for the map to imply a dwelling-level figure we have no
right to claim.

QUALITY FILTERS
---------------
Two flags in the register mark prices that are not comparable market prices:

  * `Not Full Market Price` — the sale was not at arm's length (family
    transfers and similar). Excluded, exactly as HM Land Registry category B
    transfers are excluded for England and Wales.
  * `VAT Exclusive` — the declared figure excludes VAT, which applies to new
    dwellings. Mixing an ex-VAT new-build price with VAT-inclusive second-hand
    prices understates the former by 13.5%. Grossing them up would mean
    asserting a tax treatment we have not verified per sale, so they are
    excluded and the exclusion is documented.

The consequence is stated plainly in the source registry: Irish medians lean
towards second-hand dwellings.
"""
from __future__ import annotations

import csv
import logging
import re
import statistics
import zipfile
from pathlib import Path

from app.config import get_settings
from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import register_sources

log = logging.getLogger(__name__)

SOURCE_KEY = "ie_ppr"
DOWNLOAD_URL = (
    "https://propertypriceregister.ie/website/npsra/ppr/npsra-ppr.nsf/"
    "Downloads/PPR-ALL.zip/$FILE/PPR-ALL.zip"
)
# The register is published as Windows-1252, not UTF-8; the euro sign and Irish
# place names (Dún Laoghaire, Fíonghall) are mojibake without this.
ENCODING = "cp1252"

# The register names 26 traditional counties. Natural Earth splits Dublin into
# four administrative areas and Tipperary into two, and spells Laois in its
# older form. Mapped explicitly rather than fuzzily, so a boundary revision
# upstream surfaces as an unmatched county instead of a silently wrong shape.
COUNTY_TO_REGIONS: dict[str, tuple[str, ...]] = {
    # Natural Earth spells this with an en dash; kept verbatim so the join
    # matches.
    "Dublin": ("Dublin", "Dún Laoghaire–Rathdown", "Fingal", "South Dublin"),  # noqa: RUF001
    "Tipperary": ("North Tipperary", "South Tipperary"),
    "Laois": ("Laoighis",),
}

MIN_SALES = 30  # below this a county-year median is not worth publishing


def _price(raw: str | None) -> float | None:
    """'€343,000.00' -> 343000.0. Returns None for anything unparseable."""
    digits = re.sub(r"[^0-9.]", "", raw or "")
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def _year(raw: str | None) -> int | None:
    try:
        return int((raw or "").split("/")[-1])
    except ValueError:
        return None


def _csv_path(settings) -> Path:
    raw_dir = settings.raw_dir / "ie"
    csv_path = raw_dir / "PPR-ALL.csv"
    if csv_path.exists():
        return csv_path
    zip_path = raw_dir / "ppr.zip"
    if not zip_path.exists():
        raise FileNotFoundError(
            f"{zip_path} not found. Run `make data-download-ie` first."
        )
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(raw_dir)
    return csv_path


UPSERT = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, p25_price, p75_price, currency_code,
    growth_1y_pct, source_key, basis, region_id, geom, computed_at
)
SELECT 'IE', 'county', %(code)s, %(name)s, 'all', %(year)s,
       %(count)s, %(median)s, %(p25)s, %(p75)s, 'EUR',
       %(growth)s, %(source_key)s, 'TRANSACTIONS', r.id,
       ST_PointOnSurface(r.geom), now()
FROM (
    SELECT min(id) AS id, ST_Union(geom) AS geom
    FROM regions
    WHERE country_iso2 = 'IE' AND name = ANY(%(region_names)s::text[])
) r
WHERE r.id IS NOT NULL
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET transaction_count = EXCLUDED.transaction_count,
              median_price = EXCLUDED.median_price,
              p25_price = EXCLUDED.p25_price,
              p75_price = EXCLUDED.p75_price,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              region_id = EXCLUDED.region_id,
              geom = EXCLUDED.geom,
              basis = EXCLUDED.basis,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""


def ingest() -> dict[str, int]:
    settings = get_settings()
    register_sources()
    run_id = start_run(SOURCE_KEY, "PPR-ALL.csv")
    read = rejected = written = 0

    try:
        path = _csv_path(settings)
        # (county, year) -> list of prices
        buckets: dict[tuple[str, int], list[float]] = {}

        with path.open(encoding=ENCODING, newline="") as handle:
            for row in csv.DictReader(handle):
                read += 1
                county = (row.get("County") or "").strip()
                year = _year(row.get("Date of Sale (dd/mm/yyyy)"))
                # The price column header carries a euro sign, whose byte
                # differs by encoding; find it by prefix instead.
                price_key = next(
                    (k for k in row if k and k.startswith("Price")), None
                )
                price = _price(row.get(price_key) if price_key else None)

                if not county or year is None or price is None:
                    rejected += 1
                    continue
                # Not an arm's-length market price, or quoted without VAT.
                if (row.get("Not Full Market Price") or "").strip() != "No":
                    rejected += 1
                    continue
                if (row.get("VAT Exclusive") or "").strip() != "No":
                    rejected += 1
                    continue

                buckets.setdefault((county, year), []).append(price)

        counties = sorted({county for county, _ in buckets})
        log.info(
            "%s usable sales across %s counties",
            f"{sum(len(v) for v in buckets.values()):,}", len(counties),
        )

        # Medians first, so growth can be computed against the prior year.
        medians: dict[tuple[str, int], float] = {}
        for key, prices in buckets.items():
            if len(prices) >= MIN_SALES:
                medians[key] = statistics.median(prices)

        unmatched: set[str] = set()
        with sync_conn() as conn:
            for (county, year), prices in sorted(buckets.items()):
                if len(prices) < MIN_SALES:
                    continue
                ordered = sorted(prices)
                quartiles = statistics.quantiles(ordered, n=4)
                prior = medians.get((county, year - 1))
                median = medians[(county, year)]
                growth = (
                    round((median / prior - 1) * 100, 3)
                    if prior and prior > 0 else None
                )
                region_names = COUNTY_TO_REGIONS.get(county, (county,))
                with conn.cursor() as cur:
                    cur.execute(
                        UPSERT,
                        {
                            "code": f"IE-{county.upper().replace(' ', '-')}",
                            "name": county,
                            "year": year,
                            "count": len(prices),
                            "median": round(median, 2),
                            "p25": round(quartiles[0], 2),
                            "p75": round(quartiles[2], 2),
                            "growth": growth,
                            "source_key": SOURCE_KEY,
                            "region_names": list(region_names),
                        },
                    )
                    if cur.rowcount == 0:
                        unmatched.add(county)
                    else:
                        written += 1
            conn.commit()

        if unmatched:
            # Loud, because a county with no shape silently vanishes from the
            # map rather than failing.
            log.error(
                "no polygon matched for %s — these counties will not appear: %s",
                len(unmatched), ", ".join(sorted(unmatched)),
            )
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    log.info(
        "Ireland PPR: %s county-year medians written, %s rows read, %s excluded",
        f"{written:,}", f"{read:,}", f"{rejected:,}",
    )
    return {"read": read, "written": written, "rejected": rejected}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(ingest())
