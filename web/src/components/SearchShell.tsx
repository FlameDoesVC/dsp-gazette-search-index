"use client";

import { useRouter } from "next/navigation";
import { FacetPanel } from "@/components/facets/FacetPanel";
import { ResultList } from "@/components/ResultList";
import { SearchBox } from "@/components/SearchBox";
import { Tabs } from "@/components/Tabs";
import type { SearchOut } from "@/lib/api";
import { toSearchParams, type SearchState } from "@/lib/url";

export function SearchShell({
  state, data,
}: {
  state: SearchState;
  data: SearchOut;
}) {
  const router = useRouter();
  const go = (next: SearchState) =>
    router.push(`/search?${toSearchParams(next).toString()}`);

  return (
    <div className="mx-auto max-w-6xl px-4 py-4">
      <SearchBox initial={state.q} onSubmit={(q) => go({ ...state, q, page: 1 })} />
      <div className="mt-3">
        <Tabs state={state} onChange={go} />
      </div>

      <p className="mt-3 text-xs text-base-content/60">
        {data.total.toLocaleString()} results
      </p>

      <div className="mt-3 grid gap-6 lg:grid-cols-[220px_1fr]">
        <FacetPanel facets={data.facets} state={state} onChange={go} />
        <div>
          <ResultList results={data.results} queryId={data.query_id}
                      tab={state.type} />
          {data.total > state.per_page && (
            <nav className="mt-6 flex items-center justify-center gap-2 text-sm">
              <button
                type="button"
                disabled={state.page <= 1}
                onClick={() => go({ ...state, page: state.page - 1 })}
                className="btn btn-sm"
              >
                Previous
              </button>
              <span className="text-base-content/60">Page {state.page}</span>
              <button
                type="button"
                disabled={state.page * state.per_page >= data.total}
                onClick={() => go({ ...state, page: state.page + 1 })}
                className="btn btn-sm"
              >
                Next
              </button>
            </nav>
          )}
        </div>
      </div>
    </div>
  );
}
