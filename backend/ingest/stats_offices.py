"""Real price levels published by national statistics offices.

WHY THIS IS SEPARATE FROM THE INDEX INGESTER
--------------------------------------------
`world_stats.py` loads house price *indices* — series based at 100 in a
reference year, which carry no monetary value. This module loads actual money:
average sale prices in euro and kroner, published by the national statistics
office of each country.

THE STATISTIC IS NOT THE SAME ONE
---------------------------------
We compute MEDIANS from individual sales. These offices publish MEANS
("gemiddelde verkoopprijs", "gennemsnitlig pris pr. ejendom"). House prices are
right-skewed, so the mean typically sits well above the median — presenting one
as the other would overstate typical prices by a noticeable margin. Every row
written here is therefore marked `price_statistic = 'MEAN'`, and the map labels
it as an average rather than a median.

WHAT IS AGGREGATED, AND HOW EXACTLY
-----------------------------------
Where a source publishes quarterly figures we need an annual one. A plain mean
of four quarterly means is only correct if each quarter had the same number of
sales, which is never true. Both sources here publish the sale count alongside
the price, so the annual figure is a properly weighted mean:

    annual mean = sum(quarter mean x quarter sales) / sum(quarter sales)

which is exactly the mean over the year's sales, not an approximation of it.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.db import sync_conn

from .runs import finish_run, start_run
from .sources import register_sources

log = logging.getLogger(__name__)

NL_SOURCE_KEY = "nl_cbs_prices"
DK_SOURCE_KEY = "dk_statbank_prices"
SE_SOURCE_KEY = "se_scb_prices"

# CBS 83625NED: "Bestaande koopwoningen; gemiddelde verkoopprijzen, regio".
# Annual, 1995 onwards, in euro.
# Scoped to the national row and the provinces. CBS refuses any query
# returning more than 10,000 records, and this table also carries every
# municipality, which is well past that — and unusable here anyway, since no
# municipal boundaries are held.
NL_URL = (
    "https://opendata.cbs.nl/ODataApi/odata/83625NED/TypedDataSet"
    "?$filter=substringof('JJ',Perioden)"
    "%20and%20(startswith(RegioS,'PV')%20or%20startswith(RegioS,'NL'))"
)
# CBS province codes carry a "(PV)" suffix; one name differs from the boundary
# dataset (CBS uses the West Frisian "Fryslân", Natural Earth the Dutch
# "Friesland"). Mapped explicitly so a rename upstream surfaces as an
# unmatched region rather than a silently missing province.
NL_REGION_ALIASES = {"Fryslân": "Friesland"}

DK_URL = "https://api.statbank.dk/v1/data"
# Danmarks Statistik EJEN77, quarterly, in thousands of kroner.
#   EJENDOMSKATE 0111 = single-family houses
#   BNØGLE 3 = average price per property, 2 = number of sales (the weight)
#   OVERDRAG 1 = "almindelig fri handel", an ordinary arm's-length sale.
# Family transfers and other non-market sales are excluded, matching the
# treatment of HM Land Registry category B transfers and Ireland's
# "not full market price" flag.
DK_REGION_ALIASES = {"Sjælland": "Sjaælland"}  # Natural Earth's spelling
DK_AREA_CODES = ["000", "081", "082", "083", "084", "085"]


def _get_json(url: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "RE-Maps/0.1 (property price map)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


UPSERT = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, currency_code, growth_1y_pct,
    source_key, basis, price_statistic, region_id, geom, computed_at
)
SELECT %(country)s, %(level)s, %(code)s, %(name)s, 'all', %(year)s,
       %(count)s, %(price)s, %(currency)s, %(growth)s,
       %(source_key)s, 'OFFICIAL_STATISTIC', 'MEAN', r.id,
       ST_PointOnSurface(r.geom), now()
FROM (
    SELECT min(id) AS id, ST_Union(geom) AS geom
    FROM regions WHERE country_iso2 = %(country)s AND name = ANY(%(names)s::text[])
) r
WHERE r.id IS NOT NULL
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET median_price = EXCLUDED.median_price,
              transaction_count = EXCLUDED.transaction_count,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              currency_code = EXCLUDED.currency_code,
              basis = EXCLUDED.basis,
              price_statistic = EXCLUDED.price_statistic,
              region_id = EXCLUDED.region_id,
              geom = EXCLUDED.geom,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""

# A country-level row needs no region shape; the country polygon supplies it.
UPSERT_COUNTRY = """
INSERT INTO area_stats (
    country_iso2, area_level, area_code, area_name, segment, year,
    transaction_count, median_price, currency_code, growth_1y_pct,
    source_key, basis, price_statistic, geom, computed_at
)
SELECT %(country)s, 'country', %(code)s, %(name)s, 'all', %(year)s,
       %(count)s, %(price)s, %(currency)s, %(growth)s,
       %(source_key)s, 'OFFICIAL_STATISTIC', 'MEAN',
       ST_PointOnSurface(geom), now()
FROM countries WHERE iso2 = %(country)s
ON CONFLICT (country_iso2, area_level, area_code, segment, year)
DO UPDATE SET median_price = EXCLUDED.median_price,
              transaction_count = EXCLUDED.transaction_count,
              growth_1y_pct = EXCLUDED.growth_1y_pct,
              currency_code = EXCLUDED.currency_code,
              basis = EXCLUDED.basis,
              price_statistic = EXCLUDED.price_statistic,
              source_key = EXCLUDED.source_key,
              computed_at = now();
"""


def _write(conn, rows: dict[tuple[str, str, int], tuple[float, int | None, str]],
           country: str, currency: str, source_key: str) -> int:
    """Write (level, name, year) -> (price, count, code) with growth."""
    written = 0
    unmatched: set[str] = set()
    for (level, name, year), (price, count, code) in sorted(rows.items()):
        prior = rows.get((level, name, year - 1))
        growth = (
            round((price / prior[0] - 1) * 100, 3)
            if prior and prior[0] > 0 else None
        )
        params = {
            "country": country, "level": level, "code": code, "name": name,
            "year": year, "count": count, "price": round(price, 2),
            "currency": currency, "growth": growth, "source_key": source_key,
            "names": [name],
        }
        with conn.cursor() as cur:
            cur.execute(UPSERT_COUNTRY if level == "country" else UPSERT, params)
            if cur.rowcount == 0:
                unmatched.add(name)
            else:
                written += 1
    if unmatched:
        log.error(
            "no polygon matched, these areas will not appear on the map: %s",
            ", ".join(sorted(unmatched)),
        )
    return written


def ingest_netherlands() -> dict[str, int]:
    register_sources()
    run_id = start_run(NL_SOURCE_KEY, "cbs-83625NED")
    read = written = rejected = 0
    try:
        payload = _get_json(NL_URL)
        titles = {
            r["Key"].strip(): r["Title"].strip()
            for r in _get_json(
                "https://opendata.cbs.nl/ODataApi/odata/83625NED/RegioS"
            )["value"]
        }

        rows: dict[tuple[str, str, int], tuple[float, int | None, str]] = {}
        for record in payload["value"]:
            read += 1
            code = (record.get("RegioS") or "").strip()
            price = record.get("GemiddeldeVerkoopprijs_1")
            period = (record.get("Perioden") or "").strip()
            if price is None or not period.endswith("JJ00"):
                rejected += 1
                continue
            # Only the national row and the twelve provinces: municipality
            # (GM) rows have no boundary data here, and the macro-regions (LD)
            # would double-count the provinces inside them.
            if code == "NL01":
                level, name = "country", "Netherlands"
            elif code.startswith("PV"):
                level = "county"
                raw = titles.get(code, code).replace("(PV)", "").strip()
                name = NL_REGION_ALIASES.get(raw, raw)
            else:
                continue
            rows[(level, name, int(period[:4]))] = (float(price), None, code)

        with sync_conn() as conn:
            written = _write(conn, rows, "NL", "EUR", NL_SOURCE_KEY)
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", read, written, rejected, error=str(exc))
        raise

    finish_run(run_id, "complete", read, written, rejected)
    log.info("Netherlands CBS: %s area-year average prices", f"{written:,}")
    return {"written": written, "read": read}


def _dk_request(values: str) -> dict:
    body = json.dumps({
        "table": "EJEN77",
        "format": "JSONSTAT",
        "variables": [
            # Only the country and the five regions. Requesting every area
            # exceeds the API's record cap, and the "Landsdel" rows sit inside
            # the regions, so including them would double-count.
            {"code": "OMRÅDE", "values": DK_AREA_CODES},
            {"code": "EJENDOMSKATE", "values": ["0111"]},
            {"code": "BNØGLE", "values": [values]},
            {"code": "OVERDRAG", "values": ["1"]},
            {"code": "Tid", "values": ["*"]},
        ],
    }).encode()
    req = urllib.request.Request(
        DK_URL, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "RE-Maps/0.1 (property price map)"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _dk_series(payload: dict) -> dict[tuple[str, str], float]:
    """(region label, quarter) -> value, from a JSON-stat cube."""
    dataset = payload.get("dataset", payload)
    dims = dataset["dimension"]
    order = dims.get("id") or dataset.get("id")
    sizes = dims.get("size") or dataset.get("size")
    values = dataset["value"]

    regions = dims["OMRÅDE"]["category"]
    times = dims["Tid"]["category"]
    region_by_pos = {v: k for k, v in regions["index"].items()}
    time_by_pos = {v: k for k, v in times["index"].items()}
    r_axis, t_axis = order.index("OMRÅDE"), order.index("Tid")

    out: dict[tuple[str, str], float] = {}
    total = 1
    for size in sizes:
        total *= size
    for flat in range(total):
        # Decode the flat index into per-dimension positions (row-major).
        rest, coords = flat, [0] * len(sizes)
        for axis in range(len(sizes) - 1, -1, -1):
            coords[axis] = rest % sizes[axis]
            rest //= sizes[axis]
        value = values[str(flat)] if isinstance(values, dict) else values[flat]
        if value is None:
            continue
        label = regions["label"][region_by_pos[coords[r_axis]]]
        out[(label, time_by_pos[coords[t_axis]])] = float(value)
    return out


def ingest_denmark() -> dict[str, int]:
    register_sources()
    run_id = start_run(DK_SOURCE_KEY, "statbank-EJEN77")
    written = 0
    try:
        prices = _dk_series(_dk_request("3"))   # avg price, 1000 DKK
        counts = _dk_series(_dk_request("2"))   # sales, the weight

        # Sales-weighted annual mean (see module docstring).
        totals: dict[tuple[str, int], list[float]] = {}
        for (label, quarter), price in prices.items():
            year = int(quarter[:4])
            weight = counts.get((label, quarter))
            if not weight:
                continue
            bucket = totals.setdefault((label, year), [0.0, 0.0])
            bucket[0] += price * 1000 * weight   # to kroner
            bucket[1] += weight

        rows: dict[tuple[str, str, int], tuple[float, int | None, str]] = {}
        for (label, year), (value_sum, weight) in totals.items():
            if weight <= 0:
                continue
            if label.strip() == "Hele landet":
                level, name = "country", "Denmark"
            elif label.startswith("Region "):
                level = "county"
                raw = label.replace("Region ", "").strip()
                name = DK_REGION_ALIASES.get(raw, raw)
            else:
                continue  # "Landsdel" rows sit inside the regions
            rows[(level, name, year)] = (
                value_sum / weight, int(weight), f"DK-{name[:12].upper()}"
            )

        with sync_conn() as conn:
            written = _write(conn, rows, "DK", "DKK", DK_SOURCE_KEY)
            conn.commit()
    except (urllib.error.HTTPError, KeyError, ValueError) as exc:
        finish_run(run_id, "failed", 0, written, 0, error=str(exc))
        raise

    finish_run(run_id, "complete", len(rows), written, 0)
    log.info("Denmark StatBank: %s area-year average prices", f"{written:,}")
    return {"written": written}


# --- Sweden ---------------------------------------------------------------
#
# SCB BO0501B/FastprisPSRegAr: sold one- and two-dwelling buildings for
# permanent living, by county, annual. Covers houses rather than flats --
# tenant-owned flats ("bostadsrätter") are a separate legal form and a separate
# table -- so this understates prices in the cities, where flats dominate.
SE_URL = (
    "https://api.scb.se/OV0104/v1/doris/en/ssd/BO/BO0501/BO0501B/"
    "FastprisPSRegAr"
)
SE_PRICE_CODE = "BO0501O4"   # purchase price, average, 1 000 SEK
SE_COUNT_CODE = "BO0501O3"   # number of sales
# SCB labels counties "<Name> county"; Natural Earth drops the suffix and
# transliterates one name.
SE_REGION_ALIASES = {"Örebro": "Orebro"}
# County codes only: the "Greater <city>" and national rows would double-count.
SE_COUNTY_CODES = [
    "01", "03", "04", "05", "06", "07", "08", "09", "10", "12", "13", "14",
    "17", "18", "19", "20", "21", "22", "23", "24", "25",
]


def ingest_sweden() -> dict[str, int]:
    register_sources()
    run_id = start_run(SE_SOURCE_KEY, "scb-FastprisPSRegAr")
    written = 0
    rows: dict[tuple[str, str, int], tuple[float, int | None, str]] = {}
    try:
        body = json.dumps({
            "query": [
                {"code": "Region", "selection": {
                    "filter": "item", "values": ["00", *SE_COUNTY_CODES]}},
                {"code": "ContentsCode", "selection": {
                    "filter": "item",
                    "values": [SE_PRICE_CODE, SE_COUNT_CODE]}},
            ],
            "response": {"format": "json-stat2"},
        }).encode()
        req = urllib.request.Request(
            SE_URL, data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "RE-Maps/0.1 (property price map)"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read())

        dims = payload["dimension"]
        order = payload["id"]
        sizes = payload["size"]
        values = payload["value"]
        region_pos = {v: k for k, v in dims["Region"]["category"]["index"].items()}
        region_label = dims["Region"]["category"]["label"]
        time_pos = {v: k for k, v in dims["Tid"]["category"]["index"].items()}
        content_pos = {
            v: k for k, v in dims["ContentsCode"]["category"]["index"].items()
        }
        r_axis = order.index("Region")
        t_axis = order.index("Tid")
        c_axis = order.index("ContentsCode")

        prices: dict[tuple[str, int], float] = {}
        counts: dict[tuple[str, int], float] = {}
        total = 1
        for size in sizes:
            total *= size
        for flat in range(total):
            rest, coords = flat, [0] * len(sizes)
            for axis in range(len(sizes) - 1, -1, -1):
                coords[axis] = rest % sizes[axis]
                rest //= sizes[axis]
            value = values[str(flat)] if isinstance(values, dict) else values[flat]
            if value is None:
                continue
            code = region_pos[coords[r_axis]]
            year = int(time_pos[coords[t_axis]])
            target = (
                prices if content_pos[coords[c_axis]] == SE_PRICE_CODE
                else counts
            )
            target[(code, year)] = float(value)

        for (code, year), price in prices.items():
            if code == "00":
                level, name = "country", "Sweden"
            else:
                level = "county"
                raw = region_label[code].replace(" county", "").strip()
                name = SE_REGION_ALIASES.get(raw, raw)
            count = counts.get((code, year))
            rows[(level, name, year)] = (
                price * 1000,                       # 1 000 SEK -> SEK
                int(count) if count else None,
                f"SE-{code}",
            )

        with sync_conn() as conn:
            written = _write(conn, rows, "SE", "SEK", SE_SOURCE_KEY)
            conn.commit()
    except Exception as exc:
        finish_run(run_id, "failed", len(rows), written, 0, error=str(exc))
        raise

    finish_run(run_id, "complete", len(rows), written, 0)
    log.info("Sweden SCB: %s area-year average prices", f"{written:,}")
    return {"written": written}


def ingest() -> dict:
    return {
        "netherlands": ingest_netherlands(),
        "denmark": ingest_denmark(),
        "sweden": ingest_sweden(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(ingest())
