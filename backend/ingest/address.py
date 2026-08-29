"""Address normalisation and property identity (§52).

Transaction datasets do not carry a stable dwelling identifier, so we derive
one. The goal is that all of

    "12 High Street"      /  "12 HIGH STREET"
    "Flat 2, 12 High St"  /  "Apartment 2 12 High Street"

collapse to two distinct dwellings (the house, and flat 2), not four.

The key is deliberately built from *structured* fields (sub-unit, primary unit,
street, postcode) rather than a free-text string, because a hash of free text
would be defeated by any punctuation difference.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Street-type abbreviations seen in HM Land Registry / DVF address fields.
_STREET_ABBREV = {
    "ST": "STREET", "STR": "STREET", "RD": "ROAD", "AVE": "AVENUE",
    "AV": "AVENUE", "LN": "LANE", "DR": "DRIVE", "CT": "COURT",
    "CRT": "COURT", "PL": "PLACE", "SQ": "SQUARE", "CL": "CLOSE",
    "CLS": "CLOSE", "CRES": "CRESCENT", "GDNS": "GARDENS", "GDN": "GARDEN",
    "TER": "TERRACE", "TERR": "TERRACE", "PK": "PARK", "BLVD": "BOULEVARD",
    "HTS": "HEIGHTS", "MT": "MOUNT", "GRN": "GREEN", "GR": "GROVE",
    "WLK": "WALK", "YD": "YARD", "WY": "WAY", "MEWS": "MEWS",
    "N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST",
    "NTH": "NORTH", "STH": "SOUTH",
}

# Sub-unit words that all mean "a dwelling inside a building".
_SUBUNIT_WORDS = re.compile(
    r"^(FLAT|APARTMENT|APT|APPT|UNIT|ROOM|SUITE|MAISONETTE|STUDIO)\b\.?\s*",
    re.IGNORECASE,
)

_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_WS = re.compile(r"\s+")


def _ascii_upper(value: str) -> str:
    """Strip accents and upper-case, so `Île` and `ILE` agree."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.upper()


def normalise_token(value: str | None) -> str:
    if not value:
        return ""
    v = _PUNCT.sub(" ", _ascii_upper(value))
    return _WS.sub(" ", v).strip()


def normalise_street(value: str | None) -> str:
    """Expand street-type abbreviations so `HIGH ST` == `HIGH STREET`."""
    token = normalise_token(value)
    if not token:
        return ""
    words = [_STREET_ABBREV.get(w, w) for w in token.split(" ")]
    return " ".join(words)


def normalise_subunit(value: str | None) -> str:
    """Reduce `Flat 2` / `APARTMENT 2` / `2` to the bare designator `2`."""
    token = normalise_token(value)
    if not token:
        return ""
    prev = None
    while prev != token:
        prev = token
        token = _SUBUNIT_WORDS.sub("", token).strip()
    return token


def normalise_postcode(value: str | None) -> str:
    """UK-style: upper-case, whitespace removed. Safe for other formats too."""
    if not value:
        return ""
    return _WS.sub("", _ascii_upper(value)).replace("-", "")


def uk_postcode_parts(postcode_norm: str) -> tuple[str, str, str]:
    """Split a normalised UK postcode into (area, outcode, sector).

    `MK92AB` -> ("MK", "MK9", "MK9 2"). Returns empty strings if the value does
    not look like a UK postcode, rather than guessing.
    """
    pc = postcode_norm
    if len(pc) < 5:
        return "", "", ""
    incode = pc[-3:]
    outcode = pc[:-3]
    if not incode[0].isdigit() or not outcode:
        return "", "", ""
    area = "".join(ch for ch in outcode[:2] if ch.isalpha())
    if not area:
        return "", "", ""
    return area, outcode, f"{outcode} {incode[0]}"


def pretty_uk_postcode(postcode_norm: str) -> str:
    if len(postcode_norm) < 5:
        return postcode_norm
    return f"{postcode_norm[:-3]} {postcode_norm[-3:]}"


def property_key(
    *,
    postcode: str | None,
    paon: str | None,
    saon: str | None,
    street: str | None,
    town: str | None = None,
    extra: str | None = None,
) -> str:
    """Deterministic dwelling identity.

    Returns a 24-hex-char digest of the normalised structured components. Two
    records produce the same key if and only if they describe the same dwelling
    as far as the available fields can tell.
    """
    parts = [
        normalise_postcode(postcode),
        normalise_token(paon),
        normalise_subunit(saon),
        normalise_street(street),
        # Town only participates when there is no postcode to anchor on,
        # otherwise inconsistent locality spellings would split dwellings.
        normalise_token(town) if not postcode else "",
        normalise_token(extra),
    ]
    joined = "|".join(parts)
    return hashlib.blake2b(joined.encode(), digest_size=12).hexdigest()


def display_address(
    saon: str | None, paon: str | None, street: str | None
) -> str | None:
    """Human-readable single line, e.g. `Flat 2, 12 High Street`."""
    house = " ".join(p for p in (paon, street) if p) or None
    if saon and house:
        return f"{saon.title()}, {house.title()}"
    if saon:
        return saon.title()
    return house.title() if house else None
