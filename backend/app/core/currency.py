"""Currency formatting (§24).

Transactions are always stored and displayed in their ORIGINAL currency; this
module only formats, it never converts. Indian-style lakh/crore grouping is
applied for INR because that is how prices are actually quoted there.
"""
from __future__ import annotations

SYMBOLS: dict[str, str] = {
    "GBP": "£", "EUR": "€", "USD": "$", "AUD": "A$", "CAD": "C$",
    "NZD": "NZ$", "INR": "₹", "JPY": "¥", "CHF": "CHF ", "SEK": "kr ",
    "NOK": "kr ", "DKK": "kr ", "ZAR": "R", "BRL": "R$", "SGD": "S$",
    "HKD": "HK$", "PLN": "zł ", "CZK": "Kč ", "HUF": "Ft ", "RON": "lei ",
    "ISK": "kr ", "TRY": "₺", "RSD": "din ", "UAH": "₴", "KRW": "₩",
    "CNY": "¥", "MXN": "MX$", "AED": "AED ", "ILS": "₪",
}

# ISO 3166-1 alpha-2 -> ISO 4217. Used when a provider does not state one.
#
# Completeness matters here: an incomplete map used to fall through to a
# hard-coded "EUR", which labelled Hungarian figures in euros. A missing entry
# now yields None and the currency is simply not shown, rather than asserting a
# currency the country does not use.
COUNTRY_CURRENCY: dict[str, str] = {
    # --- euro area ---------------------------------------------------------
    "AT": "EUR", "BE": "EUR", "CY": "EUR", "DE": "EUR", "EE": "EUR",
    "ES": "EUR", "FI": "EUR", "FR": "EUR", "GR": "EUR", "HR": "EUR",
    "IE": "EUR", "IT": "EUR", "LT": "EUR", "LU": "EUR", "LV": "EUR",
    "MT": "EUR", "NL": "EUR", "PT": "EUR", "SI": "EUR", "SK": "EUR",
    # Bulgaria adopted the euro on 1 January 2026. Index series published
    # before then were denominated in leva; since an index has no price level
    # this affects labelling only.
    "BG": "EUR",
    # --- rest of Europe ----------------------------------------------------
    "GB": "GBP", "CH": "CHF", "CZ": "CZK", "DK": "DKK", "HU": "HUF",
    "IS": "ISK", "NO": "NOK", "PL": "PLN", "RO": "RON", "SE": "SEK",
    "TR": "TRY", "RS": "RSD", "UA": "UAH",
    # --- rest of world -----------------------------------------------------
    "US": "USD", "CA": "CAD", "AU": "AUD", "NZ": "NZD", "IN": "INR",
    "JP": "JPY", "ZA": "ZAR", "BR": "BRL", "SG": "SGD", "HK": "HKD",
    "KR": "KRW", "CN": "CNY", "MX": "MXN", "AE": "AED", "IL": "ILS",
}


def symbol(currency: str) -> str:
    return SYMBOLS.get(currency.upper(), currency.upper() + " ")


def currency_for_country(iso2: str | None) -> str | None:
    if not iso2:
        return None
    return COUNTRY_CURRENCY.get(iso2.upper())


def format_full(value: float, currency: str) -> str:
    """`£425,000` — the exact figure, for headline values."""
    return f"{symbol(currency)}{round(value):,}"


def format_compact(value: float, currency: str) -> str:
    """`£725k`, `£1.2m`, `₹1.8 Cr` — for map markers (§8, §24)."""
    cur = currency.upper()
    sym = symbol(cur)
    v = float(value)

    if cur == "INR":
        # Indian numbering: 1 crore = 10,000,000; 1 lakh = 100,000.
        if v >= 10_000_000:
            return f"{sym}{_trim(v / 10_000_000)} Cr"
        if v >= 100_000:
            return f"{sym}{_trim(v / 100_000)} L"
        return f"{sym}{round(v):,}"

    if cur == "JPY":
        if v >= 100_000_000:
            return f"{sym}{_trim(v / 100_000_000)}億"
        if v >= 10_000:
            return f"{sym}{_trim(v / 10_000)}万"
        return f"{sym}{round(v):,}"

    if v >= 1_000_000_000:
        return f"{sym}{_trim(v / 1_000_000_000)}bn"
    if v >= 1_000_000:
        return f"{sym}{_trim(v / 1_000_000)}m"
    if v >= 1_000:
        return f"{sym}{round(v / 1_000)}k"
    return f"{sym}{round(v):,}"


def _trim(x: float) -> str:
    """One decimal place, but drop a trailing `.0`."""
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s
