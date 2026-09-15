"use client";

import { useState } from "react";

import type { MapTier } from "@/types/api";

export type Segment = "all" | "detached" | "semi_detached" | "terraced" | "flat" | "house";

const SEGMENT_LABEL: Record<Segment, string> = {
  all: "All types",
  detached: "Detached",
  semi_detached: "Semi-detached",
  terraced: "Terraced",
  flat: "Flats",
  house: "Houses",
};


const TIER_LABEL: Record<MapTier, string> = {
  WORLD: "World", COUNTRY: "Country", REGION: "Region", CITY: "City",
  NEIGHBOURHOOD: "Neighbourhood", POSTCODE: "Postcode", STREET: "Street",
  PROPERTY: "Individual properties",
};

export default function MapControls({
  segment,
  onSegmentChange,
  tier,
  loading,
  resultCount,
  truncated,
}: {
  segment: Segment;
  onSegmentChange: (s: Segment) => void;
  tier: MapTier | null;
  loading: boolean;
  resultCount: number;
  truncated: boolean;
}) {
  const [openFilters, setOpenFilters] = useState(false);

  return (
    <div className="pointer-events-auto flex flex-col items-end gap-2">
      {/* property type filter */}
      <div className="relative">
        <button
          onClick={() => setOpenFilters((v) => !v)}
          aria-expanded={openFilters}
          className="flex items-center gap-2 rounded-full bg-white/97 px-3.5 py-2 text-[12.5px]
                     font-semibold text-slate-700 backdrop-blur-xl ring-1 ring-black/[0.04]
                     transition hover:bg-white"
          style={{ boxShadow: "var(--shadow-float)" }}
        >
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M1 3h12M3 7h8M5 11h4" stroke="currentColor" strokeWidth="1.7"
                  strokeLinecap="round" />
          </svg>
          {SEGMENT_LABEL[segment]}
        </button>
        {openFilters && (
          <div
            className="rm-animate-in absolute right-0 top-full mt-1.5 w-[176px] rounded-2xl
                       bg-white/97 p-1.5 backdrop-blur-xl ring-1 ring-black/[0.05]"
            style={{ boxShadow: "var(--shadow-float-lg)" }}
          >
            {(Object.keys(SEGMENT_LABEL) as Segment[]).map((s) => (
              <button
                key={s}
                onClick={() => {
                  onSegmentChange(s);
                  setOpenFilters(false);
                }}
                className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left
                            text-[13px] transition ${
                              segment === s
                                ? "bg-slate-100 font-semibold text-slate-900"
                                : "text-slate-600 hover:bg-slate-50"
                            }`}
              >
                {SEGMENT_LABEL[s]}
                {segment === s && (
                  <svg width="11" height="9" viewBox="0 0 12 10" fill="none" aria-hidden="true"
                       className="ml-auto">
                    <path d="M1 5l3.6 3.6L11 1.6" stroke="currentColor" strokeWidth="2"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* what the map is currently showing */}
      {tier && (
        <div
          className="flex items-center gap-2 rounded-full bg-white/95 px-3 py-1.5 text-[11px]
                     font-medium text-slate-500 backdrop-blur-xl ring-1 ring-black/[0.04]"
          style={{ boxShadow: "var(--shadow-float)" }}
        >
          {loading ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400" />
              Loading…
            </span>
          ) : (
            <>
              <span className="font-semibold text-slate-700">{TIER_LABEL[tier]}</span>
              <span className="tabular-nums">
                {resultCount}
                {truncated ? "+" : ""} shown
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
