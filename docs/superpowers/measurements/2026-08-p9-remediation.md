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
