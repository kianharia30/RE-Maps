"""API contract: input validation, HTTP semantics, and response shape (§30)."""
from __future__ import annotations

import pytest


class TestValidation:
    """Every input is validated; bad input is a 4xx, never a 500."""

    @pytest.mark.parametrize(
        "params",
        [
            {},                                                   # nothing
            {"bbox": "1,2,3", "zoom": 10, "year": 2024},           # short bbox
            {"bbox": "a,b,c,d", "zoom": 10, "year": 2024},          # non-numeric
            {"bbox": "-1,50,1,52", "zoom": 99, "year": 2024},       # zoom too high
            {"bbox": "-1,50,1,52", "zoom": -5, "year": 2024},       # zoom negative
            {"bbox": "-1,50,1,52", "zoom": 10, "year": 1500},       # year too early
            {"bbox": "-1,50,1,52", "zoom": 10, "year": 3000},       # year too late
            {"bbox": "-1,52,1,50", "zoom": 10, "year": 2024},       # inverted lat
            {"bbox": "-1,50,1,52", "zoom": 10},                     # missing year
            {"bbox": "-1,50,1,52", "zoom": 10, "year": 2024,
             "segment": "mansion"},                                 # bad segment
        ],
    )
    def test_map_rejects_bad_input(self, client, params):
        resp = client.get("/api/map/prices", params=params)
        assert 400 <= resp.status_code < 500, (
            f"{params} gave {resp.status_code}, expected a client error"
        )

    @pytest.mark.parametrize(
        "params",
        [
            {}, {"lat": 200, "lon": 0}, {"lat": 0, "lon": 400},
            {"lat": "x", "lon": 0}, {"lat": 52},
        ],
    )
    def test_coverage_rejects_bad_coordinates(self, client, params):
        assert 400 <= client.get("/api/coverage/at", params=params).status_code < 500

    @pytest.mark.parametrize("q", ["", " "])
    def test_search_rejects_empty_query(self, client, q):
        assert client.get("/api/search", params={"q": q}).status_code in (400, 422)

    def test_search_rejects_an_absurd_limit(self, client):
        assert client.get(
            "/api/search", params={"q": "London", "limit": 5000}
        ).status_code == 422

    @pytest.mark.parametrize("pid", [0, -1, "abc"])
    def test_property_rejects_bad_ids(self, client, pid):
        assert 400 <= client.get(f"/api/property/{pid}").status_code < 500

    def test_unknown_property_is_404_not_500(self, client):
        resp = client.get("/api/property/999999999")
        assert resp.status_code == 404
        assert resp.json()["status"] == "NO_DATA"


class TestMeta:
    def test_health_reports_what_is_loaded(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "row_estimates" in body and "counts" in body
        # Whether EPC enrichment is active must be visible, not implied.
        assert "epc_enrichment" in body

    def test_openapi_schema_is_served(self, client):
        assert client.get("/openapi.json").status_code == 200


@pytest.mark.db
class TestSources:
    def test_every_source_has_a_licence_and_attribution(self, client):
        resp = client.get("/api/sources")
        assert resp.status_code == 200
        sources = resp.json()
        assert sources, "no data sources registered"
        for s in sources:
            assert s["licence"], f"{s['key']} has no licence"
            assert s["attribution"], f"{s['key']} has no attribution"
            assert s["url"].startswith("http"), f"{s['key']} has no usable URL"
            assert s["owner"], f"{s['key']} has no owner"

    def test_coverage_index_lists_only_backed_jurisdictions(self, client):
        resp = client.get("/api/coverage")
        assert resp.status_code == 200
        body = resp.json()
        assert "supported" in body
        for entry in body["supported"]:
            # Entries that record an absence must be honest about it.
            if entry["max_precision"] == "NONE":
                assert entry["transaction_level_data"] is False
                assert entry["notes"], "an absence row must explain itself"
            else:
                assert entry["sources"], f"{entry['country_iso2']} cites no sources"


@pytest.mark.db
class TestPostcodeSearch:
    """UK postcodes resolve from our own gazetteer, not the general geocoder.

    Nominatim's free-text search for "MK9 2AB" returned a street in Brazil at
    latitude -9.47; a postcode-shaped query must never be answered that way.
    """

    def test_a_real_postcode_resolves_to_the_right_place(self, client):
        resp = client.get("/api/search", params={"q": "SW1A 1AA", "limit": 3})
        assert resp.status_code == 200
        results = resp.json()
        assert results, "a valid postcode returned nothing"
        top = results[0]
        assert top["kind"] == "postcode"
        assert top["country_iso2"] == "GB"
        # Westminster, to within a kilometre or so.
        assert 51.4 < top["latitude"] < 51.6
        assert -0.25 < top["longitude"] < 0.0

    def test_case_and_spacing_do_not_matter(self, client):
        a = client.get("/api/search", params={"q": "SW1A 1AA"}).json()[0]
        b = client.get("/api/search", params={"q": "sw1a1aa"}).json()[0]
        assert (round(a["latitude"], 4), round(a["longitude"], 4)) == (
            round(b["latitude"], 4), round(b["longitude"], 4)
        )

    def test_an_outcode_resolves_to_its_district(self, client):
        results = client.get("/api/search", params={"q": "MK9"}).json()
        assert results and results[0]["kind"] == "postcode"
        assert results[0]["bbox"] is not None, "an outcode should carry an extent"
        assert 51.8 < results[0]["latitude"] < 52.3

    def test_a_nonexistent_postcode_returns_nothing_rather_than_a_wrong_place(
        self, client
    ):
        """MK9 2AB is not a real postcode. Returning no results is correct;
        returning a plausible-looking foreign street is not."""
        results = client.get("/api/search", params={"q": "MK9 2AB"}).json()
        for r in results:
            assert (r["country_iso2"] or "") == "GB", (
                f"a UK-postcode-shaped query returned {r['display_name']!r}"
            )

    def test_coverage_flag_is_region_aware(self, client):
        """Scotland is in the UK but has no transaction data, so a Scottish
        postcode must not be flagged as covered."""
        english = client.get("/api/search", params={"q": "SW1A 1AA"}).json()[0]
        scottish = client.get("/api/search", params={"q": "EH1 1AA"}).json()[0]
        assert english["has_property_data"] is True
        assert scottish["has_property_data"] is False
        assert "Scotland" in (scottish["coverage_note"] or "")


@pytest.mark.db
class TestSupportedMarketEndToEnd:
    """A covered location must return real, correctly-typed prices."""

    # Central Milton Keynes — inside the covered market.
    BBOX = "-0.765,52.033,-0.745,52.045"

    def test_property_tier_returns_typed_prices(self, client):
        resp = client.get(
            "/api/map/prices",
            params={"bbox": self.BBOX, "zoom": 17, "year": 2026},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "OK"
        assert body["tier"] == "PROPERTY"
        assert body["properties"], "no properties in a covered, dense area"
        assert body["currency"] == "GBP"
        assert body["attributions"], "prices returned with no attribution"

        for p in body["properties"]:
            price = p["price"]
            assert price["value"] > 0
            assert price["currency"] == "GBP"
            assert price["price_type"] in (
                "TRANSACTION", "CURRENT_ESTIMATE", "HISTORICAL_ESTIMATE", "FORECAST"
            )
            assert price["precision_level"] in (
                "EXACT_TRANSACTION", "PROPERTY_ESTIMATE"
            )
            assert price["methodology"], "a price with no stated methodology"
            assert price["sources"], "a price with no source"
            # A recorded sale must not carry a modelled interval, and an
            # estimate must not be presented as exact.
            if price["price_type"] == "TRANSACTION":
                assert price["precision_level"] == "EXACT_TRANSACTION"
            else:
                assert price["precision_level"] != "EXACT_TRANSACTION"
                assert price["confidence"] is not None

    def test_aggregated_tiers_never_claim_property_precision(self, client):
        for zoom in (5, 8, 10, 12):
            resp = client.get(
                "/api/map/prices",
                params={"bbox": "-1.2,51.7,-0.3,52.4", "zoom": zoom, "year": 2024},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["properties"] == [], f"zoom {zoom} returned dwellings"
            for area in body["areas"]:
                assert area["precision_level"] in (
                    "CITY_REGIONAL", "NEIGHBOURHOOD", "STREET_POSTCODE"
                )
                assert area["transaction_count"] > 0, "a median from no sales"

    def test_changing_year_changes_the_answer(self, client):
        """§58.10/58.11: a historical year must produce different figures."""
        def fetch(year):
            r = client.get(
                "/api/map/prices",
                params={"bbox": self.BBOX, "zoom": 17, "year": year},
            )
            return {p["id"]: p["price"] for p in r.json().get("properties", [])}

        now = fetch(2026)
        past = fetch(2022)
        shared = set(now) & set(past)
        assert shared, "no property appeared in both years"
        differing = sum(1 for pid in shared if now[pid]["value"] != past[pid]["value"])
        assert differing > 0, "the selected year had no effect on any price"

    def test_changing_bounds_changes_the_answer(self, client):
        def ids(bbox):
            r = client.get(
                "/api/map/prices", params={"bbox": bbox, "zoom": 17, "year": 2026}
            )
            return {p["id"] for p in r.json().get("properties", [])}

        a = ids(self.BBOX)
        b = ids("-0.80,52.06,-0.78,52.075")
        assert a and b
        assert a != b, "different viewports returned identical properties"

    def test_future_year_is_labelled_as_a_forecast(self, client):
        resp = client.get(
            "/api/map/prices",
            params={"bbox": "-1.2,51.7,-0.3,52.4", "zoom": 10, "year": 2031},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_future"] is True
        if body["status"] == "OK":
            assert body["areas"]
            for area in body["areas"]:
                assert area["is_forecast"] is True
                assert area["confidence"] is not None
