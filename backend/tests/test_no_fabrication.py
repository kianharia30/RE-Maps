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
class TestCountriesWithoutPricesShowNothing:
    """A growth rate is not a price, so it is not shown at all.

    These countries publish an official house price index and nothing else. An
    index measures change: it says prices moved 4.2%, never what a home costs.
    Rather than put a percentage on a map where every other country shows
    money, they are registered as having no usable data -- with the reason, so
    the API explains itself instead of shrugging.

    The index is still held in `market_indices`; it is real data. It is simply
    not something this map can present as a price.
    """

    NO_PRICES = [
        ("Berlin", 52.5200, 13.4050),
        ("Madrid", 40.4168, -3.7038),
        ("Rome", 41.9028, 12.4964),
        ("Warsaw", 52.2297, 21.0122),
        ("Lisbon", 38.7223, -9.1393),
        ("New York", 40.7128, -74.0060),
        ("Los Angeles", 34.0522, -118.2437),
    ]

    @pytest.mark.parametrize("name,lat,lon", NO_PRICES)
    def test_no_figure_of_any_kind_is_returned(self, client, name, lat, lon):
        for zoom in (8, 13, 17):
            d = 0.6 if zoom < 12 else 0.02
            body = client.get(
                "/api/map/prices",
                params={
                    "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                    "zoom": zoom, "year": 2025,
                },
            ).json()
            assert body["areas"] == [], f"{name} at z{zoom} returned areas"
            assert body["properties"] == [], f"{name} at z{zoom} returned properties"
            assert body["status"] != "OK", f"{name} at z{zoom} reported OK"

    @pytest.mark.parametrize("name,lat,lon", NO_PRICES)
    def test_the_reason_is_given_rather_than_a_bare_refusal(
        self, client, name, lat, lon
    ):
        """`entry` is withheld for an unsupported place, by design — so the
        explanation has to travel in `message`, and it must be the specific
        one from the registry, not the generic sentence."""
        body = client.get(
            "/api/coverage/at", params={"lat": lat, "lon": lon}
        ).json()
        assert body["status"] == "UNSUPPORTED_LOCATION"
        assert body["entry"] is None, "an unsupported place must not carry an entry"
        message = (body["message"] or "").lower()
        assert "index" in message, f"{name} does not mention the index"
        assert "no property prices are published" in message, name

    @pytest.mark.parametrize("name,lat,lon", NO_PRICES)
    def test_they_are_not_listed_as_supported(self, client, name, lat, lon):
        body = client.get("/api/coverage").json()
        supported = {e["country_iso2"] for e in body["supported"]}
        absent = {e["country_iso2"] for e in body["known_absences"]}
        iso = client.get(
            "/api/coverage/at", params={"lat": lat, "lon": lon}
        ).json()["country_iso2"]
        assert iso not in supported, f"{name} ({iso}) is listed as supported"
        assert iso in absent, f"{name} ({iso}) is missing from known_absences"


@pytest.mark.db
class TestEveryFigureShownIsRealMoney:
    """Nothing reaches the map without an actual monetary value behind it."""

    VIEWPORTS = [
        ("Europe", "-10,36,30,60", 4),
        ("British Isles", "-11,49,2,59", 6),
        ("Nordics", "5,54,31,70", 5),
        ("North America", "-125,25,-66,50", 4),
        ("Asia", "95,-10,145,40", 4),
    ]

    @pytest.mark.parametrize("name,bbox,zoom", VIEWPORTS)
    @pytest.mark.parametrize("year", [2022, 2025])
    def test_no_area_is_returned_without_a_price(
        self, client, name, bbox, zoom, year
    ):
        body = client.get(
            "/api/map/prices",
            params={"bbox": bbox, "zoom": zoom, "year": year},
        ).json()
        for area in body["areas"]:
            assert area["median_price"] is not None, (
                f"{name} {year}: {area['area_name']} has no price"
            )
            assert area["has_price_level"] is True
            assert area["basis"] != "OFFICIAL_INDEX", (
                f"{name} {year}: {area['area_name']} came from an index"
            )
            assert area["median_price"] > 0

    @pytest.mark.parametrize("name,bbox,zoom", VIEWPORTS)
    def test_one_figure_per_area(self, client, name, bbox, zoom):
        body = client.get(
            "/api/map/prices",
            params={"bbox": bbox, "zoom": zoom, "year": 2025},
        ).json()
        names = [a["area_name"] for a in body["areas"]]
        duplicated = {n for n in names if names.count(n) > 1}
        assert not duplicated, f"{name}: duplicated areas {duplicated}"

    def test_france_shows_its_own_recorded_sales(self, client):
        """France has real sales for 2022; they must be what is shown."""
        body = client.get(
            "/api/map/prices",
            params={"bbox": "-5,42,9,51", "zoom": 6, "year": 2022},
        ).json()
        assert body["status"] == "OK"
        assert body["areas"], "France returned nothing for a year it has data"
        for area in body["areas"]:
            assert area["basis"] == "TRANSACTIONS"
            assert area["currency"] == "EUR"


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
