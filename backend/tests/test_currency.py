"""Currency formatting (§24). Nothing here converts — only formats."""
from __future__ import annotations

import pytest

from app.core.currency import (
    COUNTRY_CURRENCY,
    SYMBOLS,
    currency_for_country,
    format_compact,
    format_full,
    symbol,
)


class TestCompact:
    @pytest.mark.parametrize(
        "value,currency,expected",
        [
            # The exact examples given in the specification.
            (725_000, "GBP", "£725k"),
            (1_200_000, "GBP", "£1.2m"),
            (875_000, "USD", "$875k"),
            (650_000, "EUR", "€650k"),
            (430_000, "EUR", "€430k"),
            (18_000_000, "INR", "₹1.8 Cr"),
            (9_500_000, "INR", "₹95 L"),
            (135_100, "GBP", "£135k"),
            (1_000_000, "GBP", "£1m"),
            (999, "GBP", "£999"),
            (2_500_000_000, "GBP", "£2.5bn"),
        ],
    )
    def test_examples(self, value, currency, expected):
        assert format_compact(value, currency) == expected

    def test_trailing_zero_decimal_is_dropped(self):
        assert format_compact(2_000_000, "GBP") == "£2m"
        assert format_compact(2_100_000, "GBP") == "£2.1m"

    def test_unknown_currency_falls_back_to_the_code(self):
        # Must not silently pretend an unknown currency is dollars.
        out = format_compact(500_000, "XYZ")
        assert "XYZ" in out and "$" not in out


class TestFull:
    def test_thousands_separators(self):
        assert format_full(425_000, "GBP") == "£425,000"
        assert format_full(1_234_567, "EUR") == "€1,234,567"

    def test_rounds_to_whole_units(self):
        assert format_full(425_000.49, "GBP") == "£425,000"


class TestCountryCurrency:
    @pytest.mark.parametrize(
        "iso2,expected",
        [("GB", "GBP"), ("FR", "EUR"), ("US", "USD"), ("IN", "INR"), ("JP", "JPY")],
    )
    def test_mapping(self, iso2, expected):
        assert currency_for_country(iso2) == expected

    def test_unknown_country_returns_none_rather_than_a_default(self):
        assert currency_for_country("ZZ") is None
        assert currency_for_country(None) is None

    def test_case_insensitive(self):
        assert currency_for_country("gb") == "GBP"

    def test_every_mapped_currency_has_a_deliberate_symbol(self):
        """Each supported currency needs an explicit SYMBOLS entry rather than
        falling through to the generic "CODE " default.

        Some entries legitimately *are* the code or a short form (CHF, kr, zl,
        Kc), so this checks for a deliberate entry, not for a glyph.
        """
        for code in sorted(set(COUNTRY_CURRENCY.values())):
            assert code in SYMBOLS, f"{code} has no explicit SYMBOLS entry"
            assert symbol(code), f"{code} formats to an empty symbol"
