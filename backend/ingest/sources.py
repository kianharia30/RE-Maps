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
        "key": "eurostat_hpi",
        "name": "Eurostat House Price Index (prc_hpi_q)",
        "owner": "Eurostat, European Commission",
        "url": "https://ec.europa.eu/eurostat/databrowser/view/prc_hpi_q/default/table",
        "documentation_url": "https://wikis.ec.europa.eu/display/EUROSTATHELP/API+-+Detailed+guidelines+-+API+Statistics",
        "licence": "Eurostat open data policy (re-use permitted with attribution)",
        "licence_url": "https://ec.europa.eu/eurostat/about-us/policies/copyright",
        "attribution": "Source: Eurostat, House price index (prc_hpi_q).",
        "allowed_use": (
            "Free re-use including commercial, with attribution. This is an "
            "INDEX (2015 = 100), not a price level: it measures change over "
            "time and cannot be converted into a monetary value without a "
            "base-year price, which Eurostat does not publish here. Deflated "
            "and nominal variants exist; RE-Maps ingests the nominal index for "
            "total purchases."
        ),
        "update_frequency": "Quarterly",
        "geographic_coverage": "31 European countries, national level only",
        "historical_coverage": "2005 onwards for most countries",
        "source_published_at": None,
    },
    {
        "key": "us_fhfa_hpi",
        "name": "FHFA House Price Index",
        "owner": "Federal Housing Finance Agency (USA)",
        "url": "https://www.fhfa.gov/data/hpi",
        "documentation_url": "https://www.fhfa.gov/data/hpi/datasets",
        "licence": "US Government work — public domain (17 U.S.C. §105)",
        "licence_url": "https://www.fhfa.gov/about/policies",
        "attribution": "Source: Federal Housing Finance Agency, House Price Index.",
        "allowed_use": (
            "Public domain. This is a repeat-sales INDEX, not a price level, "
            "so it supports growth rates but no monetary value. It also covers "
            "only homes with conforming, conventional mortgages, so cash and "
            "jumbo-financed sales are out of scope."
        ),
        "update_frequency": "Monthly",
        "geographic_coverage": "United States, by state and metropolitan area",
        "historical_coverage": "1975 onwards (purchase-only series from 1991)",
        "source_published_at": None,
    },
    {
        "key": "ie_ppr",
        "name": "Residential Property Price Register (Ireland)",
        "owner": "Property Services Regulatory Authority (Ireland)",
        "url": "https://propertypriceregister.ie/",
        "documentation_url": "https://propertypriceregister.ie/website/npsra/pprweb.nsf/page/ppr-home-en",
        "licence": "Creative Commons Attribution 4.0 (PSRA open data)",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": (
            "Contains data from the Residential Property Price Register, "
            "Property Services Regulatory Authority, Ireland."
        ),
        "allowed_use": (
            "Free re-use with attribution. Declared prices as filed for stamp "
            "duty; the register flags sales that were not at full market price "
            "and those quoted VAT-exclusive, both of which RE-Maps excludes "
            "from valuation evidence. Addresses are free text and Eircodes are "
            "sparsely populated, and Eircode-to-coordinate data is licensed, "
            "so Irish figures are published at county level only."
        ),
        "update_frequency": "Continuously, published as a full file",
        "geographic_coverage": "Republic of Ireland, by county",
        "historical_coverage": "2010 onwards",
        "source_published_at": None,
    },
    {
        "key": "sg_hdb_resale",
        "name": "HDB Resale Flat Prices (Singapore)",
        "owner": "Housing & Development Board / data.gov.sg",
        "url": "https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view",
        "documentation_url": "https://guide.data.gov.sg/developer-guide/api-overview",
        "licence": "Singapore Open Data Licence v1.0",
        "licence_url": "https://data.gov.sg/open-data-licence",
        "attribution": (
            "Contains information from the Housing & Development Board "
            "resale flat prices dataset, accessed via data.gov.sg, licensed "
            "under the Singapore Open Data Licence version 1.0."
        ),
        "allowed_use": (
            "Free re-use with attribution. Covers HDB resale flats only — "
            "roughly three quarters of Singapore's housing stock, but NOT "
            "private condominiums or landed property, so figures are not "
            "representative of the whole market. Published per town, which is "
            "the granularity RE-Maps uses."
        ),
        "update_frequency": "Monthly",
        "geographic_coverage": "Singapore, by HDB town",
        "historical_coverage": "2017 onwards in the current resource",
        "source_published_at": None,
    },
    {
        "key": "nl_cbs_prices",
        "name": "Existing own homes; average purchase prices, region (83625NED)",
        "owner": "Centraal Bureau voor de Statistiek (Netherlands)",
        "url": "https://opendata.cbs.nl/statline/#/CBS/nl/dataset/83625NED/table",
        "documentation_url": "https://www.cbs.nl/en-gb/our-services/open-data",
        "licence": "CC BY 4.0 (CBS open data)",
        "licence_url": "https://www.cbs.nl/en-gb/about-us/website/copyright",
        "attribution": "Source: Statistics Netherlands (CBS), average purchase prices of existing own homes.",
        "allowed_use": (
            "Free re-use with attribution. Figures are the MEAN purchase price, "
            "not the median, and cover existing owner-occupied dwellings only "
            "— newly built homes and the rental stock are out of scope. "
            "Published per province; municipality figures exist in the source "
            "but no matching boundary data is held here."
        ),
        "update_frequency": "Annual",
        "geographic_coverage": "Netherlands, national and 12 provinces",
        "historical_coverage": "1995 onwards",
        "source_published_at": None,
    },
    {
        "key": "dk_statbank_prices",
        "name": "Sales of property by region and category (EJEN77)",
        "owner": "Danmarks Statistik",
        "url": "https://www.statbank.dk/EJEN77",
        "documentation_url": "https://www.dst.dk/en/Statistik/brug-statistikken/muligheder-i-statistikbanken/api",
        "licence": "CC BY 4.0 (Danmarks Statistik open data)",
        "licence_url": "https://www.dst.dk/en/OmDS/omweb/rettigheder",
        "attribution": "Source: Statistics Denmark, StatBank table EJEN77.",
        "allowed_use": (
            "Free re-use with attribution. Figures are the MEAN price per "
            "property, not the median, and cover single-family houses sold in "
            "ordinary arm's-length trade ('almindelig fri handel') — family "
            "transfers and other non-market sales are excluded, and flats and "
            "multi-dwelling properties are not included. Published quarterly; "
            "annual figures here are weighted by each quarter's sale count."
        ),
        "update_frequency": "Quarterly",
        "geographic_coverage": "Denmark, national and 5 regions",
        "historical_coverage": "1992 onwards",
        "source_published_at": None,
    },
    {
        "key": "se_scb_prices",
        "name": "Real estate prices for one- and two-dwelling buildings (BO0501)",
        "owner": "Statistiska centralbyrån (Statistics Sweden)",
        "url": "https://www.statistikdatabasen.scb.se/pxweb/en/ssd/START__BO__BO0501/",
        "documentation_url": "https://www.scb.se/en/services/open-data-api/",
        "licence": "CC0 1.0 (SCB open data)",
        "licence_url": "https://www.scb.se/en/About-us/about-the-website-and-terms-of-use/",
        "attribution": "Source: Statistics Sweden (SCB), real estate prices BO0501.",
        "allowed_use": (
            "Free re-use. Figures are the MEAN purchase price, not the median, "
            "and cover one- and two-dwelling buildings for permanent living — "
            "houses. Tenant-owned flats ('bostadsrätter') are a separate legal "
            "form held in a separate table and are NOT included, so these "
            "figures understate the cost of city housing, where flats "
            "dominate. Holiday homes and agricultural property are also "
            "excluded."
        ),
        "update_frequency": "Annual",
        "geographic_coverage": "Sweden, national and 21 counties",
        "historical_coverage": "2000 onwards",
        "source_published_at": None,
    },
    {
        "key": "us_census_acs",
        "name": "American Community Survey 5-year, median home value (B25077)",
        "owner": "United States Census Bureau",
        "url": "https://www.census.gov/programs-surveys/acs",
        "documentation_url": "https://www.census.gov/data/developers/data-sets/acs-5year.html",
        "licence": "US Government work — public domain (17 U.S.C. §105)",
        "licence_url": "https://www.census.gov/about/policies/open-government.html",
        "attribution": "Source: U.S. Census Bureau, American Community Survey 5-year estimates.",
        "allowed_use": (
            "Public domain; a free API key is required. IMPORTANT: this is NOT "
            "a transaction price. B25077 is the median SELF-REPORTED value of "
            "owner-occupied homes — the survey asks occupants what they "
            "believe their property would sell for. It therefore covers the "
            "whole owner-occupied stock rather than homes that changed hands, "
            "excludes renters entirely, and owner estimates are known to run "
            "above market outcomes. Not comparable with the recorded-sale "
            "medians used for England and Wales, Ireland or Singapore. "
            "Estimates whose margin of error exceeds a third of the value are "
            "discarded as uninformative."
        ),
        "update_frequency": "Annual (5-year rolling estimates)",
        "geographic_coverage": "United States, ~3,200 counties",
        "historical_coverage": "Latest available 5-year release",
        "source_published_at": None,
    },
    {
        "key": "au_abs_res_dwell",
        "name": "Residential Dwellings: Values, Mean Price and Number by State (RES_DWELL_ST)",
        "owner": "Australian Bureau of Statistics",
        "url": "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/total-value-dwellings",
        "documentation_url": "https://api.gov.au/abs",
        "licence": "Creative Commons Attribution 4.0",
        "licence_url": "https://www.abs.gov.au/website-privacy-copyright-and-disclaimer",
        "attribution": "Source: Australian Bureau of Statistics, Total Value of Dwellings.",
        "allowed_use": (
            "Free re-use with attribution. The figure is the MEAN price of the "
            "residential dwelling stock (total value divided by number of "
            "dwellings) — not a median, and not a transaction price: it "
            "describes what existing housing is worth rather than what changed "
            "hands. Published in thousands of dollars, and per state and "
            "territory only. The sibling RES_DWELL dataflow carries true "
            "transfer medians but publishes them for 'Greater capital' and "
            "'Rest of state' areas, for which no boundary data is held here."
        ),
        "update_frequency": "Quarterly",
        "geographic_coverage": "Australia, 8 states and territories",
        "historical_coverage": "2011 onwards",
        "source_published_at": None,
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
        "key": "natural_earth_admin1",
        "name": "Natural Earth Admin 1 - States and Provinces (10m)",
        "owner": "Natural Earth",
        "url": "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/",
        "documentation_url": "https://github.com/nvkelso/natural-earth-vector",
        "licence": "Public domain",
        "licence_url": "https://www.naturalearthdata.com/about/terms-of-use/",
        "attribution": "Sub-national boundaries from Natural Earth (public domain).",
        "allowed_use": (
            "Unrestricted. Used to give area statistics a shape so they can be "
            "clipped into the map viewport, and to resolve a point to a region. "
            "Never used as a source of prices."
        ),
        "update_frequency": "Irregular",
        "geographic_coverage": "Global, 4,596 sub-national regions",
        "historical_coverage": "Current boundaries",
        "source_published_at": None,
    },
    {
        "key": "natural_earth_admin2",
        "name": "Natural Earth Admin 2 - US Counties (10m)",
        "owner": "Natural Earth",
        "url": "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/",
        "documentation_url": "https://github.com/nvkelso/natural-earth-vector",
        "licence": "Public domain",
        "licence_url": "https://www.naturalearthdata.com/about/terms-of-use/",
        "attribution": "US county boundaries from Natural Earth (public domain).",
        "allowed_use": (
            "Unrestricted. Keyed by 5-digit FIPS code, which is what the US "
            "Census API returns, so county figures join to a shape without "
            "name matching — necessary because US county names are not unique "
            "across states. Never used as a source of prices."
        ),
        "update_frequency": "Irregular",
        "geographic_coverage": "United States, 3,224 counties",
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
