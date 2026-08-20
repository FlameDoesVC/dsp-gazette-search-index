"use client";

import type { FacetOut } from "@/lib/api";
import {
  activeValues, clearFacet, setRange, toggleFilter, type SearchState,
} from "@/lib/url";
import { CheckboxFacet } from "./CheckboxFacet";
import { RangeFacet } from "./RangeFacet";
import { ToggleFacet } from "./ToggleFacet";

/**
 * Renders `facets` in the order the API gave them. For shopping that order is
 * computed per query by P7's discovery pass and is meaningful, so this
 * component must never sort, group or re-prioritize.
 */
export function FacetPanel({
  facets, state, onChange,
}: {
  facets: FacetOut[];
  state: SearchState;
  onChange: (next: SearchState) => void;
}) {
  if (!facets.length) return null;

  return (
    <aside className="space-y-4">
      {facets.map((facet) => {
        const isActive = activeValues(state, facet.key).length > 0;
        return (
          <section key={facet.key} className="border-b border-base-300 pb-3 last:border-0">
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{facet.label}</h3>
              {isActive && (
                <button
                  type="button"
                  className="text-xs text-primary underline"
                  onClick={() => onChange(clearFacet(state, facet.key))}
                >
                  Clear {facet.label}
                </button>
              )}
            </div>

            {facet.widget === "checkbox" && (
              <CheckboxFacet
                facet={facet}
                state={state}
                onToggle={(v) => onChange(toggleFilter(state, facet.key, v))}
              />
            )}
            {facet.widget === "range" && (
              <RangeFacet
                facet={facet}
                state={state}
                onApply={(lo, hi) => onChange(setRange(state, facet.key, lo, hi))}
              />
            )}
            {facet.widget === "toggle" && (
              <ToggleFacet
                facet={facet}
                state={state}
                onToggle={() => onChange(toggleFilter(state, facet.key, "true"))}
              />
            )}
          </section>
        );
      })}
    </aside>
  );
}
