"""Address normalisation and property identity (§52)."""
from __future__ import annotations

import pytest

from ingest.address import (
    display_address,
    normalise_postcode,
    normalise_street,
    normalise_subunit,
    normalise_token,
    pretty_uk_postcode,
    property_key,
    uk_postcode_parts,
)


class TestPropertyIdentity:
    """The examples named in the specification must resolve to two dwellings —
    the house and flat 2 — not four."""

    HOUSE_VARIANTS = [
        {"postcode": "MK9 2AB", "paon": "12", "saon": None, "street": "High Street"},
        {"postcode": "mk9 2ab", "paon": "12", "saon": "", "street": "HIGH STREET"},
        {"postcode": "MK92AB", "paon": "12", "saon": None, "street": "High St"},
        {"postcode": " MK9  2AB ", "paon": " 12 ", "saon": None, "street": "high st."},
    ]
    FLAT_VARIANTS = [
        {"postcode": "MK9 2AB", "paon": "12", "saon": "Flat 2", "street": "High Street"},
        {"postcode": "MK9 2AB", "paon": "12", "saon": "FLAT 2", "street": "High St"},
        {"postcode": "MK9 2AB", "paon": "12", "saon": "Apartment 2", "street": "High Street"},
        {"postcode": "MK9 2AB", "paon": "12", "saon": "APT. 2", "street": "HIGH ST"},
    ]

    def test_house_variants_collapse_to_one_key(self):
        keys = {property_key(**v) for v in self.HOUSE_VARIANTS}
        assert len(keys) == 1, f"house spellings split into {len(keys)} dwellings"

    def test_flat_variants_collapse_to_one_key(self):
        keys = {property_key(**v) for v in self.FLAT_VARIANTS}
        assert len(keys) == 1, f"flat spellings split into {len(keys)} dwellings"

    def test_house_and_flat_are_different_dwellings(self):
        house = property_key(**self.HOUSE_VARIANTS[0])
        flat = property_key(**self.FLAT_VARIANTS[0])
        assert house != flat

    def test_different_flats_in_same_building_are_distinct(self):
        a = property_key(postcode="MK9 2AB", paon="12", saon="Flat 1", street="High Street")
        b = property_key(postcode="MK9 2AB", paon="12", saon="Flat 2", street="High Street")
        assert a != b

    def test_same_number_different_street_is_distinct(self):
        a = property_key(postcode="MK9 2AB", paon="12", saon=None, street="High Street")
        b = property_key(postcode="MK9 2AB", paon="12", saon=None, street="Low Street")
        assert a != b

    def test_same_address_different_postcode_is_distinct(self):
        a = property_key(postcode="MK9 2AB", paon="12", saon=None, street="High Street")
        b = property_key(postcode="MK9 2AC", paon="12", saon=None, street="High Street")
        assert a != b

    def test_accents_are_folded(self):
        a = property_key(postcode="75020", paon="4", saon=None, street="Rue de l'Île")
        b = property_key(postcode="75020", paon="4", saon=None, street="RUE DE L ILE")
        assert a == b

    def test_key_is_stable_across_calls(self):
        args = {"postcode": "MK9 2AB", "paon": "12", "saon": None, "street": "High Street"}
        assert property_key(**args) == property_key(**args)


class TestStreetNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("High St", "HIGH STREET"),
            ("HIGH ST.", "HIGH STREET"),
            ("Oldbrook Blvd", "OLDBROOK BOULEVARD"),
            ("Century Ave", "CENTURY AVENUE"),
            ("Wardle Pl", "WARDLE PLACE"),
            ("Bridgeford Ct", "BRIDGEFORD COURT"),
            ("Grace Ave", "GRACE AVENUE"),
            ("North Rd", "NORTH ROAD"),
            ("Nth Rd", "NORTH ROAD"),
        ],
    )
    def test_abbreviations_expand(self, raw, expected):
        assert normalise_street(raw) == expected

    def test_empty_is_empty(self):
        assert normalise_street(None) == ""
        assert normalise_street("") == ""


class TestSubunitNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Flat 2", "2"), ("FLAT 2", "2"), ("Apartment 2", "2"),
            ("Apt 2", "2"), ("APT. 2", "2"), ("Unit 2", "2"),
            ("2", "2"), ("Flat 2A", "2A"), ("Maisonette 4", "4"),
            # Nested prefixes must all be stripped.
            ("Flat Apartment 3", "3"),
        ],
    )
    def test_prefixes_stripped(self, raw, expected):
        assert normalise_subunit(raw) == expected


class TestPostcodes:
    @pytest.mark.parametrize(
        "raw,norm,parts",
        [
            ("MK9 2AB", "MK92AB", ("MK", "MK9", "MK9 2")),
            ("mk9 2ab", "MK92AB", ("MK", "MK9", "MK9 2")),
            ("SW1A 1AA", "SW1A1AA", ("SW", "SW1A", "SW1A 1")),
            ("M1 1AE", "M11AE", ("M", "M1", "M1 1")),
            ("B33 8TH", "B338TH", ("B", "B33", "B33 8")),
        ],
    )
    def test_normalise_and_split(self, raw, norm, parts):
        assert normalise_postcode(raw) == norm
        assert uk_postcode_parts(norm) == parts

    def test_non_uk_postcode_yields_no_parts_rather_than_guessing(self):
        # A French postcode must not be forced into a UK outcode/sector.
        assert uk_postcode_parts(normalise_postcode("75020")) == ("", "", "")
        assert uk_postcode_parts("") == ("", "", "")
        assert uk_postcode_parts("ABC") == ("", "", "")

    def test_pretty_round_trip(self):
        assert pretty_uk_postcode("MK92AB") == "MK9 2AB"


class TestDisplayAddress:
    def test_house(self):
        assert display_address(None, "12", "HIGH STREET") == "12 High Street"

    def test_flat(self):
        assert display_address("FLAT 2", "12", "HIGH STREET") == "Flat 2, 12 High Street"

    def test_missing_parts(self):
        assert display_address(None, None, None) is None
        assert normalise_token(None) == ""
