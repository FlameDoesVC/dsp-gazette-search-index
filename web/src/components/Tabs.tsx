"use client";

import { useMeta } from "./MetaProvider";
import { changeTab, toSearchParams, type SearchState } from "@/lib/url";

export function Tabs({
  state, onChange,
}: {
  state: SearchState;
  onChange: (next: SearchState) => void;
}) {
  const meta = useMeta();
  if (!meta) return null;

  return (
    <nav className="flex gap-1 border-b border-line" role="tablist">
      {meta.tabs.map((t) => {
        const selected = state.type === t.key;
        return (
          <a
            key={t.key}
            role="tab"
            aria-selected={selected}
            href={`/search?${toSearchParams(changeTab(state, t.key)).toString()}`}
            onClick={(e) => {
              e.preventDefault();
              onChange(changeTab(state, t.key));
            }}
            className={
              "px-3 py-2 text-sm " +
              (selected
                ? "border-b-2 border-accent font-semibold"
                : "text-muted hover:text-fg")
            }
          >
            {t.label_en}
          </a>
        );
      })}
    </nav>
  );
}
