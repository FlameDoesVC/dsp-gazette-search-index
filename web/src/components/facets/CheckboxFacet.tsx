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
          <label className="label min-w-0 gap-2">
            <input
              type="checkbox"
              checked={active.has(v.value)}
              onChange={() => onToggle(v.value)}
              className="checkbox checkbox-sm"
            />
            <Bidi as="span" text={v.label} className="truncate" />
          </label>
          <span className="text-xs text-base-content/60">{v.count}</span>
        </li>
      ))}
    </ul>
  );
}
