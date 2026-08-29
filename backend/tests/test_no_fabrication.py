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
    UNSUPPORTED = [
        ("Tokyo", 35.6895, 139.6917),
        ("New York", 40.7128, -74.0060),
        ("Sydney", -33.8688, 151.2093),
        ("Mumbai", 19.0760, 72.8777),
        ("Berlin", 52.5200, 13.4050),
        ("Sao Paulo", -23.5505, -46.6333),
        ("Cairo", 30.0444, 31.2357),
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
