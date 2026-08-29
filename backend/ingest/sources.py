"""The authoritative registry of external data sources (§44).

Every field here was verified against the live source. Nothing may be ingested
or displayed unless it has an entry in this file, because the API refuses to
emit a price without a resolvable `SourceRef`.
"""
from __future__ import annotations

from datetime import date

from app.db import sync_conn

# NOTE ON ATTRIBUTION
# The licence for each dataset requires the attribution string below to be
# displayed wherever the data is used. The frontend renders these in the
# map attribution bar and in every property detail panel.

SOURCES: list[dict] = [
    {
        "key": "uk_land_registry_ppd",
        "name": "HM Land Registry Price Paid Data",
        "owner": "HM Land Registry",
        "url": "https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads",
        "documentation_url": "https://www.gov.uk/guidance/about-the-price-paid-data",
        "licence": "Open Government Licence v3.0",
        "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "attribution": (
            "Contains HM Land Registry data © Crown copyright and database "
            "right 2026. This data is licensed under the Open Government "
            "Licence v3.0."
        ),
        "allowed_use": (
            "Free re-use including commercial, with attribution. Excludes "
            "sales not lodged with HM Land Registry, sales not for value, "
            "right-to-buy discounted sales, transfers under court order, "
            "compulsory purchases and leases of 7 years or less. The two most "
            "recent months are incomplete because registration lags "
            "completion by roughly 2 weeks to 2 months."
        ),
        "update_frequency": "Monthly",
        "geographic_coverage": "England and Wales",
        "historical_coverage": "1995-01-01 to present",
        "source_published_at": date(2026, 8, 28),
    },
    {
        "key": "uk_hpi",
        "name": "UK House Price Index",
        "owner": "HM Land Registry / Office for National Statistics",
        "url": "https://www.gov.uk/government/statistical-data-sets/uk-house-price-index-data-downloads",
        "documentation_url": "https://www.gov.uk/government/publications/about-the-uk-house-price-index",
        "licence": "Open Government Licence v3.0",
        "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "attribution": (
            "Contains HM Land Registry data © Crown copyright and database "
            "right 2026, and Office for National Statistics data © Crown "
            "copyright. Licensed under the Open Government Licence v3.0."
        ),
        "allowed_use": (
            "Free re-use including commercial, with attribution. A "
            "mix-adjusted hedonic index; the most recent months are revised "
            "in subsequent releases."
        ),
        "update_frequency": "Monthly",
        "geographic_coverage": "United Kingdom, by country, region and local authority",
        "historical_coverage": "1968-04-01 to present (local authority series from 1995)",
        "source_published_at": date(2026, 6, 1),
    },
    {
        "key": "uk_open_postcode_geo",
        "name": "Open Postcode Geo",
        "owner": "GetTheData (derived from ONS Postcode Directory / OS Code-Point Open)",
        "url": "https://www.getthedata.com/open-postcode-geo",
        "documentation_url": "https://www.getthedata.com/open-postcode-geo",
        "licence": "Open Government Licence v3.0 + Ordnance Survey OpenData Licence",
        "licence_url": "https://www.ons.gov.uk/methodology/geography/licences",
        "attribution": (
            "Contains OS data © Crown copyright and database right 2026. "
            "Contains Royal Mail data © Royal Mail copyright and database "
            "right 2026. Contains National Statistics data © Crown copyright "
            "and database right 2026."
        ),
        "allowed_use": (
            "Free re-use with attribution. Northern Ireland postcodes (BT) "
            "require a separate licence from Land and Property Services for "
            "commercial use; RE-Maps holds no transaction data for Northern "
            "Ireland so BT centroids are not used for any displayed price. "
            "A postcode centroid is the centre of a postcode unit, NOT the "
            "position of an individual building."
        ),
        "update_frequency": "Periodic (dataset vintage October 2023)",
        "geographic_coverage": "United Kingdom",
        "historical_coverage": "Current postcodes plus terminated postcodes",
        "source_published_at": date(2023, 10, 17),
    },
    {
        "key": "fr_dvf",
        "name": "Demandes de valeurs foncières géolocalisées (geo-DVF)",
        "owner": "Direction générale des Finances publiques / Etalab",
        "url": "https://files.data.gouv.fr/geo-dvf/latest/csv/",
        "documentation_url": "https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres-geolocalisees/",
        "licence": "Licence Ouverte / Open Licence 2.0 (Etalab)",
        "licence_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        "attribution": (
            "Contient des données de la Direction générale des Finances "
            "publiques (DVF), géolocalisées par Etalab, sous Licence Ouverte 2.0."
        ),
        "allowed_use": (
            "Free re-use including commercial, with attribution. Excludes "
            "Alsace, Moselle and Mayotte, which have separate land registry "
            "systems. A 'mutation' may cover several lots, so a single "
            "declared value can span more than one dwelling; multi-lot "
            "mutations are excluded from per-dwelling analysis."
        ),
        "update_frequency": "Twice yearly",
        "geographic_coverage": "France (excluding Alsace, Moselle, Mayotte)",
        "historical_coverage": "2021 to present in the current geo-DVF release",
        "source_published_at": date(2025, 10, 1),
    },
    {
        "key": "natural_earth_admin0",
        "name": "Natural Earth Admin 0 - Countries (10m)",
        "owner": "Natural Earth",
        "url": "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/",
        "documentation_url": "https://github.com/nvkelso/natural-earth-vector",
        "licence": "Public domain",
        "licence_url": "https://www.naturalearthdata.com/about/terms-of-use/",
        "attribution": "Country boundaries from Natural Earth (public domain).",
        "allowed_use": "Unrestricted. Used only to resolve coordinates to a country.",
        "update_frequency": "Irregular",
        "geographic_coverage": "Global",
        "historical_coverage": "Current boundaries",
        "source_published_at": None,
    },
    {
        "key": "osm_nominatim",
        "name": "Nominatim geocoding (OpenStreetMap)",
        "owner": "OpenStreetMap contributors / OSM Foundation",
        "url": "https://nominatim.openstreetmap.org",
        "documentation_url": "https://operations.osmfoundation.org/policies/nominatim/",
        "licence": "Open Database Licence (ODbL) 1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
        "attribution": "Geocoding © OpenStreetMap contributors, ODbL 1.0.",
        "allowed_use": (
            "Free for low-volume use. The public endpoint's policy forbids "
            "bulk/heavy use and requires an identifying User-Agent; RE-Maps "
            "throttles to one request per second and caches every result."
        ),
        "update_frequency": "Continuous",
        "geographic_coverage": "Global",
        "historical_coverage": "n/a",
        "source_published_at": None,
    },
    {
        "key": "openfreemap",
        "name": "OpenFreeMap vector tiles",
        "owner": "OpenFreeMap / OpenStreetMap contributors",
        "url": "https://openfreemap.org",
        "documentation_url": "https://openfreemap.org",
        "licence": "Open Database Licence (ODbL) 1.0",
        "licence_url": "https://www.openstreetmap.org/copyright",
        "attribution": "Base map © OpenFreeMap, © OpenMapTiles, Data © OpenStreetMap contributors.",
        "allowed_use": "Free basemap tiles, no API key, no usage limits stated.",
        "update_frequency": "Continuous",
        "geographic_coverage": "Global",
        "historical_coverage": "n/a",
        "source_published_at": None,
    },
    {
        "key": "uk_epc",
        "name": "Energy Performance of Buildings Certificates (England & Wales)",
        "owner": "Ministry of Housing, Communities and Local Government",
        "url": "https://epc.opendatacommunities.org/",
        "documentation_url": "https://epc.opendatacommunities.org/docs/api",
        "licence": "Open Government Licence v3.0",
        "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "attribution": (
            "Contains Energy Performance of Buildings data © Crown copyright "
            "and database right 2026, licensed under the Open Government "
            "Licence v3.0."
        ),
        "allowed_use": (
            "Free re-use with attribution, subject to accepting the service's "
            "terms. Requires a free registered account and API key, so this "
            "enrichment is disabled unless EPC_API_EMAIL and EPC_API_KEY are "
            "configured."
        ),
        "update_frequency": "Monthly",
        "geographic_coverage": "England and Wales",
        "historical_coverage": "2008 to present",
        "source_published_at": None,
    },
]


UPSERT = """
INSERT INTO data_sources (
    key, name, owner, url, documentation_url, licence, licence_url,
    attribution, allowed_use, update_frequency, geographic_coverage,
    historical_coverage, source_published_at
) VALUES (
    %(key)s, %(name)s, %(owner)s, %(url)s, %(documentation_url)s, %(licence)s,
    %(licence_url)s, %(attribution)s, %(allowed_use)s, %(update_frequency)s,
    %(geographic_coverage)s, %(historical_coverage)s, %(source_published_at)s
)
ON CONFLICT (key) DO UPDATE SET
    name = EXCLUDED.name, owner = EXCLUDED.owner, url = EXCLUDED.url,
    documentation_url = EXCLUDED.documentation_url, licence = EXCLUDED.licence,
    licence_url = EXCLUDED.licence_url, attribution = EXCLUDED.attribution,
    allowed_use = EXCLUDED.allowed_use,
    update_frequency = EXCLUDED.update_frequency,
    geographic_coverage = EXCLUDED.geographic_coverage,
    historical_coverage = EXCLUDED.historical_coverage,
    source_published_at = EXCLUDED.source_published_at
RETURNING id
"""


def register_sources() -> dict[str, int]:
    """Idempotently upsert every source; returns key -> id."""
    ids: dict[str, int] = {}
    with sync_conn() as conn:
        with conn.cursor() as cur:
            for src in SOURCES:
                cur.execute(UPSERT, src)
                ids[src["key"]] = cur.fetchone()["id"]
        conn.commit()
    return ids


def mark_ingested(source_key: str, record_count: int | None = None) -> None:
    with sync_conn() as conn:
        with conn.cursor() as cur:
            if record_count is None:
                cur.execute(
                    "UPDATE data_sources SET last_ingested_at = now() WHERE key = %s",
                    (source_key,),
                )
            else:
                cur.execute(
                    "UPDATE data_sources SET last_ingested_at = now(), "
                    "record_count = %s WHERE key = %s",
                    (record_count, source_key),
                )
        conn.commit()


if __name__ == "__main__":
    print(register_sources())
