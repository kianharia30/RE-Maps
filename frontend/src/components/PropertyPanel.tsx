"use client";

import { useEffect, useState } from "react";

import { AbortedError, ApiError, api } from "@/lib/api";
import {
  formatArea,
  formatDate,
  formatFull,
  formatMonthYear,
  formatRange,
} from "@/lib/currency";
import type { PriceHistory, PropertyDetail } from "@/types/api";
import {
  ConfidenceBadge,
  PrecisionBadge,
  PriceTypeBadge,
  priceTypeHeading,
} from "./PriceBadge";
import PriceChart from "./PriceChart";

/**
 * The property details panel (§17).
 *
 * Becomes a bottom sheet under 768 px (§35). Every figure here is accompanied by
 * what it is, how confident we are, and where it came from.
 */
export default function PropertyPanel({
  propertyId,
  year,
  onClose,
  onSelectYear,
}: {
  propertyId: number;
  year: number;
  onClose: () => void;
  onSelectYear: (year: number) => void;
}) {
  const [detail, setDetail] = useState<PropertyDetail | null>(null);
  const [history, setHistory] = useState<PriceHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSources, setShowSources] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const d = await api.property(propertyId, year);
        if (cancelled) return;
        setDetail(d);
        setLoading(false);
        // History is secondary: loaded after the panel has something to show,
        // so the headline figure is never gated on it.
        const h = await api.history(propertyId).catch(() => null);
        if (cancelled) return;
        if (h) setHistory(h);
      } catch (err) {
        if (cancelled || err instanceof AbortedError) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "We couldn't load this property right now.",
        );
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [propertyId, year]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const price = detail?.selected_year_price ?? null;
  const currency = price?.currency ?? detail?.current_estimate?.currency ?? "GBP";

  return (
    <aside
      className="pointer-events-auto flex max-h-full w-full flex-col overflow-hidden
                 rounded-t-3xl bg-white ring-1 ring-black/[0.05]
                 md:h-full md:max-h-none md:w-[428px] md:rounded-3xl
                 rm-sheet-in md:rm-panel-in"
      style={{ boxShadow: "var(--shadow-float-lg)" }}
      aria-label="Property details"
    >
      {/* --- header ---------------------------------------------------------- */}
      <header className="flex items-start gap-3 border-b border-slate-100 px-5 pt-4 pb-3.5">
        <div className="min-w-0 flex-1">
          {loading && !detail ? (
            <div className="h-5 w-2/3 animate-pulse rounded bg-slate-100" />
          ) : (
            <>
              <h2 className="truncate text-[17px] font-bold leading-tight text-slate-900">
                {detail?.address.line ?? "Property"}
              </h2>
              <p className="mt-0.5 truncate text-[13px] text-slate-500">
                {[detail?.address.town, detail?.address.postcode]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close property details"
          className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400
                     transition hover:bg-slate-100 hover:text-slate-700"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="1.8"
                  strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="rm-scroll flex-1 overflow-y-auto overscroll-contain px-5 pb-6">
        {error && <ErrorState message={error} />}

        {loading && !detail && <SkeletonBody />}

        {detail && (
          <>
            {/* --- the selected year's figure ------------------------------- */}
            <section className="pt-4">
              {price ? (
                <>
                  <div className="flex items-center gap-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.06em]
                                  text-slate-400">
                      {priceTypeHeading(price.price_type, detail.selected_year)}
                    </p>
                    <PriceTypeBadge type={price.price_type} />
                  </div>
                  <p className="mt-1 text-[34px] font-extrabold leading-none tracking-[-0.02em]
                                tabular-nums text-slate-900">
                    {formatFull(price.value, price.currency)}
                  </p>

                  {price.price_type === "TRANSACTION" && (
                    <p className="mt-1.5 text-[13px] text-slate-600">
                      {formatDate(price.date)}
                    </p>
                  )}

                  {formatRange(price.lower_bound, price.upper_bound, price.currency) && (
                    <div className="mt-2.5">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.06em]
                                    text-slate-400">
                        {price.price_type === "FORECAST" ? "Expected range" : "Likely range"}
                      </p>
                      <p className="text-[14px] font-semibold tabular-nums text-slate-700">
                        {formatRange(price.lower_bound, price.upper_bound, price.currency)}
                      </p>
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {price.confidence && <ConfidenceBadge level={price.confidence} />}
                    <PrecisionBadge level={price.precision_level} />
                  </div>
                </>
              ) : (
                <NoValueState
                  status={detail.selected_year_status}
                  message={detail.selected_year_message}
                  year={detail.selected_year}
                />
              )}
            </section>

            {/* --- current estimate + last sale ----------------------------- */}
            <section className="mt-4 grid grid-cols-2 gap-2.5">
              <StatCard
                label="Estimated current value"
                value={
                  detail.current_estimate
                    ? formatFull(detail.current_estimate.value, detail.current_estimate.currency)
                    : null
                }
                sub={
                  detail.current_estimate
                    ? formatRange(
                        detail.current_estimate.lower_bound,
                        detail.current_estimate.upper_bound,
                        detail.current_estimate.currency,
                      )
                    : "Not enough local evidence"
                }
                tone="estimate"
              />
              <StatCard
                label="Last sold"
                value={
                  detail.last_transaction
                    ? formatFull(detail.last_transaction.price, detail.last_transaction.currency)
                    : null
                }
                sub={
                  detail.last_transaction
                    ? formatDate(detail.last_transaction.date)
                    : "No recorded sale"
                }
                tone="sold"
              />
            </section>

            {/* --- characteristics ------------------------------------------ */}
            <Facts detail={detail} />

            {/* --- position caveat ------------------------------------------ */}
            {detail.position_is_approximate && (
              <p className="mt-3 flex gap-2 rounded-xl bg-amber-50/80 px-3 py-2.5 text-[11.5px]
                            leading-relaxed text-amber-900 ring-1 ring-amber-200/70">
                <InfoIcon />
                <span>
                  The map position is{" "}
                  {detail.coordinate_precision === "POSTCODE"
                    ? "the centre of this postcode, not the building itself"
                    : detail.coordinate_precision === "PARCEL"
                      ? "the cadastral parcel, which for a flat is the building rather than the unit"
                      : "approximate"}
                  . Building-level coordinates are not available in the open data
                  for this jurisdiction.
                </span>
              </p>
            )}

            {/* --- history chart -------------------------------------------- */}
            <section className="mt-5">
              <h3 className="mb-1.5 text-[12px] font-bold uppercase tracking-[0.06em]
                             text-slate-500">
                Price history
              </h3>
              {history ? (
                <PriceChart
                  history={history}
                  selectedYear={detail.selected_year}
                  onSelectYear={onSelectYear}
                />
              ) : (
                <div className="h-[190px] animate-pulse rounded-xl bg-slate-50" />
              )}
            </section>

            {/* --- sources -------------------------------------------------- */}
            <Disclosure
              open={showSources}
              onToggle={() => setShowSources((v) => !v)}
              title={`Data sources (${detail.sources.length})`}
            >
              <ul className="space-y-2.5">
                {detail.sources.map((s) => (
                  <li key={s.key} className="text-[12px] leading-relaxed">
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="font-semibold text-blue-700 hover:underline"
                    >
                      {s.name}
                    </a>
                    <p className="text-slate-500">{s.owner}</p>
                    <p className="text-slate-500">
                      Licence:{" "}
                      {s.licence_url ? (
                        <a href={s.licence_url} target="_blank" rel="noreferrer noopener"
                           className="hover:underline">
                          {s.licence}
                        </a>
                      ) : (
                        s.licence
                      )}
                    </p>
                    {s.source_published_at && (
                      <p className="text-slate-500">
                        Source published: {formatMonthYear(s.source_published_at)}
                      </p>
                    )}
                    {s.last_ingested_at && (
                      <p className="text-slate-500">
                        Loaded into RE-Maps: {formatDate(s.last_ingested_at)}
                      </p>
                    )}
                    <p className="mt-0.5 text-[11px] text-slate-400">{s.attribution}</p>
                  </li>
                ))}
              </ul>
            </Disclosure>
          </>
        )}
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------------- */

function Facts({ detail }: { detail: PropertyDetail }) {
  const c = detail.characteristics;
  const ppsm =
    detail.current_estimate && c.floor_area_sqm
      ? formatFull(detail.current_estimate.value / c.floor_area_sqm, detail.current_estimate.currency)
      : null;

  const facts: [string, string | null][] = [
    ["Type", c.property_type ? prettyType(c.property_type) : null],
    ["Tenure", c.tenure && c.tenure !== "unknown" ? capitalise(c.tenure) : null],
    ["Bedrooms", c.bedrooms != null ? String(c.bedrooms) : null],
    ["Bathrooms", c.bathrooms != null ? String(c.bathrooms) : null],
    ["Rooms", c.habitable_rooms != null ? String(c.habitable_rooms) : null],
    ["Floor area", formatArea(c.floor_area_sqm)],
    ["Plot", formatArea(c.lot_area_sqm)],
    ["Built", c.year_built != null ? String(c.year_built) : null],
    ["Price per m²", ppsm],
  ];
  const present = facts.filter(([, v]) => v != null);
  const missing = facts.filter(([, v]) => v == null).map(([k]) => k);

  return (
    <section className="mt-4">
      <dl className="grid grid-cols-3 gap-x-3 gap-y-2.5 rounded-xl bg-slate-50/80 px-3.5 py-3">
        {present.map(([k, v]) => (
          <div key={k}>
            <dt className="text-[10px] font-semibold uppercase tracking-[0.05em] text-slate-400">
              {k}
            </dt>
            <dd className="text-[13px] font-semibold text-slate-800">{v}</dd>
          </div>
        ))}
      </dl>
      {/* Absent fields are named rather than hidden: the user should know what
          the source data does not contain, not assume we chose not to show it. */}
      {missing.length > 0 && (
        <p className="mt-1.5 px-1 text-[11px] leading-relaxed text-slate-400">
          Not available in the open data for this property: {missing.join(", ").toLowerCase()}.
        </p>
      )}
    </section>
  );
}


/** Renders the model's explainability payload as readable rows (§38). */

function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string | null;
  sub: string | null;
  tone: "estimate" | "sold";
}) {
  return (
    <div className="rounded-xl px-3 py-2.5 ring-1 ring-slate-100">
      <p className="text-[10px] font-semibold uppercase tracking-[0.05em] text-slate-400">
        {label}
      </p>
      {value ? (
        <>
          <p
            className="mt-0.5 text-[17px] font-bold leading-tight tabular-nums"
            style={{
              color: tone === "estimate" ? "var(--color-estimate)" : "var(--color-sold)",
            }}
          >
            {value}
          </p>
          {sub && <p className="mt-0.5 text-[11px] tabular-nums text-slate-500">{sub}</p>}
        </>
      ) : (
        <p className="mt-1 text-[12px] text-slate-400">{sub}</p>
      )}
    </div>
  );
}

function NoValueState({
  status,
  message,
  year,
}: {
  status: string;
  message: string | null;
  year: number;
}) {
  const isFailure = status === "PROVIDER_ERROR";
  return (
    <div
      className={`rounded-xl px-3.5 py-3.5 ring-1 ${
        isFailure
          ? "bg-rose-50/80 text-rose-900 ring-rose-200/70"
          : "bg-slate-50 text-slate-700 ring-slate-200/70"
      }`}
    >
      <p className="text-[12px] font-bold uppercase tracking-[0.06em] opacity-70">
        {isFailure ? "Could not load" : `No value for ${year}`}
      </p>
      <p className="mt-1 text-[13px] leading-relaxed">
        {message ??
          "There is not enough reliable evidence to publish a value for this year."}
      </p>
    </div>
  );
}

function Disclosure({
  open,
  onToggle,
  title,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-4 overflow-hidden rounded-xl ring-1 ring-slate-100">
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition
                   hover:bg-slate-50"
      >
        <span className="flex-1 text-[12px] font-bold uppercase tracking-[0.06em] text-slate-500">
          {title}
        </span>
        <svg
          width="11" height="7" viewBox="0 0 12 8" fill="none" aria-hidden="true"
          className={`shrink-0 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="M1 1l5 5 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                strokeLinejoin="round" />
        </svg>
      </button>
      {open && <div className="border-t border-slate-100 px-3.5 py-3">{children}</div>}
    </section>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="mt-4 rounded-xl bg-rose-50/80 px-3.5 py-3.5 text-rose-900 ring-1
                    ring-rose-200/70">
      <p className="text-[12px] font-bold uppercase tracking-[0.06em] opacity-70">
        Could not load
      </p>
      <p className="mt-1 text-[13px] leading-relaxed">{message}</p>
    </div>
  );
}

function SkeletonBody() {
  return (
    <div className="space-y-3 pt-4">
      <div className="h-3 w-24 animate-pulse rounded bg-slate-100" />
      <div className="h-9 w-44 animate-pulse rounded bg-slate-100" />
      <div className="h-16 animate-pulse rounded-xl bg-slate-50" />
      <div className="h-20 animate-pulse rounded-xl bg-slate-50" />
      <div className="h-[190px] animate-pulse rounded-xl bg-slate-50" />
    </div>
  );
}

function InfoIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true"
         className="mt-[2px] shrink-0">
      <circle cx="7" cy="7" r="6.1" stroke="currentColor" strokeWidth="1.3" />
      <path d="M7 6.1v4M7 4.1v.9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function prettyType(t: string): string {
  return { semi_detached: "Semi-detached", detached: "Detached", terraced: "Terraced",
    flat: "Flat", house: "House", other: "Other" }[t] ?? capitalise(t);
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");
}
