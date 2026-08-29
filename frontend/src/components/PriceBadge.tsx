"use client";

import type { Confidence, PrecisionLevel, PriceType } from "@/types/api";

/**
 * The badges that keep the price categories apart (§2, §21).
 *
 * This component is the single place the mapping from price type to label and
 * colour lives, so no view can accidentally render a forecast to look like a
 * recorded sale.
 */

const PRICE_TYPE_META: Record<
  PriceType,
  { label: string; classes: string; description: string }
> = {
  TRANSACTION: {
    label: "Sold",
    classes: "bg-slate-900 text-white ring-slate-900",
    description: "A genuine sale price recorded by the land registry.",
  },
  CURRENT_ESTIMATE: {
    label: "Estimate",
    classes: "bg-blue-600 text-white ring-blue-600",
    description: "A modelled current value, not a recorded sale.",
  },
  HISTORICAL_ESTIMATE: {
    label: "Historical estimate",
    classes: "bg-violet-600 text-white ring-violet-600",
    description:
      "A modelled value for a past date. The property did not sell then.",
  },
  FORECAST: {
    label: "Forecast",
    classes: "bg-orange-600 text-white ring-orange-600",
    description: "A statistical projection, not a value that has occurred.",
  },
  REGIONAL_STATISTIC: {
    label: "Area statistic",
    classes: "bg-teal-700 text-white ring-teal-700",
    description:
      "A median across an area — not the value of any individual property.",
  },
};

const PRECISION_META: Record<PrecisionLevel, { label: string; note: string }> = {
  EXACT_TRANSACTION: {
    label: "Exact transaction",
    note: "A specific recorded sale of this property.",
  },
  PROPERTY_ESTIMATE: {
    label: "Property level",
    note: "Modelled for this individual dwelling.",
  },
  STREET_POSTCODE: {
    label: "Street / postcode level",
    note: "Typical for this street or postcode, not this dwelling.",
  },
  NEIGHBOURHOOD: {
    label: "Neighbourhood level",
    note: "Typical for this neighbourhood.",
  },
  CITY_REGIONAL: {
    label: "City / regional level",
    note: "A broad market statistic covering many neighbourhoods.",
  },
  NONE: { label: "No reliable data", note: "Not enough evidence to publish a figure." },
};

const CONFIDENCE_META: Record<Confidence, { label: string; classes: string; note: string }> = {
  HIGH: {
    label: "High",
    classes: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    note: "Many recent, closely-comparable sales nearby.",
  },
  MEDIUM: {
    label: "Medium",
    classes: "bg-amber-50 text-amber-800 ring-amber-200",
    note: "Reasonable nearby evidence, with some uncertainty.",
  },
  LOW: {
    label: "Low",
    classes: "bg-orange-50 text-orange-800 ring-orange-200",
    note: "Sparse, older or weakly-comparable evidence.",
  },
  VERY_LOW: {
    label: "Very low",
    classes: "bg-rose-50 text-rose-800 ring-rose-200",
    note: "Too weak to rely on for an individual property.",
  },
};

export function PriceTypeBadge({ type, size = "md" }: { type: PriceType; size?: "sm" | "md" }) {
  const meta = PRICE_TYPE_META[type];
  return (
    <span
      title={meta.description}
      className={`inline-flex shrink-0 items-center rounded-md font-bold uppercase tracking-[0.06em]
                  ring-1 ${meta.classes} ${
                    size === "sm" ? "px-1.5 py-[2px] text-[9px]" : "px-2 py-[3px] text-[10px]"
                  }`}
    >
      {meta.label}
    </span>
  );
}

export function ConfidenceBadge({ level }: { level: Confidence }) {
  const meta = CONFIDENCE_META[level];
  return (
    <span
      title={meta.note}
      className={`inline-flex items-center gap-1 rounded-md px-2 py-[3px] text-[10px] font-bold
                  uppercase tracking-[0.06em] ring-1 ${meta.classes}`}
    >
      {meta.label} confidence
    </span>
  );
}

export function PrecisionBadge({ level }: { level: PrecisionLevel }) {
  const meta = PRECISION_META[level];
  return (
    <span
      title={meta.note}
      className="inline-flex items-center rounded-md bg-slate-100 px-2 py-[3px] text-[10px]
                 font-semibold uppercase tracking-[0.05em] text-slate-600 ring-1 ring-slate-200"
    >
      {meta.label}
    </span>
  );
}

export function priceTypeColour(type: PriceType): string {
  return {
    TRANSACTION: "var(--color-sold)",
    CURRENT_ESTIMATE: "var(--color-estimate)",
    HISTORICAL_ESTIMATE: "var(--color-historical)",
    FORECAST: "var(--color-forecast)",
    REGIONAL_STATISTIC: "var(--color-regional)",
  }[type];
}

export function priceTypeHeading(type: PriceType, year: number): string {
  switch (type) {
    case "TRANSACTION":
      return "Sold for";
    case "CURRENT_ESTIMATE":
      return "Estimated current value";
    case "HISTORICAL_ESTIMATE":
      return `Estimated value in ${year}`;
    case "FORECAST":
      return `${year} forecast`;
    case "REGIONAL_STATISTIC":
      return "Area median";
  }
}

export { PRICE_TYPE_META, PRECISION_META, CONFIDENCE_META };
