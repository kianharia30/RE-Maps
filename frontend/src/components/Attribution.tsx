"use client";

import { useState } from "react";

/**
 * Attribution bar. Every licence in play requires its statement to be
 * displayed wherever the data is used, so this is a compliance requirement,
 * not decoration (§44).
 */
export default function Attribution({ attributions }: { attributions: string[] }) {
  const [open, setOpen] = useState(false);

  const base = [
    "Base map © OpenFreeMap, © OpenMapTiles, Data © OpenStreetMap contributors",
  ];
  const all = [...new Set([...attributions, ...base])];

  return (
    <div className="pointer-events-auto flex justify-end">
      {open ? (
        <div
          className="rm-animate-in max-w-[560px] rounded-xl bg-white/97 px-3 py-2.5 text-[10.5px]
                     leading-relaxed text-slate-500 backdrop-blur-xl ring-1 ring-black/[0.05]"
          style={{ boxShadow: "var(--shadow-float)" }}
        >
          <button
            onClick={() => setOpen(false)}
            className="float-right ml-2 text-slate-400 hover:text-slate-700"
            aria-label="Hide attribution"
          >
            ×
          </button>
          {all.map((a, i) => (
            <p key={i}>{a}</p>
          ))}
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="rounded-lg bg-white/90 px-2 py-1 text-[10px] font-medium text-slate-500
                     backdrop-blur-xl ring-1 ring-black/[0.04] transition hover:bg-white
                     hover:text-slate-700"
        >
          © Data sources
        </button>
      )}
    </div>
  );
}
