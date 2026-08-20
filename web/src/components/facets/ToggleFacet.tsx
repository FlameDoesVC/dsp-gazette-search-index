"use client";

import type { FacetOut } from "@/lib/api";
import { activeValues, type SearchState } from "@/lib/url";

export function ToggleFacet({
  facet, state, onToggle,
}: {
  facet: FacetOut;
  state: SearchState;
  onToggle: () => void;
}) {
  const on = activeValues(state, facet.key)[0] === "true";
  return (
    <label className="flex items-center justify-between gap-2 text-sm">
      <span className="flex items-center gap-2">
        <input type="checkbox" checked={on} onChange={onToggle} className="toggle toggle-sm" />
        {facet.label}
      </span>
      <span className="text-xs text-base-content/60">{facet.count_true}</span>
    </label>
  );
}
