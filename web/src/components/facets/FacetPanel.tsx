"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import type { FacetOut } from "@/lib/api";
import {
  activeValues, clearFacet, setRange, toggleFilter, type SearchState,
} from "@/lib/url";
import { CheckboxFacet } from "./CheckboxFacet";
import { RangeFacet } from "./RangeFacet";

/** The pill's own short summary of its current value, e.g. "Apple" or
 * "Apple +2" for a checkbox facet, "100-2000 MVR" for a range. Falls back to
 * the raw value only when a checkbox option's label cannot be found (a stale
 * URL filter for a value no longer in this result set). */
function summarize(facet: FacetOut, values: string[]): string | null {
  if (!values.length) return null;

  if (facet.widget === "range") {
    const [lo, hi] = (values[0] ?? "").split("..");
    const unit = facet.unit ? ` ${facet.unit}` : "";
    if (lo && hi) return `${lo}-${hi}${unit}`;
    if (lo) return `${lo}+${unit}`;
    if (hi) return `<${hi}${unit}`;
    return null;
  }

  const labelFor = (v: string) => facet.values.find((x) => x.value === v)?.label ?? v;
  return values.length === 1
    ? labelFor(values[0])
    : `${labelFor(values[0])} +${values.length - 1}`;
}

/**
 * Renders `facets` in the order the API gave them, as a row of pills rather
 * than a stacked sidebar. For shopping that order is computed per query by
 * P7's discovery pass and is meaningful, so this component must never sort,
 * group or re-prioritize.
 *
 * A toggle facet is a direct on/off pill -- there is nothing to pick, so a
 * dropdown would just be a second click for the same result. Checkbox and
 * range facets open a small anchored panel, styled like SearchBox's own
 * suggestions list rather than a new pattern.
 */
export function FacetPanel({
  facets, state, onChange,
}: {
  facets: FacetOut[];
  state: SearchState;
  onChange: (next: SearchState) => void;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  if (!facets.length) return null;

  return (
    <div role="group" aria-label="Filters" className="flex flex-wrap gap-2">
      {facets.map((facet) => {
        const values = activeValues(state, facet.key);
        const active = values.length > 0;

        if (facet.widget === "toggle") {
          return (
            <button
              key={facet.key}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(toggleFilter(state, facet.key, "true"))}
              className={`btn btn-sm rounded-full gap-1.5 ${
                active ? "btn-primary" : "btn-outline"
              }`}
            >
              {facet.label}
              {facet.count_true != null && (
                <span className="opacity-70">{facet.count_true}</span>
              )}
            </button>
          );
        }

        const isOpen = openKey === facet.key;
        const summary = summarize(facet, values);

        return (
          <div
            key={facet.key}
            className="relative"
            onBlur={(e) => {
              // Only close once focus actually leaves the pill+panel as a
              // unit -- ticking a second checkbox inside the panel must not
              // close it after the first.
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
                setOpenKey((k) => (k === facet.key ? null : k));
              }
            }}
          >
            <div
              className={`inline-flex items-center rounded-full text-sm ${
                active
                  ? "bg-primary text-primary-content"
                  : "border border-base-300 text-base-content"
              }`}
            >
              <button
                type="button"
                aria-expanded={isOpen}
                aria-haspopup="true"
                onClick={() => setOpenKey(isOpen ? null : facet.key)}
                className="flex items-center gap-1.5 py-1.5 ps-3 pe-2"
              >
                <span>
                  {facet.label}
                  {summary && (
                    <>
                      {": "}
                      <Bidi as="span" text={summary} />
                    </>
                  )}
                </span>
                <span aria-hidden="true" className="text-xs opacity-70">
                  {isOpen ? "⌃" : "⌄"}
                </span>
              </button>
              {active && (
                <button
                  type="button"
                  aria-label={`Clear ${facet.label}`}
                  onClick={() => onChange(clearFacet(state, facet.key))}
                  className="me-2 grid h-4 w-4 shrink-0 place-items-center rounded-full
                             text-xs opacity-80 hover:opacity-100"
                >
                  ✕
                </button>
              )}
            </div>

            {isOpen && (
              <div
                className="absolute z-10 mt-1 w-64 rounded-lg border border-base-300
                           bg-base-100 p-3 shadow-lg"
              >
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
                    onApply={(lo, hi) => {
                      onChange(setRange(state, facet.key, lo, hi));
                      setOpenKey(null);
                    }}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
