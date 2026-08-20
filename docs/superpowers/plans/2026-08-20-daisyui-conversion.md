# DaisyUI Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bespoke component styling with DaisyUI, and replace the cramped "Detail +" text link with a full-width collapsible that can carry body text, specs and identifier links.

**Architecture:** DaisyUI 5 supplies the component vocabulary and one theme mapped from the eight tokens already in `globals.css`. Nothing about direction handling changes: `Bidi` keeps setting `dir` and `lang` per element, and DaisyUI is logical-property-first, so it flips with them.

**Tech Stack:** Next 16.3.1, React 19.2, Tailwind CSS 4 (CSS-first config), DaisyUI 5.7.19 (installed), vitest + testing-library.

**Spec:** none. This is a styling migration of P6's frontend; the design decisions it must preserve are in `docs/superpowers/specs/2026-08-17-search-engine-design.md` sections 8 and 10, and in the docstrings of `Bidi.tsx` and `Disclosure.tsx`.

**Depends on:** P6 (the components being converted). The identifier work (`2026-08-20-identifier-linking.md`) lands its links inside task 1's collapsible, but neither plan blocks the other.

---

## Global Constraints

- **Never `<details>`/`<summary>`.** DaisyUI documents `collapse` on them, and `Disclosure.tsx` rejected them for a measured reason: the native marker does not flip with `dir` and cannot be styled consistently. Adopt DaisyUI's **classes** on a button plus an `aria-controls` region. `DisclosureSpike.tsx` demonstrates the combination and its five tests pass.
- **Logical properties only.** No `left`/`right`, no `ml-`/`mr-`/`pl-`/`pr-`, no `text-left`/`text-right`. Use `ms-`/`me-`/`ps-`/`pe-`/`text-start`/`text-end`. `globals.css` already warns that a physical margin on a card that may be RTL is the single most common way this breaks.
- **Do not touch `Bidi.tsx`.** Every piece of corpus text goes through it, and it is what makes one Thaana result sit beside a Latin one. Converting it is out of scope; components merely keep rendering through it.
- **No JS-dependent DaisyUI patterns.** No `modal`, no `drawer`, no `dropdown` requiring focus tricks. The a11y test asserts no `[role="dialog"]`, no `[aria-modal="true"]` and no `<dialog>` anywhere, and that assertion stays.
- **Every existing test keeps passing, unchanged.** The suite is 122 tests across 18 files. A converted component that needs its test rewritten has changed behaviour, which this migration is not for. If a test genuinely encodes old markup, say so and stop rather than editing it.
- **The Thaana rules in `globals.css` are not DaisyUI's business.** The `@font-face`, the `html` font stack and `[lang="dv"] { line-height: 1.9 }` stay exactly as they are; only the bespoke `.card` rule and the eight raw tokens are replaced.
- **`npx tsc --noEmit` must stay clean.**
- ASCII only in code and comments, except Thaana strings already present in tests.
- Version control is **jj**, not git. Do not commit.

---

## Measured evidence

Established by a spike before this plan was written.

**DaisyUI 5 is logical-property-first**, which was the only real objection. The
collapse chevron is positioned with `inset-inline-end`, not `right`. Across the
built stylesheet:

```
margin-left 0   margin-right 0   padding-left 1   padding-right 1   left: 3   right: 2
margin-inline 12   padding-inline 16   inset-inline 7   border-inline 6
```

The three remaining physical rules are in `.tabs-border:before` and
`.table-pin-cols`, neither of which this app uses.

**Cost.** JS is unchanged at 632,204 bytes -- DaisyUI is CSS-only. CSS goes
17,859 -> 76,368 bytes raw, and the whole DaisyUI sheet gzips to 12,897 bytes, so
the delta is bounded below 12.9 KB against a 632 KB JS payload.

**The palette maps.** All eight tokens survived as a DaisyUI theme:
`--color-primary:#1a6dd6` and friends appear in the built CSS.

**Per-element direction survives.** A spike collapse renders LTR text as `dir="ltr"`
inside a `dir="rtl"` container, and the button/region a11y contract holds.

**The surface being converted:**

| area | files | LOC |
|---|---|---|
| `components/` root | 11 | 525 |
| `components/cards/` | 6 | 415 |
| `components/detail/` | 4 | 218 |
| `components/facets/` | 4 | 194 |
| `app/` | 7 | 85 |

---

## The class mapping

This is the contract for every task below. Where a component's existing classes
are not in this table, leave them: Tailwind utilities and DaisyUI coexist, and
converting for its own sake is how a migration turns into a rewrite.

| current | DaisyUI |
|---|---|
| `.card` (the bespoke rule in globals.css) | `card card-border bg-base-100` |
| `border-line` | `border-base-300` |
| `bg-chip` | `bg-base-200` |
| `text-muted` | `text-base-content/60` |
| `text-accent` | `text-primary` |
| `bg-bg` / `text-fg` | `bg-base-100` / `text-base-content` |
| a chip or tag `<span>` | `badge badge-sm` |
| a submit or action `<button>` | `btn btn-sm` (`btn-primary` for the primary action) |
| a text `<input>` | `input input-sm w-full` |
| a `<select>` | `select select-sm` |
| a checkbox `<input>` | `checkbox checkbox-sm` (with `.label` on the `<label>`; daisyui 5 has no `label-text`) |
| a range `<input>` | `range range-sm` |
| the tab strip | `tabs tabs-box` with `tab` / `tab-active` |
| a `<table>` | `table table-sm` |
| the disclosure | `collapse collapse-arrow border border-base-300` |

---

## Task 1: Theme foundation and the collapsible

**Files:**
- Modify: `web/src/app/globals.css`, `web/src/components/Disclosure.tsx`
- Delete: `web/src/components/DisclosureSpike.tsx`, `web/src/components/DisclosureSpike.test.tsx`
- Test: `web/src/components/Disclosure.test.tsx` (extend, do not rewrite)

- [ ] **Step 1: Fold the spike's theme block into globals.css properly**

The spike already added `@plugin "daisyui"` and a `@plugin "daisyui/theme"` block
mapping the eight tokens. Keep both, and now remove what they replace:

- delete the bespoke `.card { ... }` rule at the end of the file
- delete the six `--color-*` entries from `@theme` (`bg`, `fg`, `muted`, `chip`,
  `line`, `accent`), keeping `--font-sans` and `--font-thaana`
- keep the `@font-face`, the `html` font stack and the `[lang="dv"]` rule
  untouched, and keep the comment explaining why the Thaana face is listed for
  every element

Also drop the `SPIKE, throwaway` comment now that it is not one.

- [ ] **Step 2: Run the suite and expect failures**

Run: `cd web && npx vitest run`
Expected: FAIL. Components still reference `border-line`, `text-muted`,
`text-accent`, `bg-chip`; those classes no longer exist. That failure list is the
work inventory for tasks 2 through 5 -- record it before changing anything else.

- [ ] **Step 3: Promote the spike into the real Disclosure**

Replace `Disclosure.tsx`'s body with the spike's implementation, keeping
`Disclosure`'s name, props and docstring. The docstring must keep explaining why
this is a button and a region rather than `<details>`/`<summary>`, and gain one
sentence saying the DaisyUI classes supply the look while the structure stays.

The component renders full width, the whole header row is the button, and the
chevron sits on the trailing edge via `collapse-arrow`.

- [ ] **Step 4: Extend the Disclosure test**

Append to `Disclosure.test.tsx`, keeping every existing test as it is:

```tsx
it("renders full width rather than as an inline text link", () => {
  const { container } = render(
    <Disclosure label="Details"><p>body</p></Disclosure>
  );
  expect(container.firstElementChild).toHaveClass("collapse");
});

it("has no native details marker to mis-flip under rtl", () => {
  render(<Disclosure label="Details" defaultOpen><p>body</p></Disclosure>);
  expect(document.querySelector("details")).toBeNull();
  expect(document.querySelector("summary")).toBeNull();
});

it("keeps latin text ltr inside an rtl container", () => {
  render(
    <div dir="rtl">
      <Disclosure label="Details" defaultOpen>
        <Bidi text="RoseWare Corporation Pvt Ltd" as="p" />
      </Disclosure>
    </div>
  );
  expect(screen.getByText("RoseWare Corporation Pvt Ltd"))
    .toHaveAttribute("dir", "ltr");
});
```

- [ ] **Step 5: Delete the spike files and confirm**

```bash
rm web/src/components/DisclosureSpike.tsx web/src/components/DisclosureSpike.test.tsx
cd web && npx vitest run src/components/Disclosure.test.tsx && npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
jj commit -m "web: daisyui theme, and a full-width collapsible replacing the Detail link"
```

---

## Task 2: Cards

**Files:** `web/src/components/cards/*.tsx` (CardShell, ResultCard, ShoppingCard, JobCard, PropertyCard, NewsCard)
**Test:** the existing card and `ResultList` tests, unchanged.

- [ ] **Step 1** Convert `CardShell` first -- it owns the container the other five
  render into, so the rest inherit the change. `.card` becomes
  `card card-border bg-base-100`, with `card-body` for the padded interior. Use
  `p-4` or DaisyUI's own body padding, not `padding-inline` hand-rolled.
- [ ] **Step 2** Run `npx vitest run src/components` and fix only what the failures
  name.
- [ ] **Step 3** Convert the four typed cards and `ResultCard` using the mapping
  table. Chips and badges become `badge badge-sm`; muted metadata becomes
  `text-base-content/60`.
- [ ] **Step 4** Check every remaining physical property in the directory:
  `grep -rnE "\b(ml|mr|pl|pr)-|text-(left|right)|margin-(left|right)" src/components/cards`
  must return nothing.
- [ ] **Step 5** `npx vitest run && npx tsc --noEmit`
- [ ] **Step 6** `jj commit -m "web: cards on daisyui"`

---

## Task 3: Search chrome

**Files:** `SearchBox.tsx`, `Tabs.tsx`, `SourceBadge.tsx`, `SearchShell.tsx`, `ResultList.tsx`
**Test:** `SearchBox.test.tsx`, `SourceBadge.test.tsx`, `ResultList.test.tsx`, unchanged.

- [ ] **Step 1** `SearchBox`: the text input becomes `input input-bordered w-full`;
  the submit becomes `btn btn-primary`. Keep the existing form semantics and any
  `aria-label`.
- [ ] **Step 2** `Tabs`: `tabs tabs-box`, each tab `tab`, the active one
  `tab-active`. **Use `tabs-box`, not `tabs-border`** -- `tabs-border`'s
  `:before` is one of the three physical-property rules DaisyUI still ships.
- [ ] **Step 3** `SourceBadge`: `badge badge-sm`, keeping the icon and the
  `icon_fallback_text` path from spec 8.5.
- [ ] **Step 4** `grep` for physical properties as in task 2, step 4.
- [ ] **Step 5** `npx vitest run && npx tsc --noEmit`
- [ ] **Step 6** `jj commit -m "web: search chrome on daisyui"`

---

## Task 4: Facets and the report form

**Files:** `facets/FacetPanel.tsx`, `facets/CheckboxFacet.tsx`, `facets/RangeFacet.tsx`, `facets/ToggleFacet.tsx`, `ReportForm.tsx`
**Test:** the existing facet and report tests, unchanged.

- [ ] **Step 1** `CheckboxFacet`: `checkbox checkbox-sm` with `label` and
  `label-text`. The P6 measurements note that a long Thaana value does not break
  the checkbox alignment -- keep that true, and check it with a Thaana label.
- [ ] **Step 2** `RangeFacet`: `range range-sm`. `ToggleFacet`: `toggle toggle-sm`.
- [ ] ~~**Step 3** `FacetPanel`: group each facet in a `collapse`.~~
  **WRONG, do not do this.** `FacetPanel.test.tsx:26` asserts
  `getAllByRole("heading")` returns `["Brand", "Price", "Has photos"]`, and
  `Disclosure` renders its label as a `<button>`, which deletes those headings.
  Headings are how a screen-reader user navigates a facet panel, so this would
  trade real a11y for collapsing a panel that does not need to collapse. Convert
  its dead classes only and keep the `<section>`/`<h3>` structure.
- [ ] **Step 4** `ReportForm`: `textarea textarea-sm`, `btn btn-sm`. It has no
  `<select>` -- the five reasons are radios, which the mapping table does not
  cover, so leave them. It already uses `Disclosure`; leave that call site alone.
- [ ] **Step 5** `grep` for physical properties; `npx vitest run && npx tsc --noEmit`
- [ ] **Step 6** `jj commit -m "web: facets and report form on daisyui"`

---

## Task 5: Detail components

**Files:** `detail/SpecTable.tsx`, `detail/CompensationTable.tsx`, `detail/ApplyBlock.tsx`, `detail/Gallery.tsx`
**Test:** `ApplyBlock.test.tsx`, `CompensationTable.test.tsx`, unchanged.

- [ ] **Step 1** `CompensationTable` becomes `table table-sm`. **`SpecTable` is a
  `<dl>`, not a `<table>`** -- rewriting a definition list as a table is a
  structural change, not a restyle, so convert only its dead classes. Do not
  introduce `table-zebra`, which fights the muted palette.
- [ ] **Step 2** `ApplyBlock`: each apply method becomes `btn btn-sm`, with
  `btn-primary` on the first. Keep the `apply_kinds` ordering from spec 8.1.
- [ ] **Step 3** `Gallery`: keep whatever it does now. DaisyUI's `carousel` is a
  scroll-snap container and swapping it in is a behaviour change, not a
  restyle -- out of scope. If it uses physical properties, convert those only.
- [ ] **Step 4** `grep` for physical properties; `npx vitest run && npx tsc --noEmit`
- [ ] **Step 5** `jj commit -m "web: detail components on daisyui"`

---

## Task 6: Verify and record

- [ ] **Step 1** Full suite and types:
  ```bash
  cd web && npx vitest run && npx tsc --noEmit
  ```
  Expected: 122 tests plus the three added in task 1, all passing, and no type
  errors.

- [ ] **Step 2** No physical properties anywhere in the converted tree:
  ```bash
  grep -rnE "\b(ml|mr|pl|pr)-[0-9]|text-(left|right)|margin-(left|right)|padding-(left|right)" web/src
  ```
  Expected: no output. Anything found is a bug, not a style preference.

- [ ] **Step 3** No dead tokens or classes left behind:
  ```bash
  grep -rn "border-line\|text-muted\|text-accent\|bg-chip\|bg-bg\|text-fg" web/src
  ```
  Expected: no output.

- [ ] **Step 4** Build and measure against the spike's figures:
  ```bash
  cd web && rm -rf .next && npx next build
  find .next/static -name "*.css" -exec ls -la {} + | awk '{s+=$5} END {print "css", s}'
  find .next/static/chunks -name "*.js" -exec ls -la {} + | awk '{s+=$5} END {print "js", s}'
  ```
  Baseline before DaisyUI: css 17,859, js 632,204. The spike measured css 76,368
  with JS unchanged. A JS increase means a JS-dependent DaisyUI pattern crept in;
  find it.

- [ ] **Step 5** Append to `docs/superpowers/measurements/2026-08-p6-frontend.md`
  a short section: the CSS and JS figures before and after, the DaisyUI version,
  which components were converted, and anything the mapping table could not
  express. If a component was left alone, say which and why -- a migration that
  quietly skips things is worse than one that reports them.

- [ ] **Step 6** `jj commit -m "web: daisyui conversion measured"`

---

## Self-Review

**Coverage.** All 31 non-test components are assigned: task 1 covers globals.css
and Disclosure, task 2 the six cards, task 3 the five chrome components, task 4
the five facet and form components, task 5 the four detail components. `Bidi.tsx`,
`MetaProvider.tsx` and the seven `app/` files carry no bespoke styling to convert
and are deliberately untouched -- `app/layout.tsx` should be checked for stray
palette classes in task 3 and converted there if it has any.

**Placeholder scan.** No TBDs. Task 5 step 3 deliberately declines to convert
`Gallery` and says why.

**The risk this plan takes.** It converts styling across 30 files while asserting
every existing test must pass unchanged. That is the safety property -- the tests
encode behaviour, not markup -- but if several tests do assert on class names, the
migration will stall at task 2 and the right response is to report that, not to
loosen the tests. The class mapping table exists so that conversion is mechanical
enough for this to hold.

**What would make this a bad idea after all.** If task 2 reveals that the cards'
tests assert layout classes heavily, or if `npx tsc --noEmit` starts failing on
DaisyUI's own types, stop and re-open the decision. The spike bounded the cost at
under 13 KB gzipped and zero JS, but it converted two components, not thirty.
