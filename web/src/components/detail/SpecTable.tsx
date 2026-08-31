"use client";

import { Bidi } from "@/components/Bidi";

interface SpecRow {
  key_raw: string;
  value_num?: number | null;
  value_text?: string;
  unit?: string;
}

/**
 * The full spec table, including non-facetable keys (spec 8.3). The facet
 * panel is the filter; this is the detail, and dropping keys that never made
 * it to facetable would quietly erase data.
 */
export function SpecTable({
  specs, headingLevel: Heading = "h2",
}: {
  specs: SpecRow[];
  /** See CompensationTable's headingLevel: h2 on the detail page (under its
   * h1), h3 inside a result card (whose own title is already an h3). */
  headingLevel?: "h2" | "h3";
}) {
  if (!specs.length) return null;
  return (
    <section className="space-y-2">
      <Heading className="text-sm font-semibold">Specifications</Heading>
      <dl className="divide-y divide-base-300 text-sm">
        {specs.map((s, i) => (
          <div key={i} className="flex justify-between gap-4 py-1.5">
            <dt className="text-base-content/60">{s.key_raw}</dt>
            <dd className="text-end">
              <Bidi as="span" text={s.value_text ?? String(s.value_num ?? "")} />
              {s.unit ? <span className="ms-0.5 text-xs text-base-content/60">{s.unit}</span> : null}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
