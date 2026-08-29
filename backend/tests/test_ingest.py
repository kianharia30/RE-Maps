"""Ingestion correctness: parsing, quality flags, dedup (§32, §52)."""
from __future__ import annotations

from datetime import date

import pytest

from ingest.uk_ppd import PROPERTY_TYPE, TENURE, _parse_row


def ppd_row(**overrides) -> list[str]:
    """A realistic Price Paid row, in the published column order."""
    row = [
        "{42C129E4-C259-60A9-E063-4804A8C0C25D}",  # 0 id
        "635000",                                   # 1 price
        "2025-05-14 00:00",                         # 2 date
        "LN11 8GN",                                 # 3 postcode
        "D",                                        # 4 type
        "N",                                        # 5 new build
        "F",                                        # 6 duration
        "2",                                        # 7 PAON
        "",                                         # 8 SAON
        "KENWICK VIEW",                             # 9 street
        "",                                         # 10 locality
        "LOUTH",                                    # 11 town
        "EAST LINDSEY",                             # 12 district
        "LINCOLNSHIRE",                             # 13 county
        "A",                                        # 14 PPD category
        "A",                                        # 15 record status
    ]
    for index, value in overrides.items():
        row[int(index)] = value
    return row


class TestParsing:
    def test_a_normal_row_parses(self):
        parsed = _parse_row(ppd_row())
        assert parsed is not None
        assert parsed[2] == 635000.0
        assert parsed[3] == date(2025, 5, 14)
        assert parsed[4] == "LN118GN"
        assert parsed[6] == "LN11"          # outcode
        assert parsed[7] == "LN11 8"        # sector
        assert parsed[8] == "detached"
        assert parsed[9] == "freehold"

    @pytest.mark.parametrize("code,expected", list(PROPERTY_TYPE.items()))
    def test_all_property_type_codes(self, code, expected):
        parsed = _parse_row(ppd_row(**{"4": code}))
        assert parsed is not None and parsed[8] == expected

    @pytest.mark.parametrize("code,expected", list(TENURE.items()))
    def test_all_tenure_codes(self, code, expected):
        parsed = _parse_row(ppd_row(**{"6": code}))
        assert parsed is not None and parsed[9] == expected

    def test_unknown_type_code_becomes_other_not_a_guess(self):
        parsed = _parse_row(ppd_row(**{"4": "Z"}))
        assert parsed is not None and parsed[8] == "other"
        # "other" is treated as non-residential and excluded from statistics.
        assert parsed[20] is False


class TestRejection:
    def test_deleted_records_are_dropped(self):
        assert _parse_row(ppd_row(**{"15": "D"})) is None

    def test_change_records_are_kept_for_upsert(self):
        # A 'C' record supersedes an earlier one with the same id; the unique
        # constraint on (source, record id) turns it into an update.
        assert _parse_row(ppd_row(**{"15": "C"})) is not None

    @pytest.mark.parametrize("price", ["0", "1", "50", "99", "", "abc", "-5000"])
    def test_placeholder_and_invalid_prices_are_dropped(self, price):
        assert _parse_row(ppd_row(**{"1": price})) is None

    @pytest.mark.parametrize("raw", ["", "not-a-date", "14/05/2025"])
    def test_unparseable_dates_are_dropped(self, raw):
        assert _parse_row(ppd_row(**{"2": raw})) is None

    def test_short_rows_are_dropped(self):
        assert _parse_row(ppd_row()[:10]) is None


class TestQualityFlags:
    def test_category_a_is_market_value_evidence(self):
        parsed = _parse_row(ppd_row(**{"14": "A"}))
        assert parsed is not None and parsed[19] == "STANDARD"

    def test_category_b_is_flagged_and_excluded_from_evidence(self):
        """HM Land Registry warns category B may not reflect market value."""
        parsed = _parse_row(ppd_row(**{"14": "B"}))
        assert parsed is not None and parsed[19] == "ADDITIONAL"

    def test_unknown_category_is_not_assumed_to_be_market_value(self):
        parsed = _parse_row(ppd_row(**{"14": ""}))
        assert parsed is not None and parsed[19] == "UNKNOWN"

    def test_residential_flag_follows_property_type(self):
        assert _parse_row(ppd_row(**{"4": "D"}))[20] is True
        assert _parse_row(ppd_row(**{"4": "F"}))[20] is True
        assert _parse_row(ppd_row(**{"4": "O"}))[20] is False


class TestIdentityFromRows:
    def test_case_and_spacing_variants_produce_one_dwelling(self):
        a = _parse_row(ppd_row())
        b = _parse_row(ppd_row(**{"3": "ln11 8gn", "9": "kenwick view", "11": "louth"}))
        assert a[1] == b[1], "the same dwelling produced two identity keys"

    def test_a_flat_is_a_different_dwelling_from_the_building(self):
        house = _parse_row(ppd_row())
        flat = _parse_row(ppd_row(**{"8": "FLAT 1"}))
        assert house[1] != flat[1]

    def test_source_record_id_is_preserved_for_dedup(self):
        parsed = _parse_row(ppd_row())
        assert parsed[0] == "{42C129E4-C259-60A9-E063-4804A8C0C25D}"


@pytest.mark.db
class TestIngestedDataIntegrity:
    """Invariants the loaded database must satisfy."""

    def test_no_duplicate_source_records(self):
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            """
            SELECT count(*) AS n FROM (
                SELECT source_key, source_record_id
                FROM transactions
                GROUP BY 1, 2 HAVING count(*) > 1
            ) dupes
            """
        )
        assert row["n"] == 0, f"{row['n']} duplicated source records"

    def test_no_duplicate_property_identities(self):
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            """
            SELECT count(*) AS n FROM (
                SELECT country_iso2, property_key FROM properties
                GROUP BY 1, 2 HAVING count(*) > 1
            ) dupes
            """
        )
        assert row["n"] == 0

    def test_every_transaction_has_a_price_and_currency(self):
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            """
            SELECT count(*) AS n FROM transactions
            WHERE price IS NULL OR price <= 0 OR currency_code IS NULL
            """
        )
        assert row["n"] == 0

    def test_geocoded_transactions_record_their_coordinate_precision(self):
        """A coordinate without a stated precision would let the UI imply a
        rooftop position it does not have (§33)."""
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            "SELECT count(*) AS n FROM transactions "
            "WHERE geom IS NOT NULL AND coordinate_precision IS NULL"
        )
        assert row["n"] == 0

    def test_uk_transactions_are_postcode_precision_not_better(self):
        """Price Paid has no building coordinates; claiming PROPERTY precision
        for it would be a fabrication."""
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            """
            SELECT count(*) AS n FROM transactions
            WHERE country_iso2 = 'GB' AND source_key = 'uk_land_registry_ppd'
              AND coordinate_precision IN ('PROPERTY', 'PARCEL', 'ADDRESS')
            """
        )
        assert row["n"] == 0

    def test_area_stats_respect_their_minimum_sample_sizes(self):
        from app.db import sync_fetch_all
        from ingest.area_stats import TIER_SQL

        rows = sync_fetch_all(
            "SELECT area_level, min(transaction_count) AS smallest "
            "FROM area_stats GROUP BY 1"
        )
        for row in rows:
            expected = TIER_SQL.get(row["area_level"], (None, None, 1))[2]
            assert row["smallest"] >= expected, (
                f"{row['area_level']} published a median from "
                f"{row['smallest']} sales, below its {expected} threshold"
            )

    def test_no_area_stat_is_derived_from_non_evidence_transactions(self):
        """Medians must exclude category B and non-residential sales."""
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            """
            SELECT count(*) AS n FROM area_stats a
            WHERE a.area_level = 'sector' AND a.segment = 'all'
              AND a.transaction_count > (
                  SELECT count(*) FROM transactions t
                  WHERE t.country_iso2 = a.country_iso2
                    AND t.sector = a.area_code
                    AND extract(year FROM t.transaction_date) = a.year
                    AND t.market_value_basis = 'STANDARD'
                    AND t.is_residential
              )
            """
        )
        assert row["n"] == 0, "an area median counted ineligible transactions"

    def test_ingestion_runs_are_recorded(self):
        from app.db import sync_fetch_one

        row = sync_fetch_one(
            "SELECT count(*) AS n FROM ingestion_runs WHERE status = 'complete'"
        )
        assert row["n"] > 0, "no completed ingestion runs recorded"
