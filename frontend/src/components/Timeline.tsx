"use client";

import { useEffect, useMemo, useRef } from "react";

/**
 * The floating timeline (§11).
 *
 * Years are never hard-coded: the range comes from the coverage response, which
 * derives it from what is actually in the database plus the forecast horizon the
 * backend will actually honour. A jurisdiction with no forecast support simply
 * has no future years to select.
 *
 * Past / present / future are visually distinct, because selecting 2022 and
 * selecting 2030 return fundamentally different kinds of number.
 */
interface Props {
  minYear: number;
  maxYear: number;
  currentYear: number;
  maxDataYear: number;
  value: number;
  onChange: (year: number) => void;
  disabled?: boolean;
}

const WINDOW = 7; // years visible at once, as in the reference

export default function Timeline({
  minYear,
  maxYear,
  currentYear,
  maxDataYear,
  value,
  onChange,
  disabled = false,
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);

  const years = useMemo(() => {
    const out: number[] = [];
    for (let y = minYear; y <= maxYear; y++) out.push(y);
    return out;
  }, [minYear, maxYear]);

  /* Keep the selected year scrolled into view when it changes from elsewhere
     (URL, keyboard, the arrows). */
  useEffect(() => {
    const node = trackRef.current?.querySelector<HTMLElement>(`[data-year="${value}"]`);
    node?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }, [value]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      if (e.key === "ArrowLeft" && value > minYear) onChange(value - 1);
      if (e.key === "ArrowRight" && value < maxYear) onChange(value + 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [value, minYear, maxYear, onChange]);

  const step = (delta: number) => {
    const next = Math.min(maxYear, Math.max(minYear, value + delta));
    if (next !== value) onChange(next);
  };

  const label =
    value > currentYear ? "Forecast" : value < currentYear ? "Historical" : "Current";
  const labelTone =
    value > currentYear
      ? "text-orange-700 bg-orange-50 ring-orange-200/70"
      : value < currentYear
        ? "text-violet-700 bg-violet-50 ring-violet-200/70"
        : "text-emerald-700 bg-emerald-50 ring-emerald-200/70";

  return (
    <div
      className="pointer-events-auto rounded-[26px] bg-white/97 px-2.5 py-2 backdrop-blur-xl
                 ring-1 ring-black/[0.04]"
      style={{ boxShadow: "var(--shadow-float)" }}
      role="group"
      aria-label="Select a year"
    >
      <div className="flex items-center gap-1">
        <Arrow
          direction="left"
          disabled={disabled || value <= minYear}
          onClick={() => step(-1)}
        />

        <div
          ref={trackRef}
          className="rm-scroll flex items-stretch gap-0 overflow-x-auto scroll-smooth px-1"
          style={{
            maxWidth: `min(calc(100vw - 220px), ${WINDOW * 84}px)`,
            scrollbarWidth: "none",
          }}
        >
          {years.map((year, i) => {
            const selected = year === value;
            const isFuture = year > currentYear;
            const isPresent = year === currentYear;
            // A year past the last full year of data but not yet a forecast:
            // the source data for it is still incomplete.
            const partial = year === maxDataYear && year === currentYear;

            return (
              <button
                key={year}
                data-year={year}
                onClick={() => !disabled && onChange(year)}
                disabled={disabled}
                aria-pressed={selected}
                aria-label={`${year}${isFuture ? " (forecast)" : isPresent ? " (current)" : " (historical)"}`}
                className="group relative flex w-[84px] shrink-0 flex-col items-center
                           disabled:opacity-40"
              >
                {/* Connector: drawn as two half-segments so the first and last
                    dots have no dangling line, matching the reference. */}
                <span className="pointer-events-none absolute top-[13px] left-0 right-0 flex">
                  <span
                    className={`h-[2.5px] flex-1 ${i === 0 ? "bg-transparent" : isFuture ? "bg-orange-300" : "bg-slate-900/85"}`}
                  />
                  <span
                    className={`h-[2.5px] flex-1 ${
                      i === years.length - 1
                        ? "bg-transparent"
                        : years[i + 1] > currentYear
                          ? "bg-orange-300"
                          : "bg-slate-900/85"
                    }`}
                  />
                </span>

                <span
                  className={`relative z-10 grid place-items-center rounded-full transition-all
                              duration-200 ${selected ? "h-[26px] w-[26px]" : "h-[19px] w-[19px]"}`}
                >
                  <Dot selected={selected} isFuture={isFuture} isPresent={isPresent} partial={partial} />
                </span>

                <span
                  className={`mt-1.5 text-[13px] tabular-nums transition-colors ${
                    selected
                      ? "font-bold text-slate-900"
                      : isFuture
                        ? "font-medium text-orange-700/75 group-hover:text-orange-800"
                        : "font-medium text-slate-500 group-hover:text-slate-800"
                  }`}
                >
                  {year}
                </span>
              </button>
            );
          })}
        </div>

        <Arrow
          direction="right"
          disabled={disabled || value >= maxYear}
          onClick={() => step(1)}
        />
      </div>

      <div className="mt-0.5 flex justify-center">
        <span
          className={`rounded-full px-2.5 py-[3px] text-[10px] font-bold uppercase
                      tracking-[0.07em] ring-1 ${labelTone}`}
        >
          {label}
        </span>
      </div>
    </div>
  );
}

function Dot({
  selected,
  isFuture,
  isPresent,
  partial,
}: {
  selected: boolean;
  isFuture: boolean;
  isPresent: boolean;
  partial: boolean;
}) {
  if (selected) {
    return (
      <span
        className={`grid h-[26px] w-[26px] place-items-center rounded-full ring-[3px] ring-white
                    ${isFuture ? "bg-orange-600" : "bg-slate-900"}`}
      >
        <span className="h-[7px] w-[7px] rounded-full bg-white" />
      </span>
    );
  }
  if (isFuture) {
    // Hollow ring: a forecast year holds no observation.
    return (
      <span className="h-[15px] w-[15px] rounded-full border-[2.5px] border-orange-400 bg-white" />
    );
  }
  if (partial) {
    // Half-filled: the current year's source data is still incomplete.
    return (
      <span className="relative h-[15px] w-[15px] overflow-hidden rounded-full
                       border-[2.5px] border-slate-900 bg-white">
        <span className="absolute inset-y-0 left-0 w-1/2 bg-slate-900" />
      </span>
    );
  }
  return (
    <span
      className={`rounded-full bg-slate-900 ${isPresent ? "h-[16px] w-[16px] ring-2 ring-slate-900/20" : "h-[14px] w-[14px]"}`}
    />
  );
}

function Arrow({
  direction,
  disabled,
  onClick,
}: {
  direction: "left" | "right";
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={direction === "left" ? "Previous year" : "Next year"}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-800 transition
                 hover:bg-slate-100 disabled:opacity-25 disabled:hover:bg-transparent"
    >
      <svg width="19" height="13" viewBox="0 0 20 14" fill="none" aria-hidden="true"
           style={direction === "right" ? { transform: "scaleX(-1)" } : undefined}>
        <path d="M19 7H2M7.5 1.5L1.6 7l5.9 5.5" stroke="currentColor" strokeWidth="1.9"
              strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}
