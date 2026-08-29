"use client";

import { useMemo, useState } from "react";

import { formatCompact, formatFull } from "@/lib/currency";
import type { PriceHistory, PricePoint, PriceType } from "@/types/api";

/**
 * Property price history (§18).
 *
 * The central design rule: observed sales, modelled estimates and forecasts are
 * drawn as three visually distinct series. A single continuous line through all
 * of them would imply every point is a recorded sale, which is the exact
 * misrepresentation the product must avoid.
 *
 *   observed sale      solid segment, filled diamond
 *   modelled estimate  dashed segment, hollow circle
 *   forecast           dotted segment, hollow square, on a tinted field,
 *                      with the prediction interval drawn as a shaded band
 */

const W = 520;
const H = 200;
const PAD = { top: 18, right: 16, bottom: 26, left: 46 };

type Kind = "observed" | "estimate" | "forecast";

function kindOf(type: PriceType): Kind {
  if (type === "TRANSACTION") return "observed";
  if (type === "FORECAST") return "forecast";
  return "estimate";
}

export default function PriceChart({
  history,
  selectedYear,
  onSelectYear,
}: {
  history: PriceHistory;
  selectedYear: number;
  onSelectYear?: (year: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const model = useMemo(() => {
    const points = [...history.points].sort((a, b) => a.year - b.year);
    if (points.length === 0) return null;

    const years = points.map((p) => p.year);
    const minYear = Math.min(...years);
    const maxYear = Math.max(...years);

    // The band must be included in the y-domain, or a wide forecast interval
    // would be clipped and look narrower than it is.
    const lows = points.map((p) => p.price.lower_bound ?? p.price.value);
    const highs = points.map((p) => p.price.upper_bound ?? p.price.value);
    const minValue = Math.min(...lows);
    const maxValue = Math.max(...highs);
    const span = maxValue - minValue || maxValue || 1;
    const yMin = Math.max(0, minValue - span * 0.12);
    const yMax = maxValue + span * 0.12;

    const x = (year: number) =>
      PAD.left +
      ((year - minYear) / Math.max(1, maxYear - minYear)) * (W - PAD.left - PAD.right);
    const y = (value: number) =>
      PAD.top + (1 - (value - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);

    const firstForecast = points.find((p) => kindOf(p.price.price_type) === "forecast");

    return { points, minYear, maxYear, yMin, yMax, x, y, firstForecast };
  }, [history]);

  if (!model) {
    return (
      <p className="rounded-xl bg-slate-50 px-3 py-6 text-center text-[13px] text-slate-500">
        {history.message ?? "No price history available for this property."}
      </p>
    );
  }

  const { points, x, y, yMin, yMax, firstForecast } = model;

  /* Build one path per contiguous run of the same kind, so the stroke style
     changes exactly where the nature of the data changes. */
  const segments: { kind: Kind; d: string }[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    const kind: Kind =
      kindOf(a.price.price_type) === kindOf(b.price.price_type)
        ? kindOf(a.price.price_type)
        : // A boundary segment takes the weaker of the two kinds, so a line
          // into a forecast is never drawn as though it were observed.
          kindOf(b.price.price_type) === "forecast"
          ? "forecast"
          : "estimate";
    segments.push({
      kind,
      d: `M${x(a.year)},${y(a.price.value)} L${x(b.year)},${y(b.price.value)}`,
    });
  }

  /* Uncertainty band, drawn only across points that actually have one. */
  const banded = points.filter((p) => p.price.lower_bound != null && p.price.upper_bound != null);
  const bandPath =
    banded.length > 1
      ? `M${banded.map((p) => `${x(p.year)},${y(p.price.upper_bound!)}`).join(" L")} ` +
        `L${[...banded].reverse().map((p) => `${x(p.year)},${y(p.price.lower_bound!)}`).join(" L")} Z`
      : null;

  const ticks = [yMin, (yMin + yMax) / 2, yMax];
  const active = hover ?? selectedYear;
  const activePoint = points.find((p) => p.year === active);
  const currency = history.currency;

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full touch-none select-none"
        role="img"
        aria-label={`Price history from ${model.minYear} to ${model.maxYear}`}
      >
        {/* forecast field */}
        {firstForecast && (
          <rect
            x={x(firstForecast.year)}
            y={PAD.top - 6}
            width={W - PAD.right - x(firstForecast.year)}
            height={H - PAD.top - PAD.bottom + 12}
            fill="var(--color-forecast)"
            opacity="0.045"
          />
        )}

        {/* gridlines */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line
              x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)}
              stroke="#e2e8f0" strokeWidth="1"
              strokeDasharray={i === 0 ? undefined : "3 3"}
            />
            <text
              x={PAD.left - 7} y={y(t) + 3.5} textAnchor="end"
              className="fill-slate-400 text-[9px] tabular-nums"
            >
              {formatCompact(t, currency)}
            </text>
          </g>
        ))}

        {bandPath && (
          <path d={bandPath} fill="var(--color-forecast)" opacity="0.13" stroke="none" />
        )}

        {segments.map((seg, i) => (
          <path
            key={i}
            d={seg.d}
            fill="none"
            strokeWidth={seg.kind === "observed" ? 2.6 : 2}
            strokeLinecap="round"
            stroke={
              seg.kind === "observed"
                ? "var(--color-sold)"
                : seg.kind === "forecast"
                  ? "var(--color-forecast)"
                  : "var(--color-historical)"
            }
            strokeDasharray={
              seg.kind === "observed" ? undefined : seg.kind === "forecast" ? "2 4" : "6 4"
            }
          />
        ))}

        {activePoint && (
          <line
            x1={x(activePoint.year)} x2={x(activePoint.year)}
            y1={PAD.top - 6} y2={H - PAD.bottom}
            stroke="#94a3b8" strokeWidth="1" strokeDasharray="2 3"
          />
        )}

        {points.map((p) => (
          <Marker
            key={p.year}
            point={p}
            cx={x(p.year)}
            cy={y(p.price.value)}
            isActive={p.year === active}
            onEnter={() => setHover(p.year)}
            onLeave={() => setHover(null)}
            onClick={() => onSelectYear?.(p.year)}
          />
        ))}

        {/* x labels: first, last, and the active year */}
        {[points[0], points[points.length - 1]].map((p, i) => (
          <text
            key={`xl-${i}`}
            x={x(p.year)} y={H - 8}
            textAnchor={i === 0 ? "start" : "end"}
            className="fill-slate-400 text-[9.5px] tabular-nums"
          >
            {p.year}
          </text>
        ))}
        {activePoint &&
          activePoint.year !== points[0].year &&
          activePoint.year !== points[points.length - 1].year && (
            <text
              x={x(activePoint.year)} y={H - 8} textAnchor="middle"
              className="fill-slate-700 text-[9.5px] font-bold tabular-nums"
            >
              {activePoint.year}
            </text>
          )}
      </svg>

      {activePoint && (
        <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-1 text-[12px]">
          <span className="font-bold tabular-nums text-slate-900">
            {formatFull(activePoint.price.value, currency)}
          </span>
          <span className="tabular-nums text-slate-500">in {activePoint.year}</span>
          <span
            className="ml-auto font-semibold uppercase tracking-wide"
            style={{ color: kindColour(kindOf(activePoint.price.price_type)) }}
          >
            {activePoint.price.price_type === "TRANSACTION"
              ? "Recorded sale"
              : activePoint.price.price_type === "FORECAST"
                ? "Forecast"
                : "Estimate"}
          </span>
          {activePoint.price.lower_bound != null && (
            <span className="w-full tabular-nums text-slate-400">
              range {formatCompact(activePoint.price.lower_bound, currency)} –{" "}
              {formatCompact(activePoint.price.upper_bound!, currency)}
            </span>
          )}
        </div>
      )}

      <Legend />
    </div>
  );
}

function kindColour(kind: Kind): string {
  return kind === "observed"
    ? "var(--color-sold)"
    : kind === "forecast"
      ? "var(--color-forecast)"
      : "var(--color-historical)";
}

function Marker({
  point,
  cx,
  cy,
  isActive,
  onEnter,
  onLeave,
  onClick,
}: {
  point: PricePoint;
  cx: number;
  cy: number;
  isActive: boolean;
  onEnter: () => void;
  onLeave: () => void;
  onClick: () => void;
}) {
  const kind = kindOf(point.price.price_type);
  const colour = kindColour(kind);
  const r = isActive ? 5.4 : 4.2;

  return (
    <g
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onClick={onClick}
      style={{ cursor: "pointer" }}
    >
      {/* generous invisible hit area */}
      <circle cx={cx} cy={cy} r={11} fill="transparent" />
      {kind === "observed" ? (
        // filled diamond = a real recorded sale
        <rect
          x={cx - r} y={cy - r} width={r * 2} height={r * 2}
          transform={`rotate(45 ${cx} ${cy})`}
          fill={colour} stroke="#fff" strokeWidth="1.6"
        />
      ) : kind === "forecast" ? (
        // hollow square = a projection
        <rect
          x={cx - r} y={cy - r} width={r * 2} height={r * 2}
          fill="#fff" stroke={colour} strokeWidth="2.1" rx="0.8"
        />
      ) : (
        // hollow circle = a modelled estimate
        <circle cx={cx} cy={cy} r={r} fill="#fff" stroke={colour} strokeWidth="2.1" />
      )}
    </g>
  );
}

function Legend() {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3.5 gap-y-1 px-1 text-[10.5px]
                    text-slate-500">
      <span className="inline-flex items-center gap-1.5">
        <svg width="10" height="10" aria-hidden="true">
          <rect x="1.4" y="1.4" width="7" height="7" transform="rotate(45 5 5)"
                fill="var(--color-sold)" />
        </svg>
        Recorded sale
      </span>
      <span className="inline-flex items-center gap-1.5">
        <svg width="10" height="10" aria-hidden="true">
          <circle cx="5" cy="5" r="3.4" fill="#fff" stroke="var(--color-historical)"
                  strokeWidth="2" />
        </svg>
        Estimate
      </span>
      <span className="inline-flex items-center gap-1.5">
        <svg width="10" height="10" aria-hidden="true">
          <rect x="1.4" y="1.4" width="7.2" height="7.2" fill="#fff"
                stroke="var(--color-forecast)" strokeWidth="2" />
        </svg>
        Forecast
      </span>
      <span className="inline-flex items-center gap-1.5">
        <svg width="14" height="8" aria-hidden="true">
          <rect x="0" y="1" width="14" height="6" fill="var(--color-forecast)" opacity="0.18" />
        </svg>
        Likely range
      </span>
    </div>
  );
}
