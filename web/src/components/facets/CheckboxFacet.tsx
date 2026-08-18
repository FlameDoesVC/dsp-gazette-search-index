"use client";

import { Bidi } from "@/components/Bidi";
import type { FacetOut } from "@/lib/api";
import { activeValues, type SearchState } from "@/lib/url";

export function CheckboxFacet({
  facet, state, onToggle,
}: {
  facet: FacetOut;
  state: SearchState;
  onToggle: (value: string) => void;
}) {
  const active = new Set(activeValues(state, facet.key));
  return (
    <ul className="space-y-1">
      {facet.values.map((v) => (
        <li key={v.value} className="flex items-center justify-between gap-2">
          <label className="flex min-w-0 items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={active.has(v.value)}
              onChange={() => onToggle(v.value)}
            />
            <Bidi as="span" text={v.label} className="truncate" />
          </label>
          <span className="text-xs text-muted">{v.count}</span>
        </li>
      ))}
    </ul>
  );
}
