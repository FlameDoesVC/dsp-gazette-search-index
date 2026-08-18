# P9 remediation, measured

Date: 2026-08-18
Provider: `gemmatranslate:12b` on the GPU host (10.0.0.104:11434)

## Task 1: batched translation

Mechanism verified: a single numbered call of 6 titles returned parseable
output in 2.3s (0.38s/title) against ~1.0s/title sequential. The model's
numbered-reply format parses correctly; it emits a source line followed by an
indented numbered translation, and `_parse_numbered` keeps the last occurrence
per number, which is the translation.

Speedup is host-load-dependent. A 24-title bench run while the P5 Task 0C
backfill was hammering the same GPU host reported `sequential 23.5s / batched
77.5s` (0.3x) -- batch requests queued behind the single-call flood. Re-measure
off-peak; the mechanism (one call per 8 titles, cache per item, fallback on
misalignment) is the deliverable and is covered by tests.

## Task 4: category-aware ranking (measured evidence)

`iphone` restricted to `doc_type=shopping`, top 12 had zero phones of 1,160
matching documents. Real phones ranked 13, 30, 38. Score decomposition:

```
CHARGER     r_en=12.2  trigram=0.097  quality=1.00  freshness=0.000
REAL PHONE  r_en=1.8   trigram=0.123  quality=1.00  freshness=0.000
```

`ts_rank_cd` dominates 7x and no ts_rank flag fixes it -- "iPhone 15 phone
cover" is a perfect text match for "iphone". The fix must use non-lexical
signal: `attrs.category_path` and `price`, both already in the database.

## Pending

Task 2 (query-side translation), Task 3 (translation out of sync), Task 5
(dedupe), Task 6 (keyword penalty), Task 8 (backfills) -- fill in as they land.

## Task 3: translation out of sync

3-page sync (`GAZETTE_MAX_INDEX_PAGES=3`), live gazette site:

| | Wall clock | Fetched |
|---|---|---|
| baseline (with inline title+body translation) | ~4.5 min | 30 |
| after removal | **1.4s** | 5, 0 failed |

Translation is deferred to `fill_bilingual` (batched after Task 1). Body
translation is gone; `translated_body` stays on the model, unpopulated.

## Task 4: category-aware ranking

Live corpus, after the `category_leaf` column + page-1 interleave landed:

`iphone` (doc_type=shopping, per_page=10) top-10 leaf categories:
`Charger, LCD Screen & Digitizer, Charger, Headset - Wired, Headset - Wired,
Charger, Charger, Charger, Screen Protection, Mobile Phones` -- **Mobile
Phones now appears on page one** (was: zero phones in the top 12, phone ranked
13/30/38).

4b (curated accessory demotion) is superseded by P10 task 1 per the plan's
amendment; 4a's diversity cap is the landed mechanism. Eval cases added:
`iphone -> phone`, `iphone case -> case`, `iphone charger -> charger` with
accessory fixtures that outmatch the phone lexically, so the regression cannot
return silently.

## Task 5: dedupe listings

| | Before | After |
|---|---|---|
| live iBay docs | 20,445 | 12,486 |
| flagged duplicates | 0 | 7,959 |
| groups | - | 1,451 |

The plan measured 1,808 groups / 8,089 rows from a raw-title count; the
`dedupe_key` (seller + normalized title + price) lands slightly lower because
it splits on seller/price. Still ~40% of the corpus. `room daily rent`
(previously hundreds of copies) now returns one survivor per group.

`dedupe_listings` added to the scheduled-jobs runbook -- must run after every
reindex.

## Task 8: backfills and final table

| Fix | Before | After |
|---|---|---|
| translation throughput | 0.9s/title sequential | batched, 1 call / 8 titles (see Task 1; host-load dependent) |
| 3-page sync wall clock | 4m30s | 1.4s |
| `iphone` phones in top 10 | 0 | 1 (rank 10) |
| "room daily rent" total | hundreds of copies | 1 survivor per group |
| duplicate rows | 8,089 (raw titles) | 7,959 flagged (seller+title+price key) |
| documents missing title_dv | 19,890 | in-flight backfill (TranslationCache-backed, so re-runs are cheap) |
| recall@5 (eval gate) | 0.80 floor | held (711 tests green) |

Pending as background jobs: `fill_bilingual` full pass (multi-hour, GPU-serial);
`extract_attachments --no-transcribe` re-measure of the scanned fraction at the
current corpus size (the 40.3% figure predates the corpus growing to 1,245+).
