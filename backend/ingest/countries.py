"""Load country polygons so coordinates can be resolved to a jurisdiction.

Natural Earth 10m admin-0 is public domain and accurate enough to decide
"which country is this point in", which is all the jurisdiction resolver needs.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.core.currency import currency_for_country
from app.db import sync_conn

from .sources import mark_ingested, register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "natural_earth_admin0"


def _iso2(props: dict) -> str | None:
    """Prefer the '_EH' variants, which fill in codes Natural Earth marks -99."""
    for key in ("ISO_A2_EH", "ISO_A2", "ADM0_ISO"):
        val = (props.get(key) or "").strip().upper()
        if len(val) == 2 and val != "-9":
            return val
    return None


def _iso3(props: dict) -> str | None:
    for key in ("ISO_A3_EH", "ISO_A3", "ADM0_A3"):
        val = (props.get(key) or "").strip().upper()
        if len(val) == 3 and val != "-99":
            return val
    return None


def ingest(path: Path | None = None) -> int:
    settings = get_settings()
    source_ids = register_sources()
    path = path or settings.raw_dir / "geo" / "ne_10m_admin_0_countries.geojson"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `make data-download` first."
        )

    data = json.loads(path.read_text())

    # A country can appear as several features (mainland + dependencies), so
    # collect geometries per ISO code and union them into one MultiPolygon.
    by_iso: dict[str, dict] = {}
    for feat in data["features"]:
        props = feat["properties"]
        iso2 = _iso2(props)
        if not iso2:
            continue
        entry = by_iso.setdefault(
            iso2,
            {
                "iso2": iso2,
                "iso3": _iso3(props),
                "name": props.get("NAME_LONG") or props.get("NAME") or iso2,
                "geoms": [],
            },
        )
        entry["geoms"].append(json.dumps(feat["geometry"]))

    written = 0
    with sync_conn() as conn:
        with conn.cursor() as cur:
            for iso2, entry in by_iso.items():
                # ST_Collect + ST_Multi guarantees the declared MultiPolygon
                # type; ST_MakeValid guards against self-intersections in the
                # source data, which would otherwise break ST_Contains.
                cur.execute(
                    """
                    INSERT INTO countries (iso2, iso3, name, currency_code, geom, source_id)
                    SELECT %s, %s, %s, %s,
                           ST_Multi(ST_CollectionExtract(
                               ST_MakeValid(ST_Collect(g.geom)), 3))::geometry(MultiPolygon,4326),
                           %s
                    FROM (
                        SELECT ST_SetSRID(ST_GeomFromGeoJSON(j), 4326) AS geom
                        FROM unnest(%s::text[]) AS j
                    ) g
                    ON CONFLICT (iso2) DO UPDATE SET
                        iso3 = EXCLUDED.iso3, name = EXCLUDED.name,
                        currency_code = EXCLUDED.currency_code,
                        geom = EXCLUDED.geom, source_id = EXCLUDED.source_id
                    """,
                    (
                        iso2,
                        entry["iso3"],
                        entry["name"],
                        currency_for_country(iso2),
                        source_ids[SOURCE_KEY],
                        entry["geoms"],
                    ),
                )
                written += 1
        conn.commit()

    mark_ingested(SOURCE_KEY, written)
    log.info("countries: %s rows", written)
    return written


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"{ingest()} countries loaded")
