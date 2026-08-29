"use client";

import { useEffect, useRef, useState } from "react";

import { AbortedError, ApiError, api } from "@/lib/api";
import type { SearchResult } from "@/types/api";

interface Props {
  onSelect: (result: SearchResult) => void;
}

const KIND_LABEL: Record<string, string> = {
  country: "Country", state: "State", region: "Region", county: "County",
  city: "City", town: "Town", village: "Village", suburb: "Suburb",
  neighbourhood: "Neighbourhood", postcode: "Postcode", street: "Street",
  address: "Address", building: "Building", place: "Place",
};

export default function SearchBar({ onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  /* Debounced autocomplete. The 320 ms delay is not cosmetic: the geocoder is
     rate-limited upstream, so we must not fire a request per keystroke. */
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const found = await api.search(q, 8);
        setResults(found);
        setError(found.length === 0 ? "No matching places found." : null);
        setHighlight(found.length ? 0 : -1);
        setOpen(true);
      } catch (err) {
        if (err instanceof AbortedError) return;
        setResults([]);
        setError(
          err instanceof ApiError
            ? err.message
            : "Location search is unavailable right now.",
        );
      } finally {
        setLoading(false);
      }
    }, 320);
    return () => clearTimeout(timer);
  }, [query]);

  /* Dismiss on outside click. */
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  /* `/` focuses search, Escape closes it. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
      if (e.key === "/" && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const choose = (result: SearchResult) => {
    onSelect(result);
    setQuery(result.name);
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h - 1 + results.length) % results.length);
    } else if (e.key === "Enter" && highlight >= 0) {
      e.preventDefault();
      choose(results[highlight]);
    }
  };

  return (
    <div ref={rootRef} className="pointer-events-auto w-full max-w-[420px]">
      <div
        className="flex items-center gap-2.5 rounded-full bg-white/97 px-4 py-3 backdrop-blur-xl
                   ring-1 ring-black/[0.04] transition-shadow focus-within:ring-black/[0.09]"
        style={{ boxShadow: "var(--shadow-float)" }}
      >
        <SearchIcon />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Enter an area name, postcode or address"
          aria-label="Search for a location"
          aria-autocomplete="list"
          aria-expanded={open}
          role="combobox"
          aria-controls="rm-search-results"
          className="min-w-0 flex-1 bg-transparent text-[15px] text-slate-900
                     placeholder:text-slate-400 focus:outline-none"
          autoComplete="off"
          spellCheck={false}
        />
        {loading && <Spinner />}
        {!loading && query && (
          <button
            onClick={() => {
              setQuery("");
              setResults([]);
              inputRef.current?.focus();
            }}
            aria-label="Clear search"
            className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-slate-400
                       transition hover:bg-slate-100 hover:text-slate-600"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M1 1l10 10M11 1L1 11" stroke="currentColor" strokeWidth="1.7"
                    strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>

      {open && (results.length > 0 || error) && (
        <div
          id="rm-search-results"
          role="listbox"
          className="rm-animate-in rm-scroll mt-2 max-h-[min(420px,60vh)] overflow-y-auto
                     rounded-2xl bg-white/97 p-1.5 backdrop-blur-xl ring-1 ring-black/[0.05]"
          style={{ boxShadow: "var(--shadow-float-lg)" }}
        >
          {error && results.length === 0 && (
            <p className="px-3 py-3 text-[13px] text-slate-500">{error}</p>
          )}
          {results.map((result, i) => (
            <button
              key={result.id + i}
              role="option"
              aria-selected={i === highlight}
              onMouseEnter={() => setHighlight(i)}
              onClick={() => choose(result)}
              className={`flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition
                          ${i === highlight ? "bg-slate-100/90" : "hover:bg-slate-50"}`}
            >
              <PlaceIcon kind={result.kind} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14px] font-semibold text-slate-900">
                  {result.name}
                </span>
                <span className="block truncate text-[12px] text-slate-500">
                  {result.display_name}
                </span>
              </span>
              <span className="mt-0.5 flex shrink-0 flex-col items-end gap-1">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                  {KIND_LABEL[result.kind] ?? result.kind}
                </span>
                {/* Coverage is surfaced in the results themselves, so the user
                    knows before moving the map whether prices exist there. */}
                {result.has_property_data ? (
                  <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9.5px] font-bold
                                   uppercase tracking-wide text-emerald-700">
                    Price data
                  </span>
                ) : (
                  <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[9.5px] font-bold
                                   uppercase tracking-wide text-slate-500">
                    No data
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SearchIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 18 18" fill="none" aria-hidden="true"
         className="shrink-0 text-slate-500">
      <circle cx="7.6" cy="7.6" r="5.4" stroke="currentColor" strokeWidth="1.8" />
      <path d="M11.7 11.7L16 16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function Spinner() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" className="shrink-0 animate-spin text-slate-400"
         aria-hidden="true">
      <circle cx="8" cy="8" r="6.2" stroke="currentColor" strokeWidth="2" opacity="0.22" fill="none" />
      <path d="M14.2 8A6.2 6.2 0 008 1.8" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" fill="none" />
    </svg>
  );
}

function PlaceIcon({ kind }: { kind: string }) {
  const isArea = ["country", "state", "region", "county", "city", "town"].includes(kind);
  return (
    <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100
                     text-slate-500">
      {isArea ? (
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="6.4" stroke="currentColor" strokeWidth="1.5" />
          <path d="M1.6 8h12.8M8 1.6c1.8 1.8 2.7 4 2.7 6.4S9.8 12.6 8 14.4C6.2 12.6 5.3 10.4 5.3 8S6.2 3.4 8 1.6z"
                stroke="currentColor" strokeWidth="1.3" />
        </svg>
      ) : (
        <svg width="13" height="15" viewBox="0 0 14 16" fill="none" aria-hidden="true">
          <path d="M7 15s5.4-6 5.4-9.4A5.4 5.4 0 001.6 5.6C1.6 9 7 15 7 15z"
                stroke="currentColor" strokeWidth="1.5" />
          <circle cx="7" cy="5.7" r="2" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      )}
    </span>
  );
}
