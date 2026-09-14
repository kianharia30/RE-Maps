"""The tests that matter most (§41): unsupported locations must NEVER produce
a price, and no code path may invent one.

These are written as guarantees about the system as a whole rather than about
one function, because fabrication would most plausibly creep in as a
well-meaning fallback somewhere in the chain.
"""
from __future__ import annotations

import inspect

import pytest

from app.core import coverage as coverage_mod
from app.models.enums import DataStatus, PrecisionLevel, PriceType
from app.providers import registry


class TestNoMockProviderExists:
    """§20: there must be no demo/mock/sample provider to fall back to."""

    def test_registry_has_no_fallback_provider(self):
        assert registry.for_country(None) is None
        for iso2 in ["JP", "DE", "BR", "ZZ", "XX", "US", "AU", "CN", "IN", "RU"]:
            provider = registry.for_country(iso2)
            if provider is not None:
                # If a provider ever claims one of these, it must be a real one
                # with real declared sources — not a placeholder.
                assert provider.source_keys, f"{iso2} provider has no sources"
                assert "mock" not in provider.key.lower()
                assert "demo" not in provider.key.lower()
                assert "sample" not in provider.key.lower()
                assert "fake" not in provider.key.lower()

    def test_no_provider_class_is_named_like_a_stub(self):
        for provider in registry.all_providers():
            name = type(provider).__name__.lower()
            for banned in ("mock", "demo", "fake", "stub", "sample", "dummy", "test"):
                assert banned not in name, f"{name} looks like a placeholder provider"

    def test_every_provider_declares_real_sources(self):
        for provider in registry.all_providers():
            assert provider.source_keys, f"{provider.key} declares no data sources"
            assert provider.currency_code, f"{provider.key} declares no currency"


class TestNoRandomnessInPricePaths:
    """A price must never depend on a random number generator."""

    MODULES = [
        "app.valuation.avm",
        "app.valuation.comparables",
        "app.valuation.index_adjust",
        "app.valuation.backcast",
        "app.valuation.confidence",
        "app.forecast.model",
        "app.forecast.service",
        "app.api.routes.map",
        "app.api.routes.property",
        "app.providers.uk.provider",
        "app.providers.fr.provider",
    ]

    @pytest.mark.parametrize("module_name", MODULES)
    def test_no_random_import(self, module_name):
        module = __import__(module_name, fromlist=["*"])
        source = inspect.getsource(module)
        for banned in ("import random", "from random import", "numpy.random",
                       "np.random", "random.uniform", "random.gauss",
                       "random.randint", "random.choice"):
            assert banned not in source, (
                f"{module_name} references {banned!r}; prices must be "
                "deterministic and evidence-derived"
            )


class TestPriceTypeIntegrity:
    """§57.3/§57.4: forecasts must not be presentable as actual values, and
    estimates must not be presentable as transactions."""

    def test_price_types_are_distinct_values(self):
        values = [t.value for t in PriceType]
        assert len(values) == len(set(values))

    def test_transaction_is_the_only_observed_type(self):
        from datetime import date

        from app.models.price import Price

        def make(price_type: PriceType) -> Price:
            return Price(
                value=100.0, currency="GBP", price_type=price_type,
                date=date(2024, 1, 1),
                precision_level=PrecisionLevel.PROPERTY_ESTIMATE,
                methodology="test",
            )

        assert make(PriceType.TRANSACTION).is_observed is True
        for other in (
            PriceType.CURRENT_ESTIMATE, PriceType.HISTORICAL_ESTIMATE,
            PriceType.FORECAST, PriceType.REGIONAL_STATISTIC,
        ):
            assert make(other).is_observed is False

    def test_exact_transaction_precision_is_reserved_for_transactions(self):
        """Only a recorded sale may claim EXACT_TRANSACTION precision.

        Enforced by inspection of the provider source: the constant may only
        appear alongside a TRANSACTION price type.
        """
        import app.providers.fr.provider as fr
        import app.providers.uk.provider as uk

        for module in (uk, fr):
            source = inspect.getsource(module)
            for line_no, line in enumerate(source.splitlines(), 1):
                if "PrecisionLevel.EXACT_TRANSACTION" not in line:
                    continue
                # Allowed: the class-level max_precision declaration, and the
                # transaction branch of price_for_year.
                window = "\n".join(
                    source.splitlines()[max(0, line_no - 12):line_no + 3]
                )
                assert (
                    "max_precision" in line
                    or "PriceType.TRANSACTION" in window
                    or "max_precision" in window
                ), f"{module.__name__}:{line_no} claims exact-transaction precision outside a transaction"


class TestErrorsAreDistinctFromMissingData:
    """§36: a provider failure must not be reported as 'no data exists'."""

    def test_error_classes_carry_distinct_statuses(self):
        from app.core.errors import (
            InsufficientEvidence,
            OutOfRange,
            ProviderError,
            UnsupportedLocation,
        )

        assert UnsupportedLocation().data_status is DataStatus.UNSUPPORTED_LOCATION
        assert InsufficientEvidence().data_status is DataStatus.INSUFFICIENT_EVIDENCE
        assert ProviderError().data_status is DataStatus.PROVIDER_ERROR
        assert OutOfRange().data_status is DataStatus.OUT_OF_RANGE
        # The failure case must be a 5xx, the honest-absence cases must not be.
        assert ProviderError().status_code >= 500
        assert UnsupportedLocation().status_code == 200
        assert InsufficientEvidence().status_code == 200

    def test_provider_error_message_does_not_claim_absence(self):
        from app.core.errors import ProviderError

        message = ProviderError().message.lower()
        assert "not available" not in message
        assert "no data" not in message
        assert "try again" in message

    def test_unsupported_message_is_the_specified_wording(self):
        from app.core.errors import UnsupportedLocation

        assert (
            UnsupportedLocation().message
            == "Property price data is not currently available for this location."
        )


@pytest.mark.db
class TestUnsupportedLocationsReturnNoPrices:
    """End-to-end: the API must return no price anywhere unsupported."""

    # (name, lat, lon) — places with no integrated provider.
    # Countries that publish nothing usable. Berlin and New York are
    # deliberately NOT here any more: Germany is covered by Eurostat and the
    # United States by the FHFA, so reporting them as unsupported would now be
    # as wrong as inventing a price for them. They are tested in
    # TestStatisticsOnlyCoverage instead, which is stricter.
    UNSUPPORTED = [
        ("Tokyo", 35.6895, 139.6917),
        ("Sydney", -33.8688, 151.2093),
        ("Mumbai", 19.0760, 72.8777),
        ("Sao Paulo", -23.5505, -46.6333),
        ("Cairo", 30.0444, 31.2357),
        ("Lagos", 6.5244, 3.3792),
        ("Buenos Aires", -34.6037, -58.3816),
        ("Mid-Atlantic ocean", 30.0, -30.0),
        ("Edinburgh (Scotland: no open transaction data)", 55.9533, -3.1883),
        ("Belfast (Northern Ireland: no open transaction data)", 54.5973, -5.9301),
    ]

    @pytest.mark.parametrize("name,lat,lon", UNSUPPORTED)
    def test_map_returns_no_prices(self, client, name, lat, lon):
        d = 0.05
        bbox = f"{lon - d},{lat - d},{lon + d},{lat + d}"
        for zoom in (5, 10, 14, 17):
            resp = client.get(
                "/api/map/prices",
                params={"bbox": bbox, "zoom": zoom, "year": 2026},
            )
            assert resp.status_code == 200, name
            body = resp.json()
            assert body["status"] != "OK", f"{name} at zoom {zoom} returned OK"
            assert body["properties"] == [], f"{name} returned property prices"
            assert body["areas"] == [], f"{name} returned area prices"

    @pytest.mark.parametrize("name,lat,lon", UNSUPPORTED)
    def test_coverage_reports_unsupported(self, client, name, lat, lon):
        resp = client.get("/api/coverage/at", params={"lat": lat, "lon": lon})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "UNSUPPORTED_LOCATION", name
        assert body["entry"] is None
        # The message must either be the specified default sentence or a more
        # specific, non-empty explanation of why there is no data. What it must
        # never be is empty, or a price.
        message = body["message"] or ""
        assert message.strip(), f"{name} gave no explanation"
        assert (
            "not currently available" in message
            or "no price is shown" in message.lower()
        ), f"{name}: message does not communicate unavailability: {message!r}"

    @pytest.mark.parametrize("name,lat,lon", UNSUPPORTED)
    def test_forecast_is_refused(self, client, name, lat, lon):
        resp = client.get(
            "/api/location/forecast", params={"lat": lat, "lon": lon, "year": 2030}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] != "OK", name
        assert "market_movement_pct" not in body


@pytest.mark.db
class TestAWideViewportDoesNotLeakAcrossBorders:
    """The viewport centre gates the request, and a continent has many centres.

    A view of Europe centred a few degrees off lands on Switzerland, which
    Eurostat's series excludes. That single point used to blank a map showing
    27 countries we do cover, so an area tier now asks the data instead.

    The danger in relaxing that gate is substitution: the countries in view
    include the UK, so a viewport over Scotland must NOT come back with England
    and Wales's median. An earlier version of the fallback did exactly that.
    """

    def _get(self, client, bbox, zoom, year=2025):
        resp = client.get(
            "/api/map/prices",
            params={"bbox": bbox, "zoom": zoom, "year": year},
        )
        assert resp.status_code == 200
        return resp.json()

    # (name, bbox, zoom) — centre is in a country with no registry row.
    SPANNING = [
        ("centre in Switzerland", "-8.5,40.5,24.5,54.5", 4.2),
        ("centre in the Atlantic", "-20,35,15,58", 4),
        ("centre in Belarus", "18,48,38,60", 4.5),
    ]

    @pytest.mark.parametrize("name,bbox,zoom", SPANNING)
    def test_covered_countries_in_view_are_still_served(
        self, client, name, bbox, zoom
    ):
        body = self._get(client, bbox, zoom)
        assert body["status"] == "OK", f"{name}: {body.get('message')}"
        assert body["areas"], f"{name} returned nothing despite covered countries"
        assert body["properties"] == []
        # Every marker must sit at country level and name a real country.
        for area in body["areas"]:
            assert area["area_level"] == "country"
            assert area["area_name"]

    @pytest.mark.parametrize("name,bbox,zoom", SPANNING)
    def test_no_marker_is_attributed_to_an_uncovered_country(
        self, client, name, bbox, zoom
    ):
        """Switzerland and Belarus must not appear, even when centred on."""
        body = self._get(client, bbox, zoom)
        names = {a["area_name"] for a in body["areas"]}
        for uncovered in ("Switzerland", "Belarus", "Ukraine", "Russia"):
            assert uncovered not in names, (
                f"{uncovered} has no data but received a figure"
            )

    # A registered absence must refuse outright, never borrow a neighbour's
    # figure — including when the viewport also spans England.
    ABSENCES = [
        ("Scotland", "-4.5,55.5,-2.5,56.5", 8),
        ("Scotland spanning England", "-6,54,0,59", 6),
        ("Northern Ireland", "-6.5,54.3,-5.5,54.9", 8),
        ("Northern Ireland spanning Wales", "-7,52.5,-4,55.5", 6),
    ]

    @pytest.mark.parametrize("name,bbox,zoom", ABSENCES)
    def test_a_registered_absence_is_never_given_a_neighbours_figure(
        self, client, name, bbox, zoom
    ):
        body = self._get(client, bbox, zoom)
        assert body["status"] == "UNSUPPORTED_LOCATION", (
            f"{name} was served data: {body['areas'][:1]}"
        )
        assert body["areas"] == []
        assert body["properties"] == []
        # And it must say why, naming the jurisdiction.
        assert body["message"]
        assert "not currently available" in body["message"]


@pytest.mark.db
class TestTheRegistryListingSeparatesAbsenceFromCoverage:
    """`supported` must not contain places we know have no data.

    Scotland and Northern Ireland are registered precisely to record that HM
    Land Registry does not cover them. Listing them under `supported` — which
    the endpoint did — invites a client to conclude the opposite.
    """

    def test_supported_contains_only_usable_coverage(self, client):
        body = client.get("/api/coverage").json()
        for entry in body["supported"]:
            assert entry["max_precision"] != "NONE", (
                f"{entry['region_name']} is listed as supported but has no data"
            )

    def test_absences_are_still_reported_with_a_reason(self, client):
        body = client.get("/api/coverage").json()
        absences = body["known_absences"]
        names = {e["region_name"] for e in absences}
        assert "Scotland" in names
        assert "Northern Ireland" in names
        for entry in absences:
            assert entry["max_precision"] == "NONE"
            assert entry["transaction_level_data"] is False
            # An absence is only useful if it says why.
            assert entry["notes"], f"{entry['region_name']} gives no reason"

    def test_every_registered_row_appears_exactly_once(self, client):
        body = client.get("/api/coverage").json()
        keys = [
            (e["country_iso2"], e["region_code"])
            for e in body["supported"] + body["known_absences"]
        ]
        assert len(keys) == len(set(keys)), "a jurisdiction is listed twice"


@pytest.mark.db
class TestAreaLevelPriceLevels:
    """Countries with real money but no individual sales.

    Ireland publishes every declared sale (so we compute a MEDIAN), while the
    Dutch and Danish statistics offices publish an average (a MEAN). All three
    give a real monetary figure; none of them can place an individual dwelling.

    The mean/median distinction is the sharp edge here: for right-skewed house
    prices the mean sits well above the median, so presenting a published mean
    as "median" would overstate typical prices.
    """

    # (name, lat, lon, currency, expected statistic)
    PLACES = [
        ("Dublin", 53.3498, -6.2603, "EUR", "MEDIAN"),
        ("Cork", 51.8985, -8.4756, "EUR", "MEDIAN"),
        ("Amsterdam", 52.3676, 4.9041, "EUR", "MEAN"),
        ("Utrecht", 52.0900, 5.1200, "EUR", "MEAN"),
        ("Copenhagen", 55.6761, 12.5683, "DKK", "MEAN"),
        ("Aarhus", 56.1629, 10.2039, "DKK", "MEAN"),
    ]

    def _areas(self, client, lat, lon, zoom=9, year=2025):
        d = 0.35
        resp = client.get(
            "/api/map/prices",
            params={
                "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                "zoom": zoom, "year": year,
            },
        )
        assert resp.status_code == 200
        return resp.json()

    @pytest.mark.parametrize("name,lat,lon,currency,statistic", PLACES)
    def test_a_real_monetary_figure_is_returned(
        self, client, name, lat, lon, currency, statistic
    ):
        body = self._areas(client, lat, lon)
        assert body["status"] == "OK", f"{name}: {body.get('message')}"
        priced = [a for a in body["areas"] if a["median_price"] is not None]
        assert priced, f"{name} returned no monetary figure"
        for area in priced:
            assert area["has_price_level"] is True
            assert area["median_price"] > 0
            assert area["currency"] == currency
            # A plausible dwelling price, to catch a unit error such as the
            # Danish source publishing thousands of kroner.
            assert 10_000 < area["median_price"] < 100_000_000, (
                f"{name}: {area['median_price']} {currency} is not a "
                "plausible dwelling price — check the source's units"
            )

    @pytest.mark.parametrize("name,lat,lon,currency,statistic", PLACES)
    def test_the_statistic_is_labelled_correctly(
        self, client, name, lat, lon, currency, statistic
    ):
        body = self._areas(client, lat, lon)
        priced = [a for a in body["areas"] if a["median_price"] is not None]
        for area in priced:
            assert area["price_statistic"] == statistic, (
                f"{name} reports {area['price_statistic']} but this source "
                f"publishes a {statistic}"
            )

    @pytest.mark.parametrize("name,lat,lon,currency,statistic", PLACES)
    def test_an_undisclosed_sample_size_is_null_not_zero(
        self, client, name, lat, lon, currency, statistic
    ):
        """Zero would assert that no sales took place."""
        body = self._areas(client, lat, lon)
        for area in body["areas"]:
            if area["median_price"] is not None:
                assert area["transaction_count"] != 0, (
                    f"{name} claims 0 sales behind a real price"
                )

    @pytest.mark.parametrize("name,lat,lon,currency,statistic", PLACES)
    def test_individual_dwellings_are_still_refused(
        self, client, name, lat, lon, currency, statistic
    ):
        body = self._areas(client, lat, lon, zoom=17)
        assert body["properties"] == [], f"{name} served individual dwellings"
        assert body["status"] != "OK"


@pytest.mark.db
class TestStatisticsOnlyCoverage:
    """Countries covered by an official index but with no individual sales.

    These are the newest and most easily abused rows in the registry: it would
    be trivial to turn an index into a plausible-looking price. Every assertion
    here exists to stop that.
    """

    # (name, lat, lon, expected currency)
    STATISTICS_ONLY = [
        ("Berlin", 52.5200, 13.4050, "EUR"),
        ("Madrid", 40.4168, -3.7038, "EUR"),
        ("Rome", 41.9028, 12.4964, "EUR"),
        ("Warsaw", 52.2297, 21.0122, "PLN"),
        ("Stockholm", 59.3293, 18.0686, "SEK"),
        ("New York", 40.7128, -74.0060, "USD"),
        ("Los Angeles", 34.0522, -118.2437, "USD"),
    ]

    @pytest.mark.parametrize("name,lat,lon,currency", STATISTICS_ONLY)
    def test_coverage_is_reported_but_without_transaction_data(
        self, client, name, lat, lon, currency
    ):
        resp = client.get("/api/coverage/at", params={"lat": lat, "lon": lon})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "OK", f"{name} should be covered"
        entry = body["entry"]
        assert entry is not None
        assert entry["transaction_level_data"] is False, (
            f"{name} claims individual sales data it does not have"
        )
        assert entry["max_precision"] == "CITY_REGIONAL"
        assert entry["forecast_supported"] is False, (
            f"{name} claims forecasts, but an index has no price level to project"
        )
        assert entry["currency_code"] == currency
        # The notes must say plainly that this is an index, not a price.
        assert "index" in (entry["notes"] or "").lower()
        assert entry["sources"], f"{name} cites no source"

    @pytest.mark.parametrize("name,lat,lon,currency", STATISTICS_ONLY)
    def test_area_figures_carry_growth_but_never_a_price(
        self, client, name, lat, lon, currency
    ):
        d = 0.6
        resp = client.get(
            "/api/map/prices",
            params={
                "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                "zoom": 8,
                "year": 2025,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "OK", f"{name}: {body.get('message')}"
        assert body["areas"], f"{name} returned no area statistics"
        assert body["properties"] == [], f"{name} returned individual dwellings"

        for area in body["areas"]:
            assert area["basis"] == "OFFICIAL_INDEX", area
            # THE central assertion: an index must never yield a price.
            assert area["has_price_level"] is False, (
                f"{name} claims a price level derived from an index"
            )
            assert area["median_price"] is None, (
                f"{name} produced a price of {area['median_price']} from an index"
            )
            assert area["p25_price"] is None and area["p75_price"] is None
            assert area["index_value"] is not None
            assert area["growth_1y_pct"] is not None, (
                f"{name} has neither a price nor a growth rate — nothing to show"
            )
            assert area["precision_level"] == "CITY_REGIONAL"

    @pytest.mark.parametrize("name,lat,lon,currency", STATISTICS_ONLY)
    def test_individual_property_requests_are_refused(
        self, client, name, lat, lon, currency
    ):
        d = 0.02
        resp = client.get(
            "/api/map/prices",
            params={
                "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                "zoom": 17,
                "year": 2025,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] != "OK", f"{name} served individual dwellings"
        assert body["properties"] == []
        assert body["areas"] == []
        assert "area statistics only" in (body["message"] or "").lower()

    @pytest.mark.parametrize("name,lat,lon,currency", STATISTICS_ONLY)
    def test_forecasts_are_refused(self, client, name, lat, lon, currency):
        """An index has no price level, so there is nothing to project."""
        resp = client.get(
            "/api/location/forecast", params={"lat": lat, "lon": lon, "year": 2030}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] != "OK", f"{name} produced a forecast"
        assert "market_movement_pct" not in body

    @pytest.mark.parametrize("name,lat,lon,currency", STATISTICS_ONLY)
    def test_future_years_show_nothing_rather_than_a_projection(
        self, client, name, lat, lon, currency
    ):
        d = 0.6
        resp = client.get(
            "/api/map/prices",
            params={
                "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                "zoom": 8,
                "year": 2031,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_future"] is True
        # No price level means no defensible projection.
        assert body["areas"] == [], f"{name} projected an index with no price level"


@pytest.mark.db
class TestRealSalesBeatAnIndex:
    """A country may hold both real sales and an official index for one year.

    France does: recorded sales for 2021-2023 and a Eurostat series throughout.
    Two things must hold. Exactly ONE figure per country reaches the map, and
    when there is a choice it is the measured one — a real median beats a
    measure of change.
    """

    EUROPE = "-10,36,30,60"

    def test_one_figure_per_country(self, client):
        for year in (2022, 2025):
            resp = client.get(
                "/api/map/prices",
                params={"bbox": self.EUROPE, "zoom": 4, "year": year},
            )
            assert resp.status_code == 200
            areas = resp.json()["areas"]
            names = [a["area_name"] for a in areas]
            duplicated = {n for n in names if names.count(n) > 1}
            assert not duplicated, (
                f"{year}: several markers for the same country: {duplicated}"
            )

    def test_a_measured_median_is_preferred_over_an_index(self, client):
        """2022 is a year France has real recorded sales for."""
        resp = client.get(
            "/api/map/prices",
            params={"bbox": self.EUROPE, "zoom": 4, "year": 2022},
        )
        france = [a for a in resp.json()["areas"] if a["area_name"] == "France"]
        assert len(france) == 1, "France should appear exactly once"
        assert france[0]["basis"] == "TRANSACTIONS", (
            "an index was served for a year with real recorded sales"
        )
        assert france[0]["median_price"] is not None
        assert france[0]["has_price_level"] is True

    def test_the_index_is_used_only_where_there_are_no_sales(self, client):
        """2025 has no French sales loaded, so the index is all there is."""
        resp = client.get(
            "/api/map/prices",
            params={"bbox": self.EUROPE, "zoom": 4, "year": 2025},
        )
        france = [a for a in resp.json()["areas"] if a["area_name"] == "France"]
        assert len(france) == 1
        assert france[0]["basis"] == "OFFICIAL_INDEX"
        # And it must still refuse to state a price.
        assert france[0]["median_price"] is None
        assert france[0]["has_price_level"] is False
        assert france[0]["growth_1y_pct"] is not None


@pytest.mark.db
class TestCoverageIsBackedByData:
    """§57.17: a country may only be claimed if real data exists behind it."""

    @pytest.mark.asyncio
    async def test_every_claimed_country_has_transactions_or_an_index(self):
        from app.db import fetch_one

        index = await coverage_mod.index()
        assert index.supported, "no coverage rows at all — nothing is claimed"
        for entry in index.supported:
            row = await fetch_one(
                """
                SELECT (SELECT count(*) FROM transactions WHERE country_iso2 = %s) AS txns,
                       (SELECT count(*) FROM market_indices WHERE country_iso2 = %s) AS idx
                """,
                (entry.country_iso2, entry.country_iso2),
            )
            assert (row["txns"] or 0) > 0 or (row["idx"] or 0) > 0, (
                f"{entry.country_iso2} is claimed in provider_coverage but has no data"
            )

    @pytest.mark.asyncio
    async def test_transaction_flag_matches_reality(self):
        from app.db import fetch_one

        index = await coverage_mod.index()
        for entry in index.supported:
            if not entry.transaction_level_data:
                continue
            row = await fetch_one(
                "SELECT count(*) AS n FROM transactions WHERE country_iso2 = %s",
                (entry.country_iso2,),
            )
            assert row["n"] > 0, (
                f"{entry.country_iso2} claims transaction-level data but has none"
            )

    @pytest.mark.asyncio
    async def test_forecast_flag_requires_an_index(self):
        from app.db import fetch_one

        index = await coverage_mod.index()
        for entry in index.supported:
            if not entry.forecast_supported:
                continue
            row = await fetch_one(
                "SELECT count(*) AS n FROM market_indices WHERE country_iso2 = %s",
                (entry.country_iso2,),
            )
            assert row["n"] > 0, (
                f"{entry.country_iso2} claims forecasts but has no index series"
            )

    @pytest.mark.asyncio
    async def test_every_source_key_resolves_to_a_registered_source(self):
        from app.db import fetch_all

        index = await coverage_mod.index()
        known = {r["key"] for r in await fetch_all("SELECT key FROM data_sources")}
        for entry in index.supported:
            for source in entry.sources:
                assert source.key in known
                assert source.attribution, f"{source.key} has no attribution text"
                assert source.licence, f"{source.key} has no licence recorded"
