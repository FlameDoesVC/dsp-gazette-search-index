# P6 frontend, measured

Date: 2026-08-18
Stack: Next.js 16.3.1 (App Router, Turbopack), React 19.2, Tailwind CSS 4,
vitest + @testing-library/react. 109 unit/component tests green.

## Bundle

Built with `npm run build` (Turbopack; this build does not break first-load JS
out per route the way the old webpack output did).

| Route | First load JS | Notes |
|---|---|---|
| / | static, ~0 client JS | server-action form only |
| /search | ~592 KB across 4 chunks, largest 224 KB | includes React + all four cards + facets |
| /documents/[id] | shares the same chunk set | lazy-loaded cards not yet split |

The 592 KB figure is the total of `.next/static/chunks/*.js` (uncompressed);
over HTTP gzip this is roughly 40% of that. Splitting the four cards into
separate chunks so a shopping search does not load the job compensation code
is a cheap follow-up when the bundle starts to matter.

## Lighthouse, desktop, /search?q=phone

Ran against the standalone server with headless chromium.

| Metric | Value | Target |
|---|---|---|
| Performance | 85 | - |
| LCP | 1.1 s | < 2.5s |
| CLS | 0.251 | < 0.1 |
| TBT | 0 ms | < 200ms |
| Accessibility | 95 | >= 95 |
| Best practices | 96 | - |
| SEO | 91 | - |

CLS is over budget. No per-element breakdown was available from this run; the
prime suspects are (a) the `loading.tsx` skeleton swapping to taller real
content, and (b) third-party product images entering the viewport. The cards
already reserve space (`aspect-square`, fixed hero dims), so (b) should be
small -- investigate (a) first. Note this as a P8 polish item.

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

- [x] Is the Thaana font file small enough to inline-preload, or does it need
      subsetting to the 49 codepoints the corpus actually uses (spec 6)?
      The MV Faseyha woff2 is 18.6 KB -- small enough to inline-preload as-is;
      no subsetting needed.
- [x] Does the shopping grid need virtualization at per_page=20? No -- 20 cards
      is trivial to render.
- [ ] CLS over budget: investigate the loading-skeleton swap before P8.
