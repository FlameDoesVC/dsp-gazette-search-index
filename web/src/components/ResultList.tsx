"use client";

import { ResultCard } from "@/components/cards/ResultCard";
import { postClick, type ResultOut } from "@/lib/api";

export function ResultList({
  results, queryId, tab = "all",
}: {
  results: ResultOut[];
  queryId: number | null;
  tab?: string;
}) {
  if (!results.length) {
    return (
      <p className="py-12 text-center text-sm text-base-content/60">
        No results. Try fewer words, or check the spelling.
      </p>
    );
  }

  const grid = tab === "shopping" || tab === "images";

  return (
    <div
      data-testid="results"
      className={
        grid
          ? "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
          : "space-y-3"
      }
    >
      {results.map((r, i) => (
        <div
          key={r.id}
          // Capture phase, so the click is recorded even when the card's own
          // anchor navigates away immediately. postClick uses sendBeacon.
          onClickCapture={() => {
            if (queryId != null) {
              void postClick({ query_id: queryId, document_id: r.id, position: i });
            }
          }}
        >
          <ResultCard result={r} />
        </div>
      ))}
    </div>
  );
}
