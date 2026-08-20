"use client";

import { useState } from "react";
import type { FacetOut } from "@/lib/api";
import { activeValues, type SearchState } from "@/lib/url";

export function RangeFacet({
  facet, state, onApply,
}: {
  facet: FacetOut;
  state: SearchState;
  onApply: (lo: number | null, hi: number | null) => void;
}) {
  const current = activeValues(state, facet.key)[0] ?? "";
  const [lo, hi] = current.split("..");
  const [loV, setLo] = useState(lo ?? "");
  const [hiV, setHi] = useState(hi ?? "");

  const peak = Math.max(1, ...facet.histogram.map((b) => (b as never as { count: number }).count));

  return (
    <div className="space-y-2">
      <div className="flex h-10 items-end gap-0.5" aria-hidden="true">
        {facet.histogram.map((b, i) => {
          const bucket = b as never as { count: number };
          return (
            <span
              key={i}
              data-testid="hist-bar"
              className="flex-1 rounded-t-[2px] bg-base-200"
              style={{ height: `${Math.max(4, (bucket.count / peak) * 100)}%` }}
            />
          );
        })}
      </div>
      <div className="flex items-center gap-1.5 text-sm">
        <input
          type="number"
          inputMode="numeric"
          value={loV}
          placeholder={facet.min != null ? String(Math.floor(facet.min)) : "min"}
          onChange={(e) => setLo(e.target.value)}
          aria-label={`${facet.label} minimum`}
          className="input input-sm w-20"
        />
        <span className="text-base-content/60">-</span>
        <input
          type="number"
          inputMode="numeric"
          value={hiV}
          placeholder={facet.max != null ? String(Math.ceil(facet.max)) : "max"}
          onChange={(e) => setHi(e.target.value)}
          aria-label={`${facet.label} maximum`}
          className="input input-sm w-20"
        />
        {facet.unit ? <span className="text-xs text-base-content/60">{facet.unit}</span> : null}
        <button
          type="button"
          className="btn btn-sm"
          onClick={() =>
            onApply(loV === "" ? null : Number(loV), hiV === "" ? null : Number(hiV))
          }
        >
          Apply
        </button>
      </div>
    </div>
  );
}
