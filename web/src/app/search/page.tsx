import { SearchShell } from "@/components/SearchShell";
import { getSearch } from "@/lib/api";
import { parseSearchParams } from "@/lib/url";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const raw = await searchParams;
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(raw)) {
    for (const item of Array.isArray(v) ? v : v ? [v] : []) sp.append(k, item);
  }
  const state = parseSearchParams(sp);

  // Server-rendered for first paint and SEO (spec 10). Facet interaction from
  // here on is client-side, through the URL.
  const data = await getSearch(state);
  return <SearchShell state={state} data={data} />;
}
