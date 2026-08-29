/**
 * Currency formatting (§24).
 *
 * Mirrors backend/app/core/currency.py. Prices are ALWAYS shown in the
 * currency they were recorded in; nothing is converted, because converting a
 * 2021 French sale into pounds at today's rate would produce a number that
 * never existed.
 */

const SYMBOLS: Record<string, string> = {
  GBP: "£", EUR: "€", USD: "$", AUD: "A$", CAD: "C$", NZD: "NZ$",
  INR: "₹", JPY: "¥", CHF: "CHF ", SEK: "kr ", NOK: "kr ", DKK: "kr ",
  ZAR: "R", BRL: "R$", SGD: "S$", HKD: "HK$", PLN: "zł ", CZK: "Kč ",
};

export function symbol(currency: string): string {
  return SYMBOLS[currency?.toUpperCase()] ?? `${currency?.toUpperCase() ?? ""} `;
}

function trim(value: number): string {
  const s = value.toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

/** `£425,000` — the exact figure, for headline values. */
export function formatFull(value: number, currency: string): string {
  return `${symbol(currency)}${Math.round(value).toLocaleString("en-GB")}`;
}

/** `£725k`, `£1.2m`, `₹1.8 Cr` — for map markers. */
export function formatCompact(value: number, currency: string): string {
  const cur = currency?.toUpperCase() ?? "";
  const sym = symbol(cur);
  const v = Math.abs(value);

  if (cur === "INR") {
    // Indian numbering: 1 crore = 10,000,000; 1 lakh = 100,000.
    if (v >= 1e7) return `${sym}${trim(v / 1e7)} Cr`;
    if (v >= 1e5) return `${sym}${trim(v / 1e5)} L`;
    return `${sym}${Math.round(v).toLocaleString("en-IN")}`;
  }
  if (cur === "JPY") {
    if (v >= 1e8) return `${sym}${trim(v / 1e8)}億`;
    if (v >= 1e4) return `${sym}${trim(v / 1e4)}万`;
    return `${sym}${Math.round(v).toLocaleString()}`;
  }
  if (v >= 1e9) return `${sym}${trim(v / 1e9)}bn`;
  if (v >= 1e6) return `${sym}${trim(v / 1e6)}m`;
  if (v >= 1e3) return `${sym}${Math.round(v / 1e3)}k`;
  return `${sym}${Math.round(v).toLocaleString()}`;
}

/** `£438,000 – £487,000` */
export function formatRange(
  low: number | null,
  high: number | null,
  currency: string,
): string | null {
  if (low == null || high == null) return null;
  return `${formatFull(low, currency)} – ${formatFull(high, currency)}`;
}

export function formatArea(sqm: number | null): string | null {
  if (sqm == null) return null;
  return `${Math.round(sqm)} m²`;
}

export function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

export function formatMonthYear(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
}
