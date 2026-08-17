# P4 enrichment, measured

Date: 2026-08-18
Provider / model: DeepSeek `deepseek-v4-flash` (escalation `deepseek-v4-pro`)
PROMPT_VERSION: 1

## Volume and cost

Dry-run selection counts (no money spent):

| Slice | Documents |
|---|---|
| gazette job | 105 |
| gazette news | 202 |
| gazette property | 5 |
| ibay job | 336 |
| ibay news | 5 |
| ibay property | 3,498 |
| ibay shopping | 16,611 |

iBay's job count (336) matches spec 5.3's ~335 projection; gazette's total
(312) includes the 306 real iulaan plus stray test rows in the dev database.

Live validation sample (3 gazette jobs, `--limit 3`):
ok=2 needs_review=0 failed=0 skipped=1, wall clock 66s. Extracted examples:

| Iulaan | role | basic | allowances | status |
|---|---|---|---|---|
| 407587 | Medical Officer, Dr. Abdul Samad Memorial Hospital | 18,129 (max 20,004) | position 10,382 + 11,456, attendance per-day 281 + 310 | ok |
| 407641 | Supervisor, Stock, State Pharmaceutical | 19,600 | none | ok |

One record was skipped because `build_input` returned None for it (to be
investigated before the full pass).

## Manual calibration, gazette jobs read by hand

Full 25-document calibration not yet performed (see decisions below).

## Drop reasons, by frequency

Not yet gathered from a full pass.

## Attribute coverage, for P5 and P7

Not yet gathered from a full pass.

## Decisions this changes

- [ ] Full cold pass pending: ~20,450 documents, dominated by 16,611 ibay
  shopping listings. At the measured ~20-25 s/document with concurrency 8,
  the full pass is on the order of many hours and roughly the ~$5 the spec
  projected. Decide whether to run it now, off-peak, in slices.
- [ ] The one `skipped` record in the sample needs a look — `build_input`
  returning None for an existing document silently skips enrichment.
- [ ] `--no-transcribe`'s ocr_failed terminal defect (P3, 89 stranded
  attachments) is fixed in P5 Task 0; land that before any transcription
  spend.
