"""Load sub-national administrative polygons (Natural Earth admin-1).

An area statistic needs a shape. With only a stored point, a large region's
figure is invisible unless the viewport happens to contain that exact
coordinate — which is why a view of Los Angeles fell back to the US national
index instead of showing California's. Polygons let the map clip a region into
the visible area, exactly as it already does for countries.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.db import sync_conn

from .sources import mark_ingested, register_sources

log = logging.getLogger(__name__)
SOURCE_KEY = "natural_earth_admin1"

UPSERT = """
INSERT INTO regions (
    country_iso2, iso_3166_2, postal_code, name, name_local, region_type,
    geom, source_id
)
SELECT %(iso2)s, %(iso_3166_2)s, %(postal)s, %(name)s, %(name_local)s,
       %(region_type)s,
       ST_Multi(ST_CollectionExtract(
           ST_MakeValid(ST_GeomFromGeoJSON(%(geojson)s)), 3
       ))::geometry(MultiPolygon, 4326),
       %(source_id)s
ON CONFLICT (country_iso2, coalesce(iso_3166_2, ''), name) DO UPDATE SET
    postal_code = EXCLUDED.postal_code,
    name_local  = EXCLUDED.name_local,
    region_type = EXCLUDED.region_type,
    geom        = EXCLUDED.geom;
"""


def _clean(value: object) -> str | None:
    text = (str(value) if value is not None else "").strip()
    # Natural Earth uses -99 and -1 as null sentinels.
    if not text or text in {"-99", "-1", "None"}:
        return None
    return text


def ingest(path: Path | None = None) -> int:
    settings = get_settings()
    source_ids = register_sources()
    path = path or (
        settings.raw_dir / "geo" / "ne_10m_admin_1_states_provinces.geojson"
    )
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `make data-download-geo` first."
        )

    data = json.loads(path.read_text())
    written = skipped = 0

    with sync_conn() as conn:
        for feature in data["features"]:
            props = feature["properties"]
            iso2 = _clean(props.get("iso_a2"))
            name = _clean(props.get("name")) or _clean(props.get("woe_name"))
            geometry = feature.get("geometry")
            if not iso2 or len(iso2) != 2 or not name or not geometry:
                skipped += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    UPSERT,
                    {
                        "iso2": iso2,
                        "iso_3166_2": _clean(props.get("iso_3166_2")),
                        "postal": _clean(props.get("postal")),
                        "name": name,
                        "name_local": _clean(props.get("name_local")),
                        "region_type": _clean(props.get("type_en")),
                        "geojson": json.dumps(geometry),
                        "source_id": source_ids.get(SOURCE_KEY),
                    },
                )
            written += 1
            if written % 1000 == 0:
                conn.commit()
                log.info("  loaded %s regions", f"{written:,}")
        with conn.cursor() as cur:
            cur.execute("ANALYZE regions")
        conn.commit()

    mark_ingested(SOURCE_KEY, written)
    log.info("regions: %s loaded, %s skipped", f"{written:,}", skipped)
    return written


# Join index-based area statistics to their shape. US states match on the
# postal code, which is exactly FHFA's place_id (area_code 'FHFA-NY' -> 'NY').
LINK_SQL = """
UPDATE area_stats a
SET region_id = r.id
FROM regions r
WHERE a.region_id IS NULL
  AND a.area_level = 'state'
  AND r.country_iso2 = a.country_iso2
  AND r.postal_code = split_part(a.area_code, '-', 2);
"""


def link_area_stats() -> int:
    with sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(LINK_SQL)
            linked = cur.rowcount
            cur.execute("ANALYZE area_stats")
        conn.commit()
    log.info("linked %s area statistics to a region shape", f"{linked:,}")
    return linked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ingest()
    link_area_stats()
