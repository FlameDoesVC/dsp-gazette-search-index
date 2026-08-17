# P6 Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Next.js search UI with six tabs, four card types, per-element RTL, working filters, and the job compensation breakdown that is the whole reason this project exists.

**Architecture:** App Router with server components for first paint and SEO; facet interaction is client-side and lives entirely in the URL, so a filtered result page is a shareable link and the back button works. TypeScript types are generated from the API's OpenAPI document, so a rename in `api/schemas.py` is a compile error here rather than a runtime `undefined`. The source registry is fetched once from `/meta` and held for the session; a card never issues its own request for an icon.

**Tech Stack:** Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS 4, `openapi-typescript`, vitest + @testing-library/react, self-hosted Thaana webfont.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 8, 8.1 through 8.5, 9, 10, 11.

**Depends on:** P5 (every endpoint), P4 (card payload shapes).

---

## Global Constraints

- **`dir="rtl"` and `lang="dv"` are applied per element**, never as a page-level flip. Results are frequently mixed-script and a page-level flip is the thing that gets this wrong. Spec 10.
- **Source icons come from `/meta`**, fetched once and held for the session. Icons are self-hosted static assets; nothing hotlinks a third-party favicon, which would put a third-party request on every result row and leak the user's queries by referrer. Spec 4.3.3, 10.
- **The source icon is always paired with its label**, never standing alone as a rebus. 16px in dense contexts, 20px on cards. Placed by logical property (`inline-start`), never by `left`, so it flips with `dir`. Spec 8.5.
- **A listing offering one room of three must never render as a three-bedroom unit.** Render `capacity_display` as given; never reconstruct capacity from `bedrooms`. Spec 8.2.
- **`salary_display` is rendered verbatim.** It is already resolved server-side to one of three strings. The frontend never interprets a null into "Negotiable". Spec 8.1.
- **`net_estimate` is visually subordinate and explicitly approximate**, with its assumptions reachable without leaving the card. When `is_floor` is true it renders as "at least ~", never as a point value. Spec 4.3.2, 8.1.
- **News cards are outbound anchors** with `rel="noopener noreferrer"`. There is no news detail route. Spec 8.4, 8.5.
- **Filter state lives in the URL**, not in component state.
- **The Next.js image optimizer is off.** Product images are third-party URLs on hosts we do not control; running them through the optimizer puts an unbounded transform workload on a 4 GB box. Spec 12.
- **Every click on a result posts to `/events/click` with its position.** Spec 16.3.
- Version control is **jj**, not git.

---

## File Structure

```
web/
  package.json, tsconfig.json, next.config.ts, postcss.config.mjs, vitest.config.ts
  public/fonts/                       self-hosted Thaana woff2
  public/sources/                     source icons, mirrored from Django static
  src/
    app/
      layout.tsx                      html shell, font, MetaProvider
      globals.css                     tokens, Thaana font-face, logical properties
      page.tsx                        home: search box only
      search/page.tsx                 server component, first paint
      documents/[id]/page.tsx         detail, three types
      not-found.tsx, error.tsx
    lib/
      api.ts                          typed fetch wrappers
      types.ts                        GENERATED from openapi.json -- do not edit
      script.ts                       Thaana detection, dir/lang resolution
      compensation.ts                 estimate_net, ported and property-tested
      url.ts                          filter <-> URLSearchParams
      format.ts                       money, dates, relative time
    components/
      MetaProvider.tsx                /meta once per session
      SourceBadge.tsx                 icon + label, logical placement
      Bidi.tsx                        per-element dir/lang
      SearchBox.tsx                   input + suggest
      Tabs.tsx
      ResultList.tsx                  click logging wrapper
      cards/JobCard.tsx
      cards/PropertyCard.tsx
      cards/ShoppingCard.tsx
      cards/NewsCard.tsx
      facets/FacetPanel.tsx
      facets/CheckboxFacet.tsx
      facets/RangeFacet.tsx
      facets/ToggleFacet.tsx
      detail/CompensationTable.tsx    the working-days control
      detail/ApplyBlock.tsx
      detail/Gallery.tsx
      detail/SpecTable.tsx
      ReportDialog.tsx
    test/
      fixtures.ts                     one card payload per type, from real data
```

---

### Task 1: Scaffold, generated types, and the API client

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/next.config.ts`, `web/vitest.config.ts`, `web/src/lib/api.ts`, `web/src/lib/types.ts` (generated), `web/scripts/gen-types.sh`
- Test: `web/src/lib/api.test.ts`

**Interfaces:**
- Produces: `apiFetch<T>(path, init?)`, `getSearch(params)`, `getSuggest(q)`, `getMeta()`, `getDocument(id)`, `postReport(id, body)`, `postClick(body)`; type aliases `SearchOut`, `ResultOut`, `FacetOut`, `MetaOut`, `SourceOut` re-exported from the generated file.

- [ ] **Step 1: Scaffold**

```bash
cd web 2>/dev/null || mkdir web && cd web
npx create-next-app@latest . --typescript --tailwind --app --src-dir \
    --no-eslint --import-alias "@/*" --use-npm
npm i -D vitest @vitejs/plugin-react jsdom @testing-library/react \
    @testing-library/user-event @testing-library/jest-dom openapi-typescript
```

- [ ] **Step 2: Configure Next**

`web/next.config.ts`:

```ts
import type { NextConfig } from "next";

const config: NextConfig = {
  // The prod image copies .next/standalone; without this that directory is
  // never emitted and docker/web.Dockerfile's prod stage fails.
  output: "standalone",
  images: {
    // Product images are third-party URLs on hosts we do not control. Running
    // them through the optimizer puts an unbounded transform workload on a
    // 4 GB box (spec 12.5), so images are plain <img> with loading="lazy".
    unoptimized: true,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_INTERNAL_URL ?? "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default config;
```

The rewrite means the browser only ever talks to its own origin, so there is no CORS configuration to get wrong and no preflight on every search.

- [ ] **Step 3: Generate the types**

`web/scripts/gen-types.sh`:

```bash
#!/usr/bin/env sh
# Regenerate src/lib/types.ts from the running API.
#
# Run after any change to api/schemas.py. A rename there should be a compile
# error here, which is the entire point of generating rather than hand-writing.
set -eu
URL="${1:-http://localhost:8000/api/v1/openapi.json}"
npx openapi-typescript "$URL" -o src/lib/types.ts
echo "regenerated src/lib/types.ts from $URL"
```

Add to `package.json` scripts:

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "test": "vitest run",
    "test:watch": "vitest",
    "gen:types": "sh scripts/gen-types.sh"
  }
}
```

Run it: `npm run gen:types` (Django must be up).

- [ ] **Step 4: Write the failing test**

`web/vitest.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
```

`web/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

`web/src/lib/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildSearchUrl, getSearch, postClick } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("buildSearchUrl", () => {
  it("omits empty parameters instead of sending blanks", () => {
    expect(buildSearchUrl({ q: "phone" })).toBe("/api/v1/search?q=phone");
  });

  it("repeats the f parameter once per filter", () => {
    const url = buildSearchUrl({ q: "phone", f: ["brand:Apple", "brand:Nokia"] });
    expect(url).toContain("f=brand%3AApple");
    expect(url).toContain("f=brand%3ANokia");
  });

  it("encodes a Thaana query", () => {
    expect(buildSearchUrl({ q: "ވަޒީފާ" })).toContain(encodeURIComponent("ވަޒީފާ"));
  });

  it("passes the tab through as `type`", () => {
    expect(buildSearchUrl({ q: "x", type: "job" })).toContain("type=job");
  });
});

describe("getSearch", () => {
  it("throws a typed error on a 400 rather than returning undefined", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "unknown filter 'x'" }), { status: 400 })
    ));
    await expect(getSearch({ q: "x", f: ["x:1"] })).rejects.toThrow("unknown filter");
  });

  it("returns the parsed envelope on 200", async () => {
    const body = { query: { raw: "x" }, total: 0, results: [], facets: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200 })
    ));
    expect((await getSearch({ q: "x" })).total).toBe(0);
  });
});

describe("postClick", () => {
  it("never rejects -- a failed analytics call must not surface to the user", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(postClick({ query_id: 1, document_id: 2, position: 0 }))
      .resolves.toBeUndefined();
  });

  it("uses sendBeacon when available so the request survives navigation", async () => {
    const beacon = vi.fn().mockReturnValue(true);
    vi.stubGlobal("navigator", { sendBeacon: beacon });
    await postClick({ query_id: 1, document_id: 2, position: 3 });
    expect(beacon).toHaveBeenCalled();
  });
});
```

- [ ] **Step 5: Run to confirm failure**

Run: `cd web && npm test`
Expected: FAIL — cannot resolve `./api`.

- [ ] **Step 6: Write the client**

`web/src/lib/api.ts`:

```ts
import type { components } from "./types";

export type SearchOut = components["schemas"]["SearchOut"];
export type ResultOut = components["schemas"]["ResultOut"];
export type FacetOut = components["schemas"]["FacetOut"];
export type MetaOut = components["schemas"]["MetaOut"];
export type SourceOut = components["schemas"]["SourceOut"];

// Server components run inside the container and talk to `api` directly;
// the browser goes through the Next rewrite on its own origin, so there is no
// CORS surface and no preflight on every keystroke.
const BASE =
  typeof window === "undefined"
    ? (process.env.API_INTERNAL_URL ?? "http://localhost:8000")
    : "";

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export interface SearchParams {
  q: string;
  type?: string;
  page?: number;
  per_page?: number;
  sort?: string;
  lang?: string;
  f?: string[];
}

export function buildSearchUrl(p: SearchParams): string {
  const sp = new URLSearchParams();
  sp.set("q", p.q);
  if (p.type && p.type !== "all") sp.set("type", p.type);
  if (p.page && p.page > 1) sp.set("page", String(p.page));
  if (p.per_page) sp.set("per_page", String(p.per_page));
  if (p.sort && p.sort !== "relevance") sp.set("sort", p.sort);
  if (p.lang) sp.set("lang", p.lang);
  for (const f of p.f ?? []) sp.append("f", f);
  return `/api/v1/search?${sp.toString()}`;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const getSearch = (p: SearchParams) =>
  apiFetch<SearchOut>(buildSearchUrl(p), { cache: "no-store" });

export const getSuggest = (q: string) =>
  apiFetch<{ suggestions: { term: string; doc_type: string }[] }>(
    `/api/v1/suggest?q=${encodeURIComponent(q)}`
  );

// /meta changes when an admin edits a Source row, which is rare. An hour of
// staleness is fine and saves a request on every cold server render.
export const getMeta = () =>
  apiFetch<MetaOut>("/api/v1/meta", { next: { revalidate: 3600 } } as RequestInit);

export const getDocument = (id: number) =>
  apiFetch<Record<string, unknown>>(`/api/v1/documents/${id}`);

export const postReport = (id: number, body: { reason: string; note?: string }) =>
  apiFetch<{ status: string }>(`/api/v1/documents/${id}/report`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export async function postClick(body: {
  query_id: number;
  document_id: number;
  position: number;
}): Promise<void> {
  // Analytics must never surface to the user, and it must survive the
  // navigation it is recording -- hence sendBeacon first.
  try {
    const payload = JSON.stringify(body);
    if (typeof navigator !== "undefined" && navigator.sendBeacon) {
      navigator.sendBeacon(
        "/api/v1/events/click",
        new Blob([payload], { type: "application/json" })
      );
      return;
    }
    await fetch("/api/v1/events/click", {
      method: "POST",
      body: payload,
      headers: { "Content-Type": "application/json" },
      keepalive: true,
    });
  } catch {
    /* deliberate: a failed click log is not a user-visible event */
  }
}
```

- [ ] **Step 7: Run the tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
jj commit -m "P6 task 1: Next.js scaffold, generated types, API client"
```

---

### Task 2: Script detection, RTL primitives, and the source badge

**Files:**
- Create: `web/src/lib/script.ts`, `web/src/components/Bidi.tsx`, `web/src/components/MetaProvider.tsx`, `web/src/components/SourceBadge.tsx`, `web/src/app/globals.css`, `web/src/app/layout.tsx`
- Test: `web/src/lib/script.test.ts`, `web/src/components/SourceBadge.test.tsx`, `web/src/components/Bidi.test.tsx`

**Interfaces:**
- Produces: `isThaana(s)`, `scriptOf(s)`, `dirFor(s)`, `langFor(s)`, `<Bidi as="h2" text={...}>`, `<MetaProvider>` + `useMeta()` + `useSource(key)`, `<SourceBadge sourceKey size="sm"|"md" />`.

This is the task the spec calls out as the thing implementations get wrong. A page-level `dir` flip on a mixed-script result set puts English punctuation on the wrong side of Dhivehi sentences and Dhivehi punctuation on the wrong side of English ones, on the same screen.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/script.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { dirFor, isThaana, langFor, scriptOf } from "./script";

describe("isThaana", () => {
  it("detects the Thaana block", () => {
    expect(isThaana("ވަޒީފާގެ ފުރުޞަތު")).toBe(true);
  });
  it("rejects Latin", () => {
    expect(isThaana("Administrative Officer")).toBe(false);
  });
  it("rejects Latin-script Dhivehi", () => {
    // 'kudhin bahattaden' is Dhivehi but written in Latin; it must render LTR
    expect(isThaana("Vazeefaa ah dhaa firihen kudhin bahattaden")).toBe(false);
  });
  it("treats a mixed string containing Thaana as Thaana", () => {
    expect(isThaana("GS3 ގްރޭޑް")).toBe(true);
  });
  it("handles empty and null-ish input", () => {
    expect(isThaana("")).toBe(false);
    expect(isThaana(undefined as unknown as string)).toBe(false);
  });
});

describe("dirFor / langFor", () => {
  it("Thaana is rtl/dv", () => {
    expect(dirFor("ގެޒެޓް")).toBe("rtl");
    expect(langFor("ގެޒެޓް")).toBe("dv");
  });
  it("Latin is ltr/en", () => {
    expect(dirFor("Gazette")).toBe("ltr");
    expect(langFor("Gazette")).toBe("en");
  });
});

describe("scriptOf", () => {
  it("classifies the three cases", () => {
    expect(scriptOf("ހަކަތަ")).toBe("thaana");
    expect(scriptOf("power supply")).toBe("latin");
    expect(scriptOf("")).toBe("unknown");
  });
});
```

`web/src/components/Bidi.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Bidi } from "./Bidi";

describe("Bidi", () => {
  it("marks Thaana content rtl and lang=dv on the element itself", () => {
    render(<Bidi as="h2" text="ވަޒީފާގެ ފުރުޞަތު" data-testid="t" />);
    const el = screen.getByTestId("t");
    expect(el).toHaveAttribute("dir", "rtl");
    expect(el).toHaveAttribute("lang", "dv");
  });

  it("marks Latin content ltr and lang=en", () => {
    render(<Bidi as="h2" text="Administrative Officer" data-testid="t" />);
    expect(screen.getByTestId("t")).toHaveAttribute("dir", "ltr");
  });

  it("two siblings of different scripts each carry their own dir", () => {
    render(
      <div data-testid="row">
        <Bidi as="span" text="Gazette" data-testid="a" />
        <Bidi as="span" text="ގެޒެޓް" data-testid="b" />
      </div>
    );
    expect(screen.getByTestId("a")).toHaveAttribute("dir", "ltr");
    expect(screen.getByTestId("b")).toHaveAttribute("dir", "rtl");
    // The container must NOT have been flipped -- spec 10 forbids page-level
    // and container-level flipping on mixed content.
    expect(screen.getByTestId("row")).not.toHaveAttribute("dir");
  });

  it("renders nothing for empty text rather than an empty element", () => {
    const { container } = render(<Bidi as="p" text="" />);
    expect(container.firstChild).toBeNull();
  });
});
```

`web/src/components/SourceBadge.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetaContext } from "./MetaProvider";
import { SourceBadge } from "./SourceBadge";

const meta = {
  tabs: [],
  doc_types: [],
  sorts: [],
  sources: [
    { key: "gazette", label_en: "Gazette", label_dv: "ގެޒެޓް",
      icon: "/sources/gazette.svg", icon_fallback_text: "ގ", accent: "",
      site_url: "https://gazette.gov.mv" },
    { key: "nofavicon", label_en: "No Favicon", label_dv: "", icon: "",
      icon_fallback_text: "NF", accent: "", site_url: "https://x" },
  ],
};

const wrap = (ui: React.ReactNode) => (
  <MetaContext.Provider value={meta as never}>{ui}</MetaContext.Provider>
);

describe("SourceBadge", () => {
  it("renders the self-hosted icon, never a third-party favicon URL", () => {
    render(wrap(<SourceBadge sourceKey="gazette" />));
    const img = screen.getByRole("img", { hidden: true });
    expect(img.getAttribute("src")).toBe("/sources/gazette.svg");
    expect(img.getAttribute("src")).not.toContain("gazette.gov.mv");
  });

  it("always pairs the icon with a label -- never a bare rebus", () => {
    render(wrap(<SourceBadge sourceKey="gazette" />));
    expect(screen.getByText("Gazette")).toBeInTheDocument();
  });

  it("falls back to a monogram chip when a source has no usable icon", () => {
    render(wrap(<SourceBadge sourceKey="nofavicon" />));
    expect(screen.queryByRole("img", { hidden: true })).toBeNull();
    expect(screen.getByText("NF")).toBeInTheDocument();
  });

  it("renders the Dhivehi label with its own dir when lang is dv", () => {
    render(wrap(<SourceBadge sourceKey="gazette" lang="dv" />));
    expect(screen.getByText("ގެޒެޓް")).toHaveAttribute("dir", "rtl");
  });

  it("renders nothing for an unknown source key rather than a broken chip", () => {
    const { container } = render(wrap(<SourceBadge sourceKey="mystery" />));
    expect(container.firstChild).toBeNull();
  });

  it("uses 16px in dense contexts and 20px on cards", () => {
    const { rerender } = render(wrap(<SourceBadge sourceKey="gazette" size="sm" />));
    expect(screen.getByRole("img", { hidden: true })).toHaveAttribute("width", "16");
    rerender(wrap(<SourceBadge sourceKey="gazette" size="md" />));
    expect(screen.getByRole("img", { hidden: true })).toHaveAttribute("width", "20");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `npm test`
Expected: FAIL — modules do not exist.

- [ ] **Step 3: Write the script helpers**

`web/src/lib/script.ts`:

```ts
/**
 * Thaana detection. Spec 6.1, 10.
 *
 * The Thaana block is U+0780..U+07BF. A string containing any Thaana is
 * rendered RTL: a mixed string like "GS3 ގްރޭޑް" reads right-to-left with the
 * Latin run embedded, which is what the bidi algorithm already does correctly
 * once `dir` is right.
 *
 * Latin-script Dhivehi ("kudhin bahattaden") is deliberately NOT Thaana here.
 * It is Dhivehi in language but Latin in script, and flipping it would be
 * wrong -- direction follows script, not language.
 */

const THAANA = /[ހ-޿]/;

export type Script = "thaana" | "latin" | "unknown";

export function isThaana(s: string | null | undefined): boolean {
  return !!s && THAANA.test(s);
}

export function scriptOf(s: string | null | undefined): Script {
  if (!s || !s.trim()) return "unknown";
  return THAANA.test(s) ? "thaana" : "latin";
}

export function dirFor(s: string | null | undefined): "rtl" | "ltr" {
  return isThaana(s) ? "rtl" : "ltr";
}

export function langFor(s: string | null | undefined): "dv" | "en" {
  return isThaana(s) ? "dv" : "en";
}
```

- [ ] **Step 4: Write Bidi**

`web/src/components/Bidi.tsx`:

```tsx
import { createElement, type ElementType } from "react";
import { dirFor, langFor } from "@/lib/script";

type Props = {
  text: string | null | undefined;
  as?: ElementType;
  className?: string;
} & Record<string, unknown>;

/**
 * Per-element direction. Spec 10.
 *
 * Every piece of user-facing text from the corpus goes through this. The
 * alternative -- flipping the page or a container -- is wrong the moment one
 * result is Thaana and its neighbour is Latin, which in this corpus is the
 * normal case rather than the edge case.
 */
export function Bidi({ text, as = "span", className, ...rest }: Props) {
  if (!text) return null;
  return createElement(
    as,
    { dir: dirFor(text), lang: langFor(text), className, ...rest },
    text
  );
}
```

- [ ] **Step 5: Write MetaProvider and SourceBadge**

`web/src/components/MetaProvider.tsx`:

```tsx
"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { MetaOut, SourceOut } from "@/lib/api";

export const MetaContext = createContext<MetaOut | null>(null);

/**
 * The registry, fetched once on the server and handed down. Spec 10: a card
 * never issues its own request for an icon, and nothing about tabs, labels or
 * sources is hardcoded here.
 */
export function MetaProvider({
  meta,
  children,
}: {
  meta: MetaOut;
  children: ReactNode;
}) {
  return <MetaContext.Provider value={meta}>{children}</MetaContext.Provider>;
}

export function useMeta(): MetaOut | null {
  return useContext(MetaContext);
}

export function useSource(key: string): SourceOut | undefined {
  return useMeta()?.sources.find((s) => s.key === key);
}
```

`web/src/components/SourceBadge.tsx`:

```tsx
"use client";

import { Bidi } from "./Bidi";
import { useSource } from "./MetaProvider";

const SIZES = { sm: 16, md: 20 } as const;

/**
 * Source attribution. Spec 8.5.
 *
 * Three rules, all of them load-bearing:
 *  - always paired with a label, never a bare icon
 *  - 16px in dense contexts, 20px on cards
 *  - placed with `inline-start`, never `left`, so it flips with dir
 */
export function SourceBadge({
  sourceKey,
  size = "md",
  lang = "en",
}: {
  sourceKey: string;
  size?: keyof typeof SIZES;
  lang?: "en" | "dv";
}) {
  const source = useSource(sourceKey);
  if (!source) return null;

  const px = SIZES[size];
  const label = lang === "dv" && source.label_dv ? source.label_dv : source.label_en;

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted ms-0">
      {source.icon ? (
        <img
          src={source.icon}
          alt=""
          aria-hidden="true"
          width={px}
          height={px}
          className="shrink-0 rounded-[2px]"
          loading="lazy"
        />
      ) : (
        <span
          aria-hidden="true"
          style={{ width: px, height: px }}
          className="grid shrink-0 place-items-center rounded-[3px] bg-chip
                     text-[9px] font-semibold"
        >
          {source.icon_fallback_text || source.label_en.slice(0, 2)}
        </span>
      )}
      <Bidi as="span" text={label} />
    </span>
  );
}
```

- [ ] **Step 6: Font and tokens**

Download a Thaana webfont (MV Faseyha or Faruma class) as woff2 into `web/public/fonts/`. Mirror the Django source icons into `web/public/sources/` — they are a handful of files and serving them from the same origin avoids a second host on every card.

`web/src/app/globals.css`:

```css
@import "tailwindcss";

/* Self-hosted. Never a webfont CDN: it would put a third-party request on
   every page load and leak the referrer, the same argument as 4.3.3's rule
   against hotlinked favicons. */
@font-face {
  font-family: "Thaana";
  src: url("/fonts/mv-faseyha.woff2") format("woff2");
  font-display: swap;
  unicode-range: U+0780-07BF, U+FDF2;
}

@theme {
  --color-bg: #ffffff;
  --color-fg: #101418;
  --color-muted: #5b6570;
  --color-chip: #eef1f4;
  --color-line: #e2e6ea;
  --color-accent: #1a6dd6;
  --font-sans: ui-sans-serif, system-ui, "Segoe UI", sans-serif;
  --font-thaana: "Thaana", var(--font-sans);
}

/* The Thaana face is listed for every element; unicode-range means it only
   applies to Thaana codepoints, so one stack serves both scripts and there is
   no per-element font switching to get wrong. */
html {
  font-family: var(--font-sans), "Thaana";
}

[lang="dv"] {
  font-family: var(--font-thaana);
  line-height: 1.9;   /* fili sit above the baseline and need the room */
}

/* Logical properties only. `margin-left` on a card that may be RTL is the
   single most common way this gets broken. */
.card {
  border: 1px solid var(--color-line);
  border-radius: 10px;
  padding-block: 0.875rem;
  padding-inline: 1rem;
}
```

`web/src/app/layout.tsx`:

```tsx
import { getMeta } from "@/lib/api";
import { MetaProvider } from "@/components/MetaProvider";
import "./globals.css";

export const metadata = { title: "Beynunehcheh" };

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const meta = await getMeta();
  return (
    // The document is LTR. Dhivehi content flips per element (spec 10); the
    // chrome language is a separate concern handled by the toggle.
    <html lang="en" dir="ltr">
      <body className="bg-bg text-fg antialiased">
        <MetaProvider meta={meta}>{children}</MetaProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 7: Run the tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
jj commit -m "P6 task 2: script detection, RTL primitives, source badge"
```

---

### Task 3: The four card components

**Files:**
- Create: `web/src/test/fixtures.ts`, `web/src/lib/format.ts`, `web/src/components/cards/*.tsx`
- Test: `web/src/components/cards/*.test.tsx`

**Interfaces:**
- Produces: `<JobCard result />`, `<PropertyCard result />`, `<ShoppingCard result />`, `<NewsCard result />`, `<ResultCard result position />` (the dispatcher), `formatMoney`, `formatRelative`.

One component per `doc_type`, mapping 1:1 to the `card` payload. The card renders what the payload says; it does not recompute, reformat or second-guess.

- [ ] **Step 1: Write the fixtures**

`web/src/test/fixtures.ts`:

```ts
import type { ResultOut } from "@/lib/api";

const base = {
  id: 1, source: "gazette", url: "https://gazette.gov.mv/iulaan/1",
  title: "Administrative Officer", summary: "A GS3 post.", translated: false,
  score: 1.0,
};

export const jobResult: ResultOut = {
  ...base, doc_type: "job",
  card: {
    source: "gazette",
    role: "Administrative Officer",
    employer: "Ministry of Example",
    salary_display: "MVR 10,750 / month",
    salary_state: "listed",
    net_estimate: {
      value: 14397.5, is_floor: false, working_days: 20, completeness: "full",
      breakdown: [
        { label: "basic", amount: 10750 },
        { label: "pension", amount: -752.5 },
        { label: "attendance", amount: 4400 },
      ],
    },
    compensation: {
      basic_salary: 10750, currency: "MVR", period: "month",
      pension_applies: true, pension_rate: 0.07, salary_state: "listed",
      completeness: "full",
      allowances: [{ kind: "attendance", label_raw: "ހާޒިރީ އެލަވަންސް",
                     amount: 4400, basis: "fixed_monthly" }],
    },
    grade: "GS3", location: "Male", position_type: "Permanent",
    deadline: "2026-08-31", deadline_state: "open",
    apply_kinds: ["form", "email"],
    detail_source: "attachment",
  },
} as ResultOut;

export const jobUnlisted: ResultOut = {
  ...jobResult,
  card: { ...jobResult.card, salary_display: "Unlisted", salary_state: "unlisted",
          net_estimate: null },
} as ResultOut;

export const jobNegotiable: ResultOut = {
  ...jobResult,
  card: { ...jobResult.card, salary_display: "Negotiable",
          salary_state: "negotiable", net_estimate: null },
} as ResultOut;

export const jobFloorEstimate: ResultOut = {
  ...jobResult,
  card: {
    ...jobResult.card,
    net_estimate: { ...(jobResult.card as never as Record<string, never>) as never,
                    value: 12000, is_floor: true, working_days: 20,
                    completeness: "partial", breakdown: [] },
  },
} as ResultOut;

export const propertyRoomOfThree: ResultOut = {
  ...base, id: 2, source: "ibay", doc_type: "property",
  title: "Room in Apartment", url: "https://ibay.com.mv/2",
  card: {
    source: "ibay", hero_image: "https://x/1.jpg", image_count: 4,
    location_display: "Hulhumale Phase 2",
    rent_display: "MVR 7,000 / month", currency: "MVR", currency_inferred: false,
    capacity_display: "1 room of 3, shared",
    unit_kind: "room", is_shared: true,
    bedrooms: 3, bathrooms: 2, furnishing: "Furnished",
    tenant_preference: ["Family"],
  },
} as ResultOut;

export const propertyBedSpace: ResultOut = {
  ...propertyRoomOfThree, id: 3,
  title: "Sharing Bed Space (2 Space)",
  card: { ...propertyRoomOfThree.card, capacity_display: "Bed space, 2 available, shared",
          unit_kind: "bed_space", bedrooms: null, hero_image: null, image_count: 0,
          rent_display: "MVR 2,800 / month", tenant_preference: ["Male", "Working"] },
} as ResultOut;

export const shoppingResult: ResultOut = {
  ...base, id: 4, source: "ibay", doc_type: "shopping",
  title: "KICO METAL POWER SUPPLY 24V-5A-120W", url: "https://ibay.com.mv/4",
  card: {
    source: "ibay", hero_image: "https://x/ps.jpg", image_count: 2,
    title: "KICO METAL POWER SUPPLY 24V-5A-120W",
    price_display: "MVR 850", currency: "MVR", negotiable: false,
    condition: "New", brand: "KICO", location: "Male",
    seller_name: "Kico Store", seller_is_premium: true,
    spec_chips: ["24V", "5A", "120W"],
  },
} as ResultOut;

export const newsResult: ResultOut = {
  ...base, id: 5, doc_type: "news",
  title: "Bids invited for harbour works",
  summary: "The ministry invites sealed bids for harbour construction at Kulhudhuffushi.",
  card: {
    source: "gazette",
    title: "Bids invited for harbour works",
    summary: "The ministry invites sealed bids for harbour construction at Kulhudhuffushi.",
    office: "Ministry of Example", announcement_type: "ބީލަން",
    published_at: "2026-08-01T00:00:00Z",
    external_url: "https://gazette.gov.mv/iulaan/5",
    attachment_count: 2, is_tender: true,
  },
} as ResultOut;

export const dhivehiTitleResult: ResultOut = {
  ...base, id: 6, title: "ވަޒީފާގެ ފުރުޞަތު", translated: true,
  doc_type: "job", card: { ...jobResult.card, role: "ވަޒީފާގެ ފުރުޞަތު" },
} as ResultOut;
```

- [ ] **Step 2: Write the failing card tests**

`web/src/components/cards/JobCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  jobFloorEstimate, jobNegotiable, jobResult, jobUnlisted, dhivehiTitleResult,
} from "@/test/fixtures";
import { JobCard } from "./JobCard";

describe("JobCard", () => {
  it("leads with role, employer and salary", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText("Administrative Officer")).toBeInTheDocument();
    expect(screen.getByText("Ministry of Example")).toBeInTheDocument();
    expect(screen.getByText("MVR 10,750 / month")).toBeInTheDocument();
  });

  it("renders salary_display verbatim rather than interpreting a null", () => {
    render(<JobCard result={jobUnlisted} />);
    expect(screen.getByText("Unlisted")).toBeInTheDocument();
  });

  it("shows Negotiable only when the payload says so", () => {
    render(<JobCard result={jobNegotiable} />);
    expect(screen.getByText("Negotiable")).toBeInTheDocument();
    expect(screen.queryByText("Unlisted")).toBeNull();
  });

  it("renders the take-home estimate as explicitly approximate", () => {
    render(<JobCard result={jobResult} />);
    const est = screen.getByTestId("net-estimate");
    expect(est.textContent).toMatch(/~/);
    expect(est.textContent).toMatch(/14,397|14,398/);
  });

  it("renders a partial estimate as a floor, never as a point value", () => {
    render(<JobCard result={jobFloorEstimate} />);
    expect(screen.getByTestId("net-estimate").textContent).toMatch(/at least/i);
  });

  it("omits the estimate entirely when there is none", () => {
    render(<JobCard result={jobUnlisted} />);
    expect(screen.queryByTestId("net-estimate")).toBeNull();
  });

  it("exposes the estimate's assumptions without leaving the card", async () => {
    render(<JobCard result={jobResult} />);
    await userEvent.click(screen.getByRole("button", { name: /assumptions/i }));
    expect(screen.getByText(/20 working days/i)).toBeInTheDocument();
    expect(screen.getByText(/7%/)).toBeInTheDocument();
  });

  it("says when the details came out of an attachment", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText(/from attached/i)).toBeInTheDocument();
  });

  it("renders an icon row for the apply methods", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByLabelText(/apply via form/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/apply by email/i)).toBeInTheDocument();
  });

  it("renders the deadline state the server computed, not one of its own", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByTestId("deadline").textContent).toMatch(/31/);
  });

  it("gives a Thaana role its own dir", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    expect(screen.getByText("ވަޒީފާގެ ފުރުޞަތު")).toHaveAttribute("dir", "rtl");
  });
});
```

`web/src/components/cards/PropertyCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { propertyBedSpace, propertyRoomOfThree } from "@/test/fixtures";
import { PropertyCard } from "./PropertyCard";

describe("PropertyCard", () => {
  it("shows location, rent and capacity at a glance", () => {
    render(<PropertyCard result={propertyRoomOfThree} />);
    expect(screen.getByText("Hulhumale Phase 2")).toBeInTheDocument();
    expect(screen.getByText("MVR 7,000 / month")).toBeInTheDocument();
    expect(screen.getByText("1 room of 3, shared")).toBeInTheDocument();
  });

  it("NEVER renders one room of three as a three-bedroom unit", () => {
    // The concrete failure spec 8.2 exists to prevent. `bedrooms: 3` is in the
    // payload and must not become the headline capacity.
    render(<PropertyCard result={propertyRoomOfThree} />);
    const capacity = screen.getByTestId("capacity");
    expect(capacity.textContent).toBe("1 room of 3, shared");
    expect(capacity.textContent).not.toMatch(/3 bedroom/i);
  });

  it("renders bed-space capacity as given", () => {
    render(<PropertyCard result={propertyBedSpace} />);
    expect(screen.getByTestId("capacity").textContent)
      .toBe("Bed space, 2 available, shared");
  });

  it("shows a hero image with a count when there are several", () => {
    render(<PropertyCard result={propertyRoomOfThree} />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://x/1.jpg");
    expect(screen.getByText(/4/)).toBeInTheDocument();
  });

  it("renders a placeholder rather than a broken image when there is none", () => {
    render(<PropertyCard result={propertyBedSpace} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByTestId("no-image")).toBeInTheDocument();
  });

  it("renders tenant preference as chips", () => {
    render(<PropertyCard result={propertyBedSpace} />);
    expect(screen.getByText("Male")).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
  });

  it("marks an inferred currency differently from a stated one", () => {
    const inferred = {
      ...propertyRoomOfThree,
      card: { ...propertyRoomOfThree.card, currency_inferred: true },
    } as never;
    render(<PropertyCard result={inferred} />);
    expect(screen.getByTitle(/currency inferred/i)).toBeInTheDocument();
  });
});
```

`web/src/components/cards/ShoppingCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { shoppingResult } from "@/test/fixtures";
import { ShoppingCard } from "./ShoppingCard";

describe("ShoppingCard", () => {
  it("is image-forward with price and title", () => {
    render(<ShoppingCard result={shoppingResult} />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://x/ps.jpg");
    expect(screen.getByText("MVR 850")).toBeInTheDocument();
  });

  it("renders up to three spec chips", () => {
    render(<ShoppingCard result={shoppingResult} />);
    ["24V", "5A", "120W"].forEach((c) =>
      expect(screen.getByText(c)).toBeInTheDocument()
    );
  });

  it("badges the condition", () => {
    render(<ShoppingCard result={shoppingResult} />);
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("marks a premium seller", () => {
    render(<ShoppingCard result={shoppingResult} />);
    expect(screen.getByTestId("premium")).toBeInTheDocument();
  });

  it("lazy-loads images", () => {
    render(<ShoppingCard result={shoppingResult} />);
    expect(screen.getByRole("img")).toHaveAttribute("loading", "lazy");
  });
});
```

`web/src/components/cards/NewsCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { newsResult } from "@/test/fixtures";
import { NewsCard } from "./NewsCard";

describe("NewsCard", () => {
  it("is a single outbound anchor to the source", () => {
    render(<NewsCard result={newsResult} />);
    const a = screen.getByRole("link");
    expect(a).toHaveAttribute("href", "https://gazette.gov.mv/iulaan/5");
    expect(a).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(a).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
  });

  it("does NOT link to an internal detail route", () => {
    // Spec 8.4: no detail page. Building an internal reader for content we do
    // not own is work that helps nobody.
    render(<NewsCard result={newsResult} />);
    expect(screen.getByRole("link").getAttribute("href"))
      .not.toMatch(/^\/documents\//);
  });

  it("carries the excerpt, which is the whole product for a news result", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.getByText(/sealed bids for harbour/i)).toBeInTheDocument();
  });

  it("shows the office and attachment count", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.getByText("Ministry of Example")).toBeInTheDocument();
    expect(screen.getByText(/2 documents/i)).toBeInTheDocument();
  });

  it("gives the Thaana announcement type its own dir", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.getByText("ބީލަން")).toHaveAttribute("dir", "rtl");
  });
});
```

- [ ] **Step 3: Run to confirm failure**

Run: `npm test`
Expected: FAIL.

- [ ] **Step 4: Write `format.ts`**

`web/src/lib/format.ts`:

```ts
export function formatMoney(value: number, currency = "MVR"): string {
  return `${currency} ${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** '~MVR 14,398' -- always approximate, never a precise-looking figure. */
export function formatApprox(value: number, currency = "MVR"): string {
  return `~${formatMoney(Math.round(value), currency)}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function formatRelative(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const days = Math.round((now.getTime() - d.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  if (days < 365) return `${Math.round(days / 30)} months ago`;
  return `${Math.round(days / 365)} years ago`;
}
```

- [ ] **Step 5: Write the cards**

`web/src/components/cards/JobCard.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";
import { formatApprox, formatDate } from "@/lib/format";

const APPLY_LABEL: Record<string, string> = {
  form: "Apply via form",
  email: "Apply by email",
  phone: "Apply by phone",
  viber: "Apply on Viber",
  whatsapp: "Apply on WhatsApp",
  portal: "Apply on a portal",
  walk_in: "Apply in person",
  post: "Apply by post",
};

const APPLY_ICON: Record<string, string> = {
  form: "📝", email: "✉️", phone: "📞", viber: "💬",
  whatsapp: "💬", portal: "🌐", walk_in: "🚶", post: "📮",
};

const DEADLINE_TONE: Record<string, string> = {
  open: "text-muted",
  closing_soon: "text-amber-700",
  closed: "text-muted line-through",
};

export function JobCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const [showAssumptions, setShowAssumptions] = useState(false);
  const est = c.net_estimate as
    | { value: number; is_floor: boolean; working_days: number }
    | null;

  return (
    <article className="card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Bidi
            as="h3"
            text={(c.role as string) || result.title}
            className="truncate text-base font-semibold"
          />
          <Bidi as="p" text={c.employer as string} className="text-sm text-muted" />
        </div>
        <SourceBadge sourceKey={result.source} />
      </div>

      <div className="mt-2">
        {/* Rendered verbatim. Already resolved server-side to one of three
            strings; the frontend never interprets a null into 'Negotiable'. */}
        <p className="text-[15px] font-medium">{c.salary_display as string}</p>

        {est && (
          <p data-testid="net-estimate" className="mt-0.5 text-xs text-muted">
            {est.is_floor ? "at least " : ""}
            {formatApprox(est.value)} take-home{" "}
            <button
              type="button"
              onClick={() => setShowAssumptions((v) => !v)}
              className="underline underline-offset-2"
              aria-expanded={showAssumptions}
            >
              assumptions
            </button>
          </p>
        )}
        {showAssumptions && est && (
          <p className="mt-1 rounded bg-chip px-2 py-1 text-xs text-muted">
            Estimated from the stated line items over {est.working_days} working days,
            less 7% pension on basic salary. Not a figure the employer stated.
          </p>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        {c.grade ? <span>{c.grade as string}</span> : null}
        {c.position_type ? <span>{c.position_type as string}</span> : null}
        <Bidi as="span" text={c.location as string} />
        {c.deadline ? (
          <span
            data-testid="deadline"
            className={DEADLINE_TONE[(c.deadline_state as string) ?? "open"]}
          >
            Closes {formatDate(c.deadline as string)}
          </span>
        ) : null}
        {c.detail_source === "attachment" ? (
          <span className="rounded bg-chip px-1.5 py-0.5">
            Details from attached document
          </span>
        ) : null}
      </div>

      {(c.apply_kinds as string[])?.length ? (
        <div className="mt-2 flex gap-1.5">
          {(c.apply_kinds as string[]).map((k) => (
            <span
              key={k}
              aria-label={APPLY_LABEL[k] ?? k}
              title={APPLY_LABEL[k] ?? k}
              className="grid h-6 w-6 place-items-center rounded bg-chip text-xs"
            >
              {APPLY_ICON[k] ?? "•"}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}
```

`web/src/components/cards/PropertyCard.tsx`:

```tsx
"use client";

import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";

export function PropertyCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const hero = c.hero_image as string | null;

  return (
    <article className="card flex gap-3">
      {hero ? (
        <div className="relative shrink-0">
          <img
            src={hero}
            alt=""
            loading="lazy"
            className="h-24 w-32 rounded-md object-cover"
          />
          {(c.image_count as number) > 1 && (
            <span className="absolute bottom-1 end-1 rounded bg-black/60 px-1
                             text-[10px] text-white">
              {c.image_count as number}
            </span>
          )}
        </div>
      ) : (
        <div
          data-testid="no-image"
          className="grid h-24 w-32 shrink-0 place-items-center rounded-md
                     bg-chip text-xs text-muted"
        >
          No photo
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <Bidi
            as="h3"
            text={(c.location_display as string) || result.title}
            className="truncate font-semibold"
          />
          <SourceBadge sourceKey={result.source} />
        </div>

        <p className="mt-0.5 text-[15px] font-medium">
          {c.rent_display as string}
          {c.currency_inferred ? (
            <span title="Currency inferred, not stated in the listing"
                  className="ms-1 text-xs text-muted">
              *
            </span>
          ) : null}
        </p>

        {/* Rendered exactly as the server computed it. Never reconstructed
            from `bedrooms` -- one room of three is not a 3-bedroom unit
            (spec 8.2). */}
        <p data-testid="capacity" className="mt-0.5 text-sm text-muted">
          {c.capacity_display as string}
        </p>

        <div className="mt-1.5 flex flex-wrap gap-1">
          {((c.tenant_preference as string[]) ?? []).map((t) => (
            <Bidi
              key={t}
              as="span"
              text={t}
              className="rounded bg-chip px-1.5 py-0.5 text-[11px]"
            />
          ))}
        </div>
      </div>
    </article>
  );
}
```

`web/src/components/cards/ShoppingCard.tsx`:

```tsx
"use client";

import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";

export function ShoppingCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const hero = c.hero_image as string | null;

  return (
    <article className="card flex flex-col gap-2">
      {hero ? (
        <img
          src={hero}
          alt=""
          loading="lazy"
          className="aspect-square w-full rounded-md object-cover"
        />
      ) : (
        <div className="grid aspect-square w-full place-items-center rounded-md
                        bg-chip text-xs text-muted">
          No photo
        </div>
      )}

      <Bidi
        as="h3"
        text={(c.title as string) || result.title}
        className="line-clamp-2 text-sm font-medium"
      />

      <p className="text-[15px] font-semibold">
        {(c.price_display as string) ?? "Price on request"}
        {c.negotiable ? (
          <span className="ms-1 text-xs font-normal text-muted">negotiable</span>
        ) : null}
      </p>

      {(c.spec_chips as string[])?.length ? (
        <div className="flex flex-wrap gap-1">
          {(c.spec_chips as string[]).map((s) => (
            <span key={s} className="rounded bg-chip px-1.5 py-0.5 text-[11px]">
              {s}
            </span>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-2 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          {c.condition ? (
            <span className="rounded bg-chip px-1.5 py-0.5">
              {c.condition as string}
            </span>
          ) : null}
          {c.seller_is_premium ? (
            <span data-testid="premium" title="Premium seller">★</span>
          ) : null}
        </span>
        <SourceBadge sourceKey={result.source} size="sm" />
      </div>
    </article>
  );
}
```

`web/src/components/cards/NewsCard.tsx`:

```tsx
"use client";

import { Bidi } from "@/components/Bidi";
import { SourceBadge } from "@/components/SourceBadge";
import type { ResultOut } from "@/lib/api";
import { formatRelative } from "@/lib/format";

/**
 * Four things and nothing else: icon, title, excerpt, link out. Spec 8.4.
 *
 * The whole card is the anchor. There is no detail route for news, and adding
 * one would mean building a reader for content we do not own.
 */
export function NewsCard({ result }: { result: ResultOut }) {
  const c = result.card as Record<string, never>;
  const href = (c.external_url as string) || result.url;

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="card block hover:border-accent"
    >
      <div className="flex items-center justify-between gap-2">
        <SourceBadge sourceKey={result.source} size="sm" />
        <span className="text-xs text-muted">
          {formatRelative(c.published_at as string)}
        </span>
      </div>

      <Bidi
        as="h3"
        text={(c.title as string) || result.title}
        className="mt-1 font-semibold"
      />
      <Bidi
        as="p"
        text={(c.summary as string) || result.summary}
        className="mt-0.5 line-clamp-2 text-sm text-muted"
      />

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 text-xs text-muted">
        <Bidi as="span" text={c.office as string} />
        <Bidi as="span" text={c.announcement_type as string} />
        {(c.attachment_count as number) > 0 ? (
          <span>{c.attachment_count as number} documents</span>
        ) : null}
      </div>
    </a>
  );
}
```

`web/src/components/cards/ResultCard.tsx`:

```tsx
"use client";

import type { ResultOut } from "@/lib/api";
import { JobCard } from "./JobCard";
import { NewsCard } from "./NewsCard";
import { PropertyCard } from "./PropertyCard";
import { ShoppingCard } from "./ShoppingCard";

const CARDS = {
  job: JobCard,
  property: PropertyCard,
  shopping: ShoppingCard,
  news: NewsCard,
} as const;

export function ResultCard({ result }: { result: ResultOut }) {
  // news is the default sink (spec 5.3), so an unknown doc_type rendering as
  // news is correct rather than a fallback.
  const Card = CARDS[result.doc_type as keyof typeof CARDS] ?? NewsCard;
  return <Card result={result} />;
}
```

- [ ] **Step 6: Run the tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P6 task 3: the four card components"
```

---

### Task 4: URL state, tabs, facet UI

**Files:**
- Create: `web/src/lib/url.ts`, `web/src/components/Tabs.tsx`, `web/src/components/facets/*.tsx`
- Test: `web/src/lib/url.test.ts`, `web/src/components/facets/FacetPanel.test.tsx`

**Interfaces:**
- Produces: `parseSearchParams(sp)`, `toSearchParams(state)`, `toggleFilter(state, key, value)`, `setRange(state, key, lo, hi)`, `clearFacet(state, key)`, `<Tabs>`, `<FacetPanel facets state onChange>`.

Filter state lives in the URL. A filtered result page is then a shareable link, the back button works, and the server component can render the first paint without waiting for client hydration.

- [ ] **Step 1: Write the failing test**

`web/src/lib/url.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  clearFacet, parseSearchParams, setRange, toSearchParams, toggleFilter,
} from "./url";

const parse = (qs: string) => parseSearchParams(new URLSearchParams(qs));

describe("parseSearchParams", () => {
  it("reads q, tab, page and sort", () => {
    const s = parse("q=phone&type=shopping&page=2&sort=price_asc");
    expect(s).toMatchObject({ q: "phone", type: "shopping", page: 2,
                              sort: "price_asc" });
  });

  it("defaults to the all tab, page 1, relevance", () => {
    expect(parse("q=x")).toMatchObject({ type: "all", page: 1,
                                         sort: "relevance" });
  });

  it("collects repeated f params", () => {
    expect(parse("q=x&f=brand:Apple&f=brand:Nokia").f)
      .toEqual(["brand:Apple", "brand:Nokia"]);
  });

  it("ignores a non-numeric page rather than throwing", () => {
    expect(parse("q=x&page=banana").page).toBe(1);
  });
});

describe("toggleFilter", () => {
  it("adds a value that is not present", () => {
    expect(toggleFilter(parse("q=x"), "brand", "Apple").f).toEqual(["brand:Apple"]);
  });

  it("removes a value that is present", () => {
    const s = parse("q=x&f=brand:Apple&f=brand:Nokia");
    expect(toggleFilter(s, "brand", "Apple").f).toEqual(["brand:Nokia"]);
  });

  it("resets to page 1 -- page 4 of the old filter set is meaningless", () => {
    const s = { ...parse("q=x&page=4") };
    expect(toggleFilter(s, "brand", "Apple").page).toBe(1);
  });

  it("preserves the query and the tab", () => {
    const s = toggleFilter(parse("q=phone&type=shopping"), "brand", "Apple");
    expect(s.q).toBe("phone");
    expect(s.type).toBe("shopping");
  });
});

describe("setRange", () => {
  it("writes one f entry per key and replaces the previous range", () => {
    let s = setRange(parse("q=x"), "price", 100, 500);
    expect(s.f).toEqual(["price:100..500"]);
    s = setRange(s, "price", 200, 600);
    expect(s.f).toEqual(["price:200..600"]);
  });

  it("supports open-ended ranges", () => {
    expect(setRange(parse("q=x"), "price", 100, null).f).toEqual(["price:100.."]);
    expect(setRange(parse("q=x"), "price", null, 500).f).toEqual(["price:..500"]);
  });

  it("clears the filter when both bounds are null", () => {
    const s = setRange(setRange(parse("q=x"), "price", 1, 2), "price", null, null);
    expect(s.f).toEqual([]);
  });
});

describe("clearFacet", () => {
  it("removes every entry for one key and leaves the others", () => {
    const s = parse("q=x&f=brand:Apple&f=brand:Nokia&f=condition:New");
    expect(clearFacet(s, "brand").f).toEqual(["condition:New"]);
  });
});

describe("toSearchParams", () => {
  it("round-trips", () => {
    const original = parse("q=phone&type=shopping&f=brand:Apple&sort=price_asc");
    expect(parseSearchParams(toSearchParams(original))).toEqual(original);
  });

  it("omits defaults so the URL stays short", () => {
    expect(toSearchParams(parse("q=x")).toString()).toBe("q=x");
  });

  it("changing the tab drops filters that belong to the old tab", () => {
    // Facet keys are per-doc_type (spec 9); carrying `bedrooms` into the Jobs
    // tab would produce a 400 from the API.
    const s = { ...parse("q=x&type=property&f=bedrooms:3"), type: "job", f: [] };
    expect(toSearchParams(s).getAll("f")).toEqual([]);
  });
});
```

`web/src/components/facets/FacetPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FacetPanel } from "./FacetPanel";

const facets = [
  { key: "brand", label: "Brand", label_dv: "ބްރޭންޑް", widget: "checkbox",
    unit: "", values: [{ value: "Apple", label: "Apple", count: 12 },
                       { value: "Nokia", label: "Nokia", count: 3 }],
    min: null, max: null, histogram: [], count_true: null },
  { key: "price", label: "Price", label_dv: "އަގު", widget: "range", unit: "MVR",
    values: [], min: 100, max: 9500,
    histogram: [{ from: 100, to: 1040, count: 4 },
                { from: 1040, to: 1980, count: 9 }],
    count_true: null },
  { key: "has_images", label: "Has photos", label_dv: "ފޮޓޯ", widget: "toggle",
    unit: "", values: [], min: null, max: null, histogram: [], count_true: 7 },
] as never;

const state = { q: "phone", type: "shopping", page: 1, per_page: 20,
                sort: "relevance", f: [] as string[] };

describe("FacetPanel", () => {
  it("renders one control per facet, in the order given", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["Brand", "Price", "Has photos"]);
  });

  it("shows the count next to each checkbox value", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByLabelText(/Apple/)).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("emits a new state when a checkbox is ticked", async () => {
    const onChange = vi.fn();
    render(<FacetPanel facets={facets} state={state} onChange={onChange} />);
    await userEvent.click(screen.getByLabelText(/Apple/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ f: ["brand:Apple"] })
    );
  });

  it("reflects an already-applied filter as checked", () => {
    render(
      <FacetPanel facets={facets} state={{ ...state, f: ["brand:Apple"] }}
                  onChange={() => {}} />
    );
    expect(screen.getByLabelText(/Apple/)).toBeChecked();
  });

  it("renders a toggle with its true-count", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByLabelText(/Has photos/)).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("renders the range histogram as bars, not as a table", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getAllByTestId("hist-bar")).toHaveLength(2);
  });

  it("shows the unit on a range facet", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByText(/MVR/)).toBeInTheDocument();
  });

  it("offers a clear action per facet only when that facet is active", async () => {
    const { rerender } = render(
      <FacetPanel facets={facets} state={state} onChange={() => {}} />
    );
    expect(screen.queryByRole("button", { name: /clear brand/i })).toBeNull();
    rerender(
      <FacetPanel facets={facets} state={{ ...state, f: ["brand:Apple"] }}
                  onChange={() => {}} />
    );
    expect(screen.getByRole("button", { name: /clear brand/i })).toBeInTheDocument();
  });

  it("renders nothing when there are no facets", () => {
    const { container } = render(
      <FacetPanel facets={[] as never} state={state} onChange={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `npm test`
Expected: FAIL.

- [ ] **Step 3: Write `url.ts`**

`web/src/lib/url.ts`:

```ts
export interface SearchState {
  q: string;
  type: string;
  page: number;
  per_page: number;
  sort: string;
  f: string[];
}

const DEFAULTS = { type: "all", page: 1, per_page: 20, sort: "relevance" };

export function parseSearchParams(sp: URLSearchParams): SearchState {
  const page = Number.parseInt(sp.get("page") ?? "", 10);
  const perPage = Number.parseInt(sp.get("per_page") ?? "", 10);
  return {
    q: sp.get("q") ?? "",
    type: sp.get("type") ?? DEFAULTS.type,
    page: Number.isFinite(page) && page > 0 ? page : DEFAULTS.page,
    per_page: Number.isFinite(perPage) && perPage > 0 ? perPage : DEFAULTS.per_page,
    sort: sp.get("sort") ?? DEFAULTS.sort,
    f: sp.getAll("f"),
  };
}

export function toSearchParams(s: SearchState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  if (s.type !== DEFAULTS.type) sp.set("type", s.type);
  if (s.page !== DEFAULTS.page) sp.set("page", String(s.page));
  if (s.per_page !== DEFAULTS.per_page) sp.set("per_page", String(s.per_page));
  if (s.sort !== DEFAULTS.sort) sp.set("sort", s.sort);
  for (const f of s.f) sp.append("f", f);
  return sp;
}

/** Any filter edit resets to page 1: page 4 of a different result set is
 *  not the page the user was looking at. */
const reset = (s: SearchState, f: string[]): SearchState => ({ ...s, f, page: 1 });

export function toggleFilter(s: SearchState, key: string, value: string): SearchState {
  const entry = `${key}:${value}`;
  return reset(
    s,
    s.f.includes(entry) ? s.f.filter((x) => x !== entry) : [...s.f, entry]
  );
}

export function setRange(
  s: SearchState,
  key: string,
  lo: number | null,
  hi: number | null
): SearchState {
  const without = s.f.filter((x) => !x.startsWith(`${key}:`));
  if (lo === null && hi === null) return reset(s, without);
  return reset(s, [...without, `${key}:${lo ?? ""}..${hi ?? ""}`]);
}

export function clearFacet(s: SearchState, key: string): SearchState {
  return reset(s, s.f.filter((x) => !x.startsWith(`${key}:`)));
}

export function activeValues(s: SearchState, key: string): string[] {
  return s.f
    .filter((x) => x.startsWith(`${key}:`))
    .map((x) => x.slice(key.length + 1));
}

/** Facet keys are per-doc_type, so a tab change must drop them or the API
 *  returns 400 for a key that is unknown in the new tab. */
export function changeTab(s: SearchState, type: string): SearchState {
  return { ...s, type, f: [], page: 1 };
}
```

- [ ] **Step 4: Write the facet components**

`web/src/components/facets/CheckboxFacet.tsx`:

```tsx
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
```

`web/src/components/facets/RangeFacet.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { FacetOut } from "@/lib/api";
import { activeValues, type SearchState } from "@/lib/url";

export function RangeFacet({
  facet, state, onApply,
}: {
  facet: FacetOut;
  state: SearchState;
  onApply: (lo: number | null, hi: number | null) => void;
}) {
  const current = activeValues(state, facet.key)[0] ?? "";
  const [lo, hi] = current.split("..");
  const [loV, setLo] = useState(lo ?? "");
  const [hiV, setHi] = useState(hi ?? "");

  const peak = Math.max(1, ...facet.histogram.map((b) => (b as never as { count: number }).count));

  return (
    <div className="space-y-2">
      <div className="flex h-10 items-end gap-0.5" aria-hidden="true">
        {facet.histogram.map((b, i) => {
          const bucket = b as never as { count: number };
          return (
            <span
              key={i}
              data-testid="hist-bar"
              className="flex-1 rounded-t-[2px] bg-chip"
              style={{ height: `${Math.max(4, (bucket.count / peak) * 100)}%` }}
            />
          );
        })}
      </div>
      <div className="flex items-center gap-1.5 text-sm">
        <input
          type="number"
          inputMode="numeric"
          value={loV}
          placeholder={facet.min != null ? String(Math.floor(facet.min)) : "min"}
          onChange={(e) => setLo(e.target.value)}
          aria-label={`${facet.label} minimum`}
          className="w-20 rounded border border-line px-1.5 py-1"
        />
        <span className="text-muted">-</span>
        <input
          type="number"
          inputMode="numeric"
          value={hiV}
          placeholder={facet.max != null ? String(Math.ceil(facet.max)) : "max"}
          onChange={(e) => setHi(e.target.value)}
          aria-label={`${facet.label} maximum`}
          className="w-20 rounded border border-line px-1.5 py-1"
        />
        {facet.unit ? <span className="text-xs text-muted">{facet.unit}</span> : null}
        <button
          type="button"
          className="rounded bg-chip px-2 py-1 text-xs"
          onClick={() =>
            onApply(loV === "" ? null : Number(loV), hiV === "" ? null : Number(hiV))
          }
        >
          Apply
        </button>
      </div>
    </div>
  );
}
```

`web/src/components/facets/ToggleFacet.tsx`:

```tsx
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
        <input type="checkbox" checked={on} onChange={onToggle} />
        {facet.label}
      </span>
      <span className="text-xs text-muted">{facet.count_true}</span>
    </label>
  );
}
```

`web/src/components/facets/FacetPanel.tsx`:

```tsx
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
          <section key={facet.key} className="border-b border-line pb-3 last:border-0">
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{facet.label}</h3>
              {isActive && (
                <button
                  type="button"
                  className="text-xs text-accent underline"
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
```

`web/src/components/Tabs.tsx`:

```tsx
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
```

Tabs are real anchors with an `href` and a prevented default: the tab bar keeps working without JS, is middle-clickable into a new tab, and is crawlable.

- [ ] **Step 5: Run the tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P6 task 4: URL state, tabs, facet UI"
```

---

### Task 5: The search page

**Files:**
- Create: `web/src/app/page.tsx`, `web/src/app/search/page.tsx`, `web/src/components/SearchBox.tsx`, `web/src/components/ResultList.tsx`, `web/src/components/SearchShell.tsx`, `web/src/app/error.tsx`, `web/src/app/not-found.tsx`
- Test: `web/src/components/ResultList.test.tsx`, `web/src/components/SearchBox.test.tsx`

**Interfaces:**
- Produces: `/` and `/search` routes, `<SearchBox>`, `<ResultList results queryId>`, `<SearchShell>`.

- [ ] **Step 1: Write the failing tests**

`web/src/components/ResultList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { jobResult, newsResult, shoppingResult } from "@/test/fixtures";
import { ResultList } from "./ResultList";

describe("ResultList", () => {
  it("dispatches each result to the card for its type", () => {
    render(<ResultList results={[jobResult, shoppingResult, newsResult]}
                       queryId={1} />);
    expect(screen.getByText("Administrative Officer")).toBeInTheDocument();
    expect(screen.getByText("MVR 850")).toBeInTheDocument();
    expect(screen.getByText(/harbour works/i)).toBeInTheDocument();
  });

  it("logs a click with its zero-based position", async () => {
    const spy = vi.spyOn(api, "postClick").mockResolvedValue(undefined);
    render(<ResultList results={[jobResult, shoppingResult]} queryId={42} />);
    await userEvent.click(screen.getByText("MVR 850"));
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ query_id: 42, document_id: shoppingResult.id,
                                position: 1 })
    );
  });

  it("does not log when there is no query id", async () => {
    const spy = vi.spyOn(api, "postClick").mockResolvedValue(undefined);
    render(<ResultList results={[jobResult]} queryId={null} />);
    await userEvent.click(screen.getByText("Administrative Officer"));
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders an empty state rather than an empty page", () => {
    render(<ResultList results={[]} queryId={1} />);
    expect(screen.getByText(/no results/i)).toBeInTheDocument();
  });

  it("uses a grid for a shopping-only result set and a list otherwise", () => {
    const { rerender } = render(
      <ResultList results={[shoppingResult]} queryId={1} tab="shopping" />
    );
    expect(screen.getByTestId("results")).toHaveClass("grid");
    rerender(<ResultList results={[jobResult]} queryId={1} tab="job" />);
    expect(screen.getByTestId("results")).not.toHaveClass("grid");
  });
});
```

`web/src/components/SearchBox.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { SearchBox } from "./SearchBox";

describe("SearchBox", () => {
  it("submits the typed query", async () => {
    const onSubmit = vi.fn();
    render(<SearchBox initial="" onSubmit={onSubmit} />);
    await userEvent.type(screen.getByRole("searchbox"), "power supply{enter}");
    expect(onSubmit).toHaveBeenCalledWith("power supply");
  });

  it("accepts Thaana input and marks the field rtl as it is typed", async () => {
    render(<SearchBox initial="" onSubmit={() => {}} />);
    const box = screen.getByRole("searchbox");
    await userEvent.type(box, "ވަޒީފާ");
    expect(box).toHaveAttribute("dir", "rtl");
  });

  it("stays ltr for Latin input", async () => {
    render(<SearchBox initial="" onSubmit={() => {}} />);
    const box = screen.getByRole("searchbox");
    await userEvent.type(box, "phone");
    expect(box).toHaveAttribute("dir", "ltr");
  });

  it("shows suggestions after a pause and not on every keystroke", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(api, "getSuggest").mockResolvedValue({
      suggestions: [{ term: "iphone", doc_type: "shopping" }],
    });
    render(<SearchBox initial="" onSubmit={() => {}} />);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.type(screen.getByRole("searchbox"), "ipho");
    expect(spy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    vi.useRealTimers();
  });

  it("does not query suggest for a single character", async () => {
    const spy = vi.spyOn(api, "getSuggest");
    render(<SearchBox initial="" onSubmit={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "i");
    expect(spy).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `npm test`
Expected: FAIL.

- [ ] **Step 3: Write SearchBox**

`web/src/components/SearchBox.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { getSuggest } from "@/lib/api";
import { dirFor, langFor } from "@/lib/script";

const DEBOUNCE_MS = 200;
const MIN_SUGGEST_LEN = 2;

export function SearchBox({
  initial, onSubmit,
}: {
  initial: string;
  onSubmit: (q: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const [suggestions, setSuggestions] = useState<{ term: string }[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (value.trim().length < MIN_SUGGEST_LEN) {
      setSuggestions([]);
      return;
    }
    timer.current = setTimeout(() => {
      getSuggest(value)
        .then((r) => {
          setSuggestions(r.suggestions);
          setOpen(true);
        })
        .catch(() => setSuggestions([]));
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value]);

  return (
    <form
      role="search"
      className="relative"
      onSubmit={(e) => {
        e.preventDefault();
        setOpen(false);
        onSubmit(value.trim());
      }}
    >
      <input
        type="search"
        value={value}
        // The field itself flips as the user types, so a Thaana query does not
        // type backwards into an LTR box. Spec 10, per-element.
        dir={dirFor(value)}
        lang={langFor(value)}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        placeholder="Search"
        className="w-full rounded-full border border-line px-4 py-2.5 text-base
                   outline-none focus:border-accent"
      />
      {open && suggestions.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg
                       border border-line bg-bg shadow-lg">
          {suggestions.map((s) => (
            <li key={s.term}>
              <button
                type="button"
                dir={dirFor(s.term)}
                lang={langFor(s.term)}
                className="block w-full px-4 py-2 text-start text-sm hover:bg-chip"
                onMouseDown={() => {
                  setValue(s.term);
                  setOpen(false);
                  onSubmit(s.term);
                }}
              >
                {s.term}
              </button>
            </li>
          ))}
        </ul>
      )}
    </form>
  );
}
```

- [ ] **Step 4: Write ResultList**

`web/src/components/ResultList.tsx`:

```tsx
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
      <p className="py-12 text-center text-sm text-muted">
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
```

- [ ] **Step 5: Write the pages**

`web/src/components/SearchShell.tsx`:

```tsx
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

      <p className="mt-3 text-xs text-muted">
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
                className="rounded border border-line px-3 py-1 disabled:opacity-40"
              >
                Previous
              </button>
              <span className="text-muted">Page {state.page}</span>
              <button
                type="button"
                disabled={state.page * state.per_page >= data.total}
                onClick={() => go({ ...state, page: state.page + 1 })}
                className="rounded border border-line px-3 py-1 disabled:opacity-40"
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
```

`web/src/app/search/page.tsx`:

```tsx
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
```

`web/src/app/page.tsx`:

```tsx
import { redirect } from "next/navigation";
import { SearchBox } from "@/components/SearchBox";

export default function Home() {
  async function go(formData: FormData) {
    "use server";
    const q = String(formData.get("q") ?? "").trim();
    redirect(`/search?q=${encodeURIComponent(q)}`);
  }

  return (
    <main className="mx-auto grid min-h-[70vh] max-w-xl place-items-center px-4">
      <div className="w-full">
        <h1 className="mb-6 text-center text-3xl font-semibold">Beynunehcheh</h1>
        {/* A plain server-action form, so the home page works with JS off. */}
        <form action={go} role="search">
          <input
            type="search"
            name="q"
            placeholder="Search"
            className="w-full rounded-full border border-line px-4 py-3 text-base
                       outline-none focus:border-accent"
          />
        </form>
      </div>
    </main>
  );
}
```

`web/src/app/error.tsx` and `web/src/app/not-found.tsx`: minimal, with a link back to `/`.

- [ ] **Step 6: Run the tests and the app**

Run: `npm test`, then `docker compose --profile web up web` and search for `phone`, `ވަޒީފާ` and `kuyyah`.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P6 task 5: search page, tabs, results, pagination"
```

---

### Task 6: Detail pages — compensation, gallery, spec table

**Files:**
- Create: `web/src/lib/compensation.ts`, `web/src/app/documents/[id]/page.tsx`, `web/src/components/detail/*.tsx`, `web/src/components/ReportDialog.tsx`
- Test: `web/src/lib/compensation.test.ts`, `web/src/components/detail/CompensationTable.test.tsx`, `web/src/components/detail/ApplyBlock.test.tsx`

**Interfaces:**
- Produces: `estimateNet(comp, workingDays)`, `<CompensationTable comp />`, `<ApplyBlock methods />`, `<Gallery images />`, `<SpecTable specs />`, `<ReportDialog documentId />`, the `/documents/[id]` route.

The working-days control recomputes client-side from the same pure logic; nothing is re-fetched. That means `estimate_net` exists twice, in Python and TypeScript, and the two must not drift — hence the shared fixture table below.

- [ ] **Step 1: Write the failing test**

`web/src/lib/compensation.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { estimateNet, type Compensation } from "./compensation";

/**
 * These cases are copied verbatim from tests/enrich/test_compensation.py.
 * The two implementations must agree; if one is changed, change both and
 * update this table in the same commit.
 */
const base: Compensation = {
  basic_salary: null, basic_salary_max: null, currency: "MVR", period: "month",
  allowances: [], pension_applies: false, pension_rate: 0.07,
  salary_state: "unlisted", completeness: "none",
};

const fixed = (kind: string, amount: number) => ({
  kind, label_raw: kind, amount, basis: "fixed_monthly" as const,
});

describe("estimateNet -- parity with the Python implementation", () => {
  it("the worked example: 10750 basic, 4400 attendance, 7% pension", () => {
    const c = { ...base, basic_salary: 10750, allowances: [fixed("attendance", 4400)],
                pension_applies: true, salary_state: "listed" as const,
                completeness: "full" as const };
    expect(estimateNet(c)!.value).toBeCloseTo(14397.5, 2);
  });

  it("pension comes off basic, not off gross", () => {
    const c = { ...base, basic_salary: 10000, allowances: [fixed("living", 5000)],
                pension_applies: true, salary_state: "listed" as const,
                completeness: "full" as const };
    expect(estimateNet(c)!.value).toBeCloseTo(14300, 2);   // not 13950
  });

  it("per-day allowances scale with the working-days control", () => {
    const c = {
      ...base, basic_salary: 8000, pension_applies: true,
      salary_state: "listed" as const, completeness: "full" as const,
      allowances: [{ kind: "attendance", label_raw: "daily", amount: 100,
                     basis: "per_day" as const }],
    };
    expect(estimateNet(c, 20)!.value).toBeCloseTo(8000 - 560 + 2000, 2);
    expect(estimateNet(c, 26)!.value).toBeCloseTo(8000 - 560 + 2600, 2);
  });

  it("percent_of_basic", () => {
    const c = {
      ...base, basic_salary: 10000, salary_state: "listed" as const,
      completeness: "full" as const,
      allowances: [{ kind: "service", label_raw: "35%", amount: 35,
                     basis: "percent_of_basic" as const }],
    };
    expect(estimateNet(c)!.value).toBeCloseTo(13500, 2);
  });

  it("returns null when there is nothing honest to compute", () => {
    expect(estimateNet(base)).toBeNull();
    expect(estimateNet({ ...base, salary_state: "negotiable" })).toBeNull();
    expect(estimateNet({ ...base, basic_salary: 500, period: "day",
                         salary_state: "listed" })).toBeNull();
    expect(estimateNet({ ...base, basic_salary: 10000, salary_state: "listed",
                         completeness: "basic_only" })).toBeNull();
  });

  it("flags a partial estimate as a floor", () => {
    const c = { ...base, basic_salary: 10000, allowances: [fixed("living", 1000)],
                salary_state: "listed" as const, completeness: "partial" as const };
    expect(estimateNet(c)!.is_floor).toBe(true);
  });
});
```

`web/src/components/detail/CompensationTable.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CompensationTable } from "./CompensationTable";

const comp = {
  basic_salary: 8000, basic_salary_max: null, currency: "MVR",
  period: "month" as const, pension_applies: true, pension_rate: 0.07,
  salary_state: "listed" as const, completeness: "full" as const,
  allowances: [{ kind: "attendance", label_raw: "ހާޒިރީ އެލަވަންސް", amount: 100,
                 basis: "per_day" as const }],
};

describe("CompensationTable", () => {
  it("shows every line item the employer stated", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByText(/8,000/)).toBeInTheDocument();
    expect(screen.getByText("ހާޒިރީ އެލަވަންސް")).toHaveAttribute("dir", "rtl");
  });

  it("shows the arithmetic the user can follow", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByText(/pension/i)).toBeInTheDocument();
    expect(screen.getByText(/-560/)).toBeInTheDocument();
  });

  it("recomputes client-side when the working-days control changes", async () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByTestId("net-total").textContent).toMatch(/9,440/);
    const control = screen.getByLabelText(/working days/i);
    await userEvent.clear(control);
    await userEvent.type(control, "26");
    expect(screen.getByTestId("net-total").textContent).toMatch(/10,040/);
  });

  it("labels the total as an estimate, not as pay", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByTestId("net-total").textContent).toMatch(/estimate|~/i);
  });

  it("rejects a working-days value outside 1..31 rather than computing nonsense",
     async () => {
    render(<CompensationTable comp={comp} />);
    const control = screen.getByLabelText(/working days/i);
    await userEvent.clear(control);
    await userEvent.type(control, "400");
    expect(control).toHaveAttribute("max", "31");
    expect(screen.getByTestId("net-total").textContent).not.toMatch(/40,000/);
  });
});
```

`web/src/components/detail/ApplyBlock.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApplyBlock } from "./ApplyBlock";

const methods = [
  { kind: "form", value: "https://forms.gle/abc", label_en: "", label_dv: "" },
  { kind: "email", value: "hr@example.gov.mv", label_en: "", label_dv: "" },
  { kind: "phone", value: "7994400", label_en: "", label_dv: "" },
  { kind: "viber", value: "9223232", label_en: "", label_dv: "" },
];

describe("ApplyBlock", () => {
  it("renders a form link as a button", () => {
    render(<ApplyBlock methods={methods} />);
    const a = screen.getByRole("link", { name: /apply via form/i });
    expect(a).toHaveAttribute("href", "https://forms.gle/abc");
    expect(a).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("renders email as mailto", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /hr@example/i }))
      .toHaveAttribute("href", "mailto:hr@example.gov.mv");
  });

  it("renders a phone number as tap-to-call with the country code", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /7994400/ }))
      .toHaveAttribute("href", "tel:+9607994400");
  });

  it("renders a Viber number as a viber deep link", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /9223232/ })
      .getAttribute("href")).toMatch(/^viber:/);
  });

  it("renders nothing when there are no methods", () => {
    const { container } = render(<ApplyBlock methods={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

Run: `npm test`
Expected: FAIL.

- [ ] **Step 3: Port `estimate_net`**

`web/src/lib/compensation.ts`:

```ts
/**
 * A direct port of enrich/compensation.py.
 *
 * It exists twice on purpose: the working-days control on the detail page
 * recomputes from the line items already in the card payload, so changing it
 * costs no request (spec 4.3.2). The two implementations must agree, and
 * compensation.test.ts holds the same cases as tests/enrich/test_compensation.py
 * to keep them honest.
 */

const HOURS_PER_DAY = 8;
export const DEFAULT_WORKING_DAYS = 20;

export interface Allowance {
  kind: string;
  label_raw: string;
  amount: number | null;
  basis: "fixed_monthly" | "per_day" | "per_hour" | "percent_of_basic";
}

export interface Compensation {
  basic_salary: number | null;
  basic_salary_max: number | null;
  currency: string;
  period: "month" | "day" | "hour" | "year";
  allowances: Allowance[];
  pension_applies: boolean;
  pension_rate: number;
  salary_state: "listed" | "negotiable" | "unlisted";
  completeness: "full" | "partial" | "basic_only" | "none";
}

export interface NetEstimate {
  value: number;
  is_floor: boolean;
  working_days: number;
  completeness: string;
  breakdown: { label: string; amount: number }[];
}

export function estimateNet(
  comp: Compensation,
  workingDays: number = DEFAULT_WORKING_DAYS
): NetEstimate | null {
  if (comp.salary_state !== "listed" || !comp.basic_salary) return null;
  if (comp.period !== "month") return null;
  if (comp.completeness === "none") return null;

  const basic = comp.basic_salary;
  const breakdown: { label: string; amount: number }[] = [
    { label: "basic", amount: basic },
  ];

  let pension = 0;
  if (comp.pension_applies) {
    pension = basic * (comp.pension_rate || 0.07);
    breakdown.push({ label: "pension", amount: -pension });
  }

  let added = 0;
  for (const a of comp.allowances) {
    if (a.amount == null) continue;
    let amount: number;
    switch (a.basis) {
      case "fixed_monthly": amount = a.amount; break;
      case "per_day": amount = a.amount * workingDays; break;
      case "per_hour": amount = a.amount * HOURS_PER_DAY * workingDays; break;
      case "percent_of_basic": amount = (basic * a.amount) / 100; break;
      default: continue;
    }
    added += amount;
    breakdown.push({ label: a.kind, amount });
  }

  if (added === 0 && pension === 0) return null;

  return {
    value: basic - pension + added,
    is_floor: comp.completeness === "partial",
    working_days: workingDays,
    completeness: comp.completeness,
    breakdown,
  };
}
```

- [ ] **Step 4: Write the detail components**

`web/src/components/detail/CompensationTable.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Bidi } from "@/components/Bidi";
import {
  DEFAULT_WORKING_DAYS, estimateNet, type Compensation,
} from "@/lib/compensation";
import { formatMoney } from "@/lib/format";

export function CompensationTable({ comp }: { comp: Compensation }) {
  const [days, setDays] = useState(DEFAULT_WORKING_DAYS);
  const est = estimateNet(comp, days);

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">Pay breakdown</h2>

      <table className="w-full text-sm">
        <tbody>
          <tr className="border-b border-line">
            <td className="py-1.5">Basic salary</td>
            <td className="py-1.5 text-end">
              {comp.basic_salary != null
                ? formatMoney(comp.basic_salary, comp.currency)
                : "-"}
            </td>
          </tr>
          {comp.allowances.map((a, i) => (
            <tr key={i} className="border-b border-line">
              <td className="py-1.5">
                <Bidi as="span" text={a.label_raw || a.kind} />
                {a.basis === "per_day" && (
                  <span className="ms-1 text-xs text-muted">per day</span>
                )}
                {a.basis === "percent_of_basic" && (
                  <span className="ms-1 text-xs text-muted">% of basic</span>
                )}
              </td>
              <td className="py-1.5 text-end">
                {a.amount != null ? a.amount.toLocaleString() : "-"}
              </td>
            </tr>
          ))}
          {comp.pension_applies && est && (
            <tr className="border-b border-line text-muted">
              <td className="py-1.5">
                Pension ({Math.round((comp.pension_rate || 0.07) * 100)}% of basic)
              </td>
              <td className="py-1.5 text-end">
                {Math.round(
                  -(comp.basic_salary ?? 0) * (comp.pension_rate || 0.07)
                ).toLocaleString()}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <label className="flex items-center gap-2 text-sm">
        Working days per month
        <input
          type="number"
          min={1}
          max={31}
          value={days}
          onChange={(e) => {
            const n = Number(e.target.value);
            // Clamped rather than validated-on-submit: a 400-day month would
            // produce a confident, wrong number, which is the failure mode
            // this whole subsystem exists to prevent.
            if (Number.isFinite(n)) setDays(Math.min(31, Math.max(1, n)));
          }}
          className="w-16 rounded border border-line px-1.5 py-0.5"
        />
      </label>

      {est ? (
        <p data-testid="net-total" className="text-base font-semibold">
          {est.is_floor ? "At least " : ""}~{formatMoney(Math.round(est.value),
                                                         comp.currency)}
          <span className="ms-2 text-xs font-normal text-muted">
            estimated take-home, {days} working days
          </span>
        </p>
      ) : (
        <p className="text-sm text-muted">
          Not enough detail in the listing to estimate take-home pay.
        </p>
      )}
    </section>
  );
}
```

`web/src/components/detail/ApplyBlock.tsx`:

```tsx
"use client";

interface Method {
  kind: string;
  value: string;
  label_en?: string;
  label_dv?: string;
}

const MV = (n: string) => `+960${n.replace(/\D/g, "")}`;

function hrefFor(m: Method): string {
  switch (m.kind) {
    case "email": return `mailto:${m.value}`;
    case "phone": return `tel:${MV(m.value)}`;
    case "viber": return `viber://chat?number=${encodeURIComponent(MV(m.value))}`;
    case "whatsapp": return `https://wa.me/${MV(m.value).replace("+", "")}`;
    default: return m.value;
  }
}

const LABEL: Record<string, string> = {
  form: "Apply via form", portal: "Apply on the portal",
  walk_in: "Apply in person", post: "Apply by post",
};

export function ApplyBlock({ methods }: { methods: Method[] }) {
  if (!methods.length) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold">How to apply</h2>
      <div className="flex flex-wrap gap-2">
        {methods.map((m, i) => (
          <a
            key={i}
            href={hrefFor(m)}
            target={m.kind === "form" || m.kind === "portal" ? "_blank" : undefined}
            rel="noopener noreferrer"
            className="rounded-full border border-line px-3 py-1.5 text-sm
                       hover:border-accent"
          >
            {LABEL[m.kind] ?? m.value}
          </a>
        ))}
      </div>
    </section>
  );
}
```

`Gallery.tsx` and `SpecTable.tsx`: a thumbnail strip with a selected main image, and a two-column `<dl>` over `specs` including non-facetable keys (spec 8.3).

`ReportDialog.tsx`: a `<dialog>` with the five reasons, an optional note, `postReport`, and a fixed confirmation message regardless of outcome — the endpoint always returns 202 and the UI must not leak whether the report was new.

- [ ] **Step 5: Write the detail route**

`web/src/app/documents/[id]/page.tsx` — fetch with `getDocument`, `notFound()` on a 404 (which is what news gets, correctly), then render title, gallery, description, `CompensationTable` for jobs, `SpecTable` for shopping, contacts for property, `ApplyBlock`, source badge, a link to the original, and `ReportDialog`.

- [ ] **Step 6: Run the tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P6 task 6: detail pages, compensation control, apply block"
```

---

### Task 7: Cross-cutting checks

**Files:**
- Create: `web/src/app/search/loading.tsx`, `docs/superpowers/measurements/2026-08-p6-frontend.md`
- Test: `web/src/components/a11y.test.tsx`

**Interfaces:**
- Produces: a recorded Lighthouse and bundle-size table.

- [ ] **Step 1: Write the cross-cutting tests**

`web/src/components/a11y.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetaContext } from "./MetaProvider";
import { ResultList } from "./ResultList";
import {
  jobResult, newsResult, propertyRoomOfThree, shoppingResult,
} from "@/test/fixtures";

const meta = {
  tabs: [], doc_types: [], sorts: [],
  sources: [
    { key: "gazette", label_en: "Gazette", label_dv: "ގެޒެޓް",
      icon: "/sources/gazette.svg", icon_fallback_text: "ގ", accent: "",
      site_url: "https://gazette.gov.mv" },
    { key: "ibay", label_en: "iBay", label_dv: "އައިބޭ",
      icon: "/sources/ibay.svg", icon_fallback_text: "iB", accent: "",
      site_url: "https://ibay.com.mv" },
  ],
};

const all = [jobResult, propertyRoomOfThree, shoppingResult, newsResult];

function renderAll() {
  return render(
    <MetaContext.Provider value={meta as never}>
      <ResultList results={all} queryId={1} />
    </MetaContext.Provider>
  );
}

describe("cross-cutting", () => {
  it("every card carries a source badge", () => {
    renderAll();
    expect(screen.getAllByText(/Gazette|iBay/).length).toBe(all.length);
  });

  it("no card places its badge with a physical left/right property", () => {
    const { container } = renderAll();
    const offenders = Array.from(container.querySelectorAll("*")).filter((el) =>
      /(^|\s)(ml-|mr-|left-|right-|text-left|text-right)/.test(el.className || "")
    );
    // Physical properties do not flip with dir and are the standard way RTL
    // layouts break. Use ms-/me-/start-/end-/text-start/text-end instead.
    expect(offenders.map((e) => e.className)).toEqual([]);
  });

  it("every image has an alt attribute", () => {
    const { container } = renderAll();
    container.querySelectorAll("img").forEach((img) =>
      expect(img).toHaveAttribute("alt")
    );
  });

  it("every heading is an h3 inside a result card, so the page outline is sane", () => {
    renderAll();
    screen.getAllByRole("heading").forEach((h) =>
      expect(h.tagName).toBe("H3")
    );
  });

  it("no external anchor is missing noopener noreferrer", () => {
    const { container } = renderAll();
    container.querySelectorAll('a[target="_blank"]').forEach((a) => {
      expect(a.getAttribute("rel")).toContain("noopener");
      expect(a.getAttribute("rel")).toContain("noreferrer");
    });
  });
});
```

- [ ] **Step 2: Add a loading skeleton**

`web/src/app/search/loading.tsx` — three grey card outlines. The search route is a server component, so without this the tab switch shows the previous page frozen.

- [ ] **Step 3: Measure**

```bash
cd web && npm run build
npx lighthouse http://localhost:3000/search?q=phone --preset=desktop \
    --output=json --output-path=/tmp/lh.json
```

Record first-load JS per route from the build output, LCP, CLS, and TBT.

- [ ] **Step 4: Record**

`docs/superpowers/measurements/2026-08-p6-frontend.md`:

```markdown
# P6 frontend, measured

Date: <fill>

## Bundle

| Route | First load JS | Notes |
|---|---|---|
| / | | |
| /search | | |
| /documents/[id] | | |

## Lighthouse, desktop, /search?q=phone

| Metric | Value | Target |
|---|---|---|
| LCP | | < 2.5s |
| CLS | | < 0.1 |
| TBT | | < 200ms |
| Accessibility | | >= 95 |

## Manual RTL checks

Screenshot each and confirm by eye. These are the cases that automated tests
cannot settle:

- [ ] A results page mixing a Thaana job card and a Latin shopping card. Each
      card's own text direction is correct and neither flips the other.
- [ ] A Thaana card's source badge sits on the correct (right) edge.
- [ ] Typing Thaana into the search box: the caret starts on the right and the
      text does not type backwards.
- [ ] A Thaana suggestion row reads correctly and its icon is on the right.
- [ ] The facet panel with a Thaana value label (`ބީލަން` under announcement
      type) does not break the checkbox alignment.
- [ ] Fili render above the baseline without clipping at the card's line-height.

## Decisions this changes

- [ ] Is the Thaana font file small enough to inline-preload, or does it need
      subsetting to the 49 codepoints the corpus actually uses (spec 6)?
- [ ] Does the shopping grid need virtualization at per_page=20? (It should not.)
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "P6 task 7: cross-cutting checks and measurements"
```

---

## Self-Review

**Spec coverage.** 8 tabs → task 4's `Tabs`, driven entirely by `/meta`. 8.1 jobs → task 3's `JobCard` and task 6's `CompensationTable` + `ApplyBlock`. 8.2 property → `PropertyCard`, with the one-room-of-three test as the named guard. 8.3 shopping → `ShoppingCard` grid plus `SpecTable`; the dynamic facets it will eventually render are P7 and need no change here, because `FacetPanel` already renders whatever ordered list the API returns. 8.4 news → `NewsCard` as a bare outbound anchor with no detail route. 8.5 → `SourceBadge`, its size rule, its label pairing, and the physical-property test. 9 → task 1's generated types and task 4's URL contract. 10 → task 2 entirely, plus the manual RTL checklist. 16.3 click logging → task 5's `onClickCapture` with position.

**Known gaps, deliberate.** Dynamic shopping facets render through the same `FacetPanel` and need no frontend change in P7. The language toggle for UI chrome (spec 10) is deferred to P8 with the rest of the polish — the query language already drives result direction, which is the load-bearing half. Infinite scroll, saved searches and accounts are out of scope by spec 10.

**Type consistency checked.** `SearchState` is the single shape passed between `parseSearchParams`, `toSearchParams`, `FacetPanel`, `Tabs` and `SearchShell`. `ResultOut` from the generated types is what every card takes. `Compensation` in `web/src/lib/compensation.ts` mirrors `enrich/schemas.py`'s field-for-field, and the two test files carry the same cases.

**The one thing to watch.** `estimateNet` is duplicated logic across two languages, which is the kind of thing that drifts silently. It is duplicated because the alternative — a round trip per working-days keystroke — is worse, and the shared test table is the mitigation. If `enrich/compensation.py` changes, `web/src/lib/compensation.ts` and both test files change in the same commit or the change is not done.
