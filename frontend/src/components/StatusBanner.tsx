"use client";

import type { DataStatus } from "@/types/api";

/**
 * Distinct messaging per state (§36).
 *
 * The whole point of this component is that "no data exists here" and "the
 * server failed" look and read completely differently. Conflating them would
 * teach users to distrust the honest answer.
 */
const META: Record<
  DataStatus,
  { tone: string; icon: "info" | "warn" | "error"; title: string } | null
> = {
  OK: null,
  NO_DATA: {
    tone: "bg-white/97 text-slate-700 ring-black/[0.05]",
    icon: "info",
    title: "No data here",
  },
  UNSUPPORTED_LOCATION: {
    tone: "bg-white/97 text-slate-700 ring-black/[0.05]",
    icon: "info",
    title: "Not yet covered",
  },
  INSUFFICIENT_EVIDENCE: {
    tone: "bg-amber-50/97 text-amber-900 ring-amber-200/70",
    icon: "warn",
    title: "Not enough evidence",
  },
  OUT_OF_RANGE: {
    tone: "bg-amber-50/97 text-amber-900 ring-amber-200/70",
    icon: "warn",
    title: "Outside the available range",
  },
  PROVIDER_ERROR: {
    tone: "bg-rose-50/97 text-rose-900 ring-rose-200/70",
    icon: "error",
    title: "Something went wrong",
  },
};

export default function StatusBanner({
  status,
  message,
  onRetry,
}: {
  status: DataStatus;
  message: string | null;
  onRetry?: () => void;
}) {
  const meta = META[status];
  if (!meta) return null;

  return (
    <div
      role={meta.icon === "error" ? "alert" : "status"}
      className={`rm-animate-in pointer-events-auto flex max-w-[520px] items-start gap-2.5
                  rounded-2xl px-4 py-3 backdrop-blur-xl ring-1 ${meta.tone}`}
      style={{ boxShadow: "var(--shadow-float)" }}
    >
      <Icon kind={meta.icon} />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-bold uppercase tracking-[0.06em] opacity-65">
          {meta.title}
        </p>
        <p className="mt-0.5 text-[13px] leading-snug">
          {message ?? "Property price data is not currently available for this location."}
        </p>
      </div>
      {/* Retry is offered only for genuine failures — retrying a place with no
          data would just produce the same honest answer. */}
      {meta.icon === "error" && onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 rounded-lg bg-rose-900/10 px-2.5 py-1.5 text-[12px] font-semibold
                     text-rose-900 transition hover:bg-rose-900/15"
        >
          Retry
        </button>
      )}
    </div>
  );
}

function Icon({ kind }: { kind: "info" | "warn" | "error" }) {
  if (kind === "error") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"
           className="mt-[3px] shrink-0">
        <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
        <path d="M8 4.6v4.2M8 11.1v.9" stroke="currentColor" strokeWidth="1.7"
              strokeLinecap="round" />
      </svg>
    );
  }
  if (kind === "warn") {
    return (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"
           className="mt-[3px] shrink-0">
        <path d="M8 1.6l6.4 11.2H1.6L8 1.6z" stroke="currentColor" strokeWidth="1.5"
              strokeLinejoin="round" />
        <path d="M8 6v3.1M8 11.1v.7" stroke="currentColor" strokeWidth="1.6"
              strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"
         className="mt-[3px] shrink-0">
      <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8 7v4.4M8 4.7v.9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}
