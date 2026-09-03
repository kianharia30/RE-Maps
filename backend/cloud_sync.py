"""Copy RE-Maps data to a hosted Postgres database.

WHY THIS EXISTS
---------------
The full database is ~7.7 GB, and every reputable free Postgres tier is around
0.5-1 GB. The two are not reconcilable, so this tool is explicit about it rather
than failing halfway through a 3 GB table.

The data splits cleanly in two:

  * the MAP layer — boundaries, official indices, and precomputed area
    aggregates. About 185 MB of data. This is what draws the map for all 31
    jurisdictions, at every zoom from world down to UK postcode sector, and it
    fits a free tier comfortably.

  * the SALES layer — 6M individual transactions, 5.5M dwellings, and 2.6M
    postcode centroids. About 7.3 GB. This is what powers individual property
    markers, postcode search and valuation. It does NOT fit a free tier, and
    no amount of index trimming changes that.

So `--profile map` (the default) deploys a working world map to a free tier.
`--profile full` deploys everything and needs a paid tier.

HONESTY REQUIREMENT
-------------------
A `map` deployment holds no individual sales. If it copied the coverage
registry verbatim, the deployed API would advertise `transaction_level_data:
true` for England & Wales and France and then return nothing — claiming data it
does not have. So the map profile rewrites those rows to say what is actually
deployed. See `_correct_coverage_for_profile`.

USAGE
-----
Set the target in the environment; this tool never asks for it on the command
line and never prints it, because a connection string contains a password.

    export CLOUD_DATABASE_URL='postgresql://...'   # from your provider
    .venv/bin/python cloud_sync.py --profile map --dry-run
    .venv/bin/python cloud_sync.py --profile map
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from app.config import get_settings

log = logging.getLogger("cloud_sync")

# Tables the map needs, in dependency order (parents before children).
MAP_TABLES: tuple[str, ...] = (
    "data_sources",
    "countries",
    "regions",
    "market_indices",
    "area_index_links",
    "area_stats",
    "provider_coverage",
    "forecasts",
    "geocode_cache",
    "ingestion_runs",
    "model_evaluations",
)

# Everything the map profile deliberately leaves behind.
SALES_TABLES: tuple[str, ...] = ("properties", "transactions", "postcodes")

PROFILES: dict[str, tuple[str, ...]] = {
    "lean": MAP_TABLES,
    "map": MAP_TABLES,
    # Sales tables slot in after the boundaries they reference and before the
    # aggregates derived from them.
    "full": (
        *MAP_TABLES[:3], "postcodes", "properties", "transactions",
        *MAP_TABLES[3:],
    ),
}

# Row filters applied per profile. `map` copies every row; `lean` trims the two
# tables that dominate the total, to leave real headroom under a 500 MB cap.
#
# What lean gives up, precisely:
#   * UK aggregates below postcode-district level, so the map stops resolving
#     finer than a district (roughly z11) in the UK rather than reaching
#     postcode sectors. Every other jurisdiction is unaffected, because none of
#     them publish anything finer than we keep.
#   * Index history before 1995, which is earlier than any transaction we hold
#     and therefore earlier than the timeline can be set to. Forecasts lose
#     long-run history, which measurably weakens them — hence `map` being the
#     default when the tier allows it.
FILTERS: dict[str, dict[str, str]] = {
    "lean": {
        "area_stats": "area_level <> 'sector'",
        "market_indices": "period >= DATE '1995-01-01'",
    },
}


def _redact(url: str) -> str:
    """A connection string contains a password; never log it."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        db = (parsed.path or "/?").lstrip("/") or "?"
        return f"{host}/{db}"
    except ValueError:
        return "<unparseable target>"


def _target_url() -> str:
    url = os.environ.get("CLOUD_DATABASE_URL", "").strip()
    if not url:
        sys.exit(
            "CLOUD_DATABASE_URL is not set.\n\n"
            "Create a free Postgres database with your provider, then:\n"
            "  export CLOUD_DATABASE_URL='postgresql://...'\n\n"
            "This tool never prints the value."
        )
    return url


def _filtered_rows(
    conn: psycopg.Connection, table: str, where: str | None
) -> int | None:
    """Exact row count under a filter (an estimate would be misleading here)."""
    if not where:
        return None
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE {where}")
        row = cur.fetchone()
    return row[0] if row else 0


def _size_report(
    conn: psycopg.Connection,
    tables: tuple[str, ...],
    filters: dict[str, str] | None = None,
) -> int:
    total = 0
    log.info("%-20s %14s %12s", "table", "rows", "size")
    for table in tables:
        with conn.cursor() as cur:
            # to_regclass returns NULL rather than raising for a missing table,
            # so a partially loaded database still produces a full report.
            cur.execute(
                """
                SELECT coalesce(pg_total_relation_size(c.oid), 0) AS size,
                       coalesce(c.reltuples::bigint, 0) AS rows
                FROM (SELECT to_regclass(%s) AS oid) t
                LEFT JOIN pg_class c ON c.oid = t.oid
                """,
                (table,),
            )
            row = cur.fetchone()
        size, rows = (row[0], row[1]) if row else (0, 0)
        where = (filters or {}).get(table)
        kept = _filtered_rows(conn, table, where) if size else None
        if kept is not None and rows > 0:
            # Scale the on-disk size by the fraction of rows kept. Approximate,
            # but honest about being an estimate, and close enough to judge a
            # tier against.
            size = int(size * min(1.0, kept / max(rows, 1)))
            rows = kept
        total += size
        label = table if size else f"{table} (absent)"
        suffix = "  (filtered)" if where and kept is not None else ""
        log.info("%-20s %14s %12s%s", label, f"{rows:,}", _human(size), suffix)
    log.info("%-20s %14s %12s", "TOTAL", "", _human(total))
    return total


def _human(num: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:,.0f} {unit}"
        num /= 1024
    return f"{num:,.0f} GB"


def _apply_schema(target_url: str) -> None:
    """Create the schema on the target by replaying the migrations."""
    log.info("applying schema to %s", _redact(target_url))
    env = dict(os.environ, DATABASE_URL=target_url)
    result = subprocess.run(
        [sys.executable, "migrate.py"],
        cwd=Path(__file__).parent,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(
            "schema creation failed on the target:\n"
            + (result.stderr or result.stdout)[-3000:]
        )
    for line in (result.stdout or "").strip().splitlines()[-3:]:
        log.info("  %s", line)


def _copy_table(
    source: psycopg.Connection,
    target: psycopg.Connection,
    table: str,
    where: str | None = None,
) -> int:
    """Stream one table across with COPY.

    Streamed rather than dumped to a file: the machine this was written on had
    under 2 GB of disk free, and a temp dump of `transactions` alone is 1.6 GB.
    """
    with target.cursor() as cur:
        cur.execute(f"TRUNCATE {table} CASCADE")

    # A filtered copy needs a subquery; an unfiltered one names the table so
    # Postgres can use a faster path.
    source_sql = (
        f"COPY (SELECT * FROM {table} WHERE {where}) TO STDOUT (FORMAT BINARY)"
        if where
        else f"COPY {table} TO STDOUT (FORMAT BINARY)"
    )
    copied = 0
    with source.cursor().copy(source_sql) as src_copy, target.cursor().copy(
        f"COPY {table} FROM STDIN (FORMAT BINARY)"
    ) as dst_copy:
        for block in src_copy:
            dst_copy.write(block)
            copied += len(block)

    with target.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        row = cur.fetchone()
    return row[0] if row else 0


# Coverage claims must match what is actually deployed.
CORRECT_COVERAGE = """
UPDATE provider_coverage
SET transaction_level_data = false,
    property_characteristics = false,
    max_precision = 'CITY_REGIONAL',
    coordinate_precision = 'REGION',
    notes = coalesce(notes || ' ', '') ||
            'This deployment holds precomputed area statistics only: the '
            'individual sale records are not present, so no individual '
            'property figures, comparables or valuations are available here.'
WHERE transaction_level_data = true;
"""


def _correct_coverage_for_profile(target: psycopg.Connection) -> int:
    """Stop a map-only deployment advertising sales data it does not hold."""
    with target.cursor() as cur:
        cur.execute(CORRECT_COVERAGE)
        changed = cur.rowcount
    log.info(
        "corrected %s coverage rows: this deployment does not claim "
        "individual sales data", changed,
    )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=sorted(PROFILES), default="map",
        help="lean = the map with UK detail trimmed to postcode district and "
             "index history from 1995 (comfortably under a 500 MB free tier). "
             "map = the same layers untrimmed, ~464 MB, which fits 500 MB with "
             "no headroom. full = everything including 6M individual sales "
             "(~7.7 GB, needs a paid tier).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would be copied and how large it is, then stop.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    tables = PROFILES[args.profile]
    filters = FILTERS.get(args.profile, {})
    source_url = get_settings().database_url

    with psycopg.connect(source_url) as source:
        log.info("profile '%s' would copy:", args.profile)
        total = _size_report(source, tables, filters)
        if args.profile != "full":
            log.info("")
            log.info("deliberately excluded (%s):", ", ".join(SALES_TABLES))
            excluded = _size_report(source, SALES_TABLES)
            log.info("")
            log.info(
                "Excluding these keeps the deployment %.0fx smaller. The cost "
                "is real: no individual property markers, no postcode search "
                "and no valuations, because those need the sale records "
                "themselves.",
                (total + excluded) / max(total, 1),
            )
            log.info(
                "  area figures survive down to %s.",
                "postcode district in the UK (about zoom 11); finer UK "
                "aggregates are trimmed by this profile"
                if args.profile == "lean"
                else "postcode sector in the UK (about zoom 14)",
            )
        if args.dry_run:
            log.info("")
            log.info("dry run: nothing was written.")
            return 0

        target_url = _target_url()
        _apply_schema(target_url)

        with psycopg.connect(target_url) as target:
            for table in tables:
                rows = _copy_table(source, target, table, filters.get(table))
                log.info("  copied %-20s %14s rows", table, f"{rows:,}")
            if args.profile != "full":
                _correct_coverage_for_profile(target)
            with target.cursor() as cur:
                cur.execute("ANALYZE")
            target.commit()

            with target.cursor() as cur:
                cur.execute("SELECT pg_database_size(current_database())")
                row = cur.fetchone()
            log.info("")
            log.info("target database size: %s", _human(row[0] if row else 0))

    log.info("done. Point the API at the target with DATABASE_URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
