# Pipeline runbook

Every stage below is a separate management command. `sync_all` only writes raw
source rows; nothing downstream is triggered by it. All commands take
`venv/bin/python manage.py ...` from the repo root.

Two stages spend money and are marked COSTS. Everything else is free and
idempotent, so re-running it is always safe.

## Why the order is what it is

`reindex` is the publish step: it reads the source tables, runs the two draft
overlays (`enrich.overlay.apply_enrichment` then `catalog.overlay.apply_entity`,
settings.py:305), and upserts `SearchDocument`. An overlay can only publish data
that already exists, so anything that produces overlay data has to run before
the reindex that publishes it.

`resolve_entities` reads `SearchDocument`, so it needs a reindex before it. Its
output is published by a reindex after it. That is the one place the pipeline
doubles back, and it is why link counts and indexed `attrs.entity_id` counts
drift apart when only one reindex has run.

## The full order

    1  sync_all                       ingest, free, already running
    2  extract_attachments            gazette PDFs, COSTS (Vision)
    3  reindex                        publish raw -> SearchDocument
    4  sync_specs                     SearchDocument.attrs -> DocumentSpec
    5  enrich_documents               COSTS (DeepSeek), per source and type
    6  resolve_entities               deterministic grouping, free
    7  eval_entities                  precision gate, must clear 90%
    8  build_profiles                 COSTS (DeepSeek), one call per entity
    9  reindex                        republish, now with overlay data
    10 fill_bilingual                 fill the missing language side
    11 rebuild_suggest_terms          autocomplete table
    12 fill_entity_translations       office and iulaan-type names, once

Steps 10 and 11 are last because step 9 overwrites them. `reindex` rewrites
`title_dv`, `summary_dv`, `contact_phone` and `dedupe_key` from the adapter plus
overlays (`_UPDATE_FIELDS`, search/indexing.py), so a `fill_bilingual` run that
happens before a reindex is discarded by it.

## Commands, in order

### 1. Ingest

    venv/bin/python manage.py sync_all

Stops after `STOP_AFTER_SEEN_PAGES` consecutive already-seen pages. Pass
`--full` to `sync_gazette` / `sync_ibay` to override.

The cycle ends with a bounded stale-refresh pass over products older than
`IBAY_STALE_DAYS` (default 7), oldest first, `IBAY_STALE_BATCH_LIMIT` per cycle.
If the log reports the same stale backlog every cycle, the refresh is not
clearing staleness; see `tests/sync/test_stale_refresh.py` for the failure mode
that caused it once already.

### 2. Gazette attachments (COSTS: Google Vision)

    venv/bin/python manage.py extract_attachments --limit 20      # probe first

Discovers attachment references and then fetches and extracts each one. OCR
results are cached on disk (`OCR_CACHE_DIR`), so a re-run of an unchanged file
is free. Only needed for gazette.

### 3. Publish the index

    venv/bin/python manage.py reindex                    # all sources
    venv/bin/python manage.py reindex --source gazette   # one source

Always runs on the `direct` alias. Never point it at a pooled alias; streaming
needs server-side cursors.

### 4. Project specs

    venv/bin/python manage.py sync_specs --source ibay --prune

Reads `SearchDocument.attrs` plus scraped `ProductInfo` into `DocumentSpec`,
which is what faceting and the catalog's brand signal read.

### 5. Enrichment

This runs on a LOCAL model by default and costs nothing. The `.env` settings
that make it so are gitignored, so they are recorded here:

    ENRICH_PROVIDER=ollama
    ENRICH_MODEL_LOCAL=mistral:latest
    ENRICH_LOCAL_NUM_CTX=16384
    OLLAMA_URL=http://10.0.0.104:11434

With `ENRICH_PROVIDER=ollama` the ladder in `enrich/client.py::_stages` is two
local attempts and no DeepSeek stage at all, so a pass cannot spend money by
accident. That is the point of the setting, more than the speed. Switching back
to `deepseek` restores the paid ladder with a local last resort.

Model choice, measured one at a time with nothing else on the GPU and the
weight-load call excluded from the timing:

| Model | s/doc | parsed | 33,925 docs |
|---|---|---|---|
| mistral:latest | 3.93 | 16/16 | 38h |
| gemmatranslate:12b | 6.34 | 16/16 | 60h |
| qwen2.5:7b | 13.13 | 0/16 | returns `keywords` as a string |

Against the same 16 documents DeepSeek had done: brand agrees 9 / differs 0 for
both local models, condition 12/0 for mistral against 12/1 for gemmatranslate.
mistral omits `delivery` more often, which is the field worth least.

`gemma4:12b` is not a candidate: its thinking cannot be turned off.

Validated on the nested schemas too, 8 property and 6 job documents: 14 of 14
parsed, 0 flagged for review, and a single-room offering came back
`unit=room rooms_offered=1` rather than as a one-bedroom flat. Two things to
know: mistral returned `compensation.basic_salary_max` as an object once in six
job documents and the validator dropped it, losing a real salary (job is 367 of
33,925 documents); and `location` is dropped as `not_grounded` on most documents
because the model echoes the SCRAPED block back and `location` is not in any
attrs schema. The second is harmless noise, not data loss.

### 5b. Enrichment on DeepSeek (COSTS money)

Count before spending:

    venv/bin/python manage.py enrich_documents --source ibay --type shopping --dry-run

Then run a slice at a time:

    venv/bin/python manage.py enrich_documents --source ibay --type shopping
    venv/bin/python manage.py enrich_documents --source ibay --type property
    venv/bin/python manage.py enrich_documents --source ibay --type job
    venv/bin/python manage.py enrich_documents --source gazette --type job
    venv/bin/python manage.py enrich_documents --source gazette --type news

`--force` re-enriches records that already succeeded; `--stale` picks up only
documents whose text changed. Neither is needed on a first pass.

### 6. Resolve entities (free)

    venv/bin/python manage.py resolve_entities --source ibay --dry-run
    venv/bin/python manage.py resolve_entities --source ibay

Reports `seen / linked / missed`. Deterministic: same corpus, same grouping.

### 7. The precision gate

Re-resolving assigns new entity keys, which invalidates the labels in
`catalog/eval/golden.yaml` because each row records the entity that listing
resolved to at sample time. After any change to `catalog/identity.py`, re-sample
and re-label:

    venv/bin/python manage.py eval_entities --sample 50    # writes rows to label
    venv/bin/python manage.py eval_entities                # scores them

Below 90% precision the command says "do not backfill profiles yet". Take it
literally: a wrong link puts wrong specs on a real listing and no later stage
can detect it.

### 8. Profiles (one call per entity; free on the local provider)

    venv/bin/python manage.py build_profiles --kind service --dry-run
    venv/bin/python manage.py build_profiles --kind service --limit 50

Ordered by `listing_count` descending, so a `--limit` run buys the entities that
cover the most listings. Skips entities already profiled at the current
`PROFILE_PROMPT_VERSION` unless given `--force`.

Bumping `PROFILE_PROMPT_VERSION` re-selects every entity, and so does a
re-resolve that changes entity keys. Both mean running it all again -- and on
2026-08-20 the identity work did exactly that, taking every existing profile
with it (`EntityField` went to 0).

Profile prompts are about 20,200 characters, roughly 2.2x what a 4,096-token
local window holds: 30 of 30 sampled prompts overflowed, for both entity kinds.
`ENRICH_LOCAL_NUM_CTX=16384` is what makes them fit, and it is free on mistral
(4.00s/doc at 16k against 3.93s at 4k). Without it ollama truncates each prompt
silently and `enrich/client.py` logs a warning saying so.

Worth knowing if the size ever needs cutting: `build_profile_input` caps
`union_text` at `ENRICH_MAX_INPUT_CHARS`, but `build_profile_messages` is handed
the UNCAPPED `listings` list, so that cap bounds nothing that reaches the model.
The real lever is `CATALOG_PROFILE_MAX_LISTINGS`.

### 9. Republish

    venv/bin/python manage.py reindex

This is the step that makes entity profiles visible to search and to the
frontend.

It publishes nothing for an unprofiled entity, and that is deliberate rather
than a bug: `catalog/overlay.py::apply_entity` returns the draft untouched
unless `profile_status` is `ok` or `needs_review`. So resolving 18,424 links and
then reindexing wrote `attrs.entity_id` on zero documents, because every entity
was still `pending`. Reindexing to close a gap between resolved links and
indexed entity ids does not work; step 8 is what closes it.

Worth deciding rather than assuming: a link with no profile still knows that 34
other listings are the same product, and `listing_count` needs no model call. If
the frontend should show groupings before any profiling spend, the overlay has
to publish the link-derived fields separately from the profile-derived ones.

### 10-12. Search surface

    venv/bin/python manage.py fill_bilingual --dry-run
    venv/bin/python manage.py fill_bilingual
    venv/bin/python manage.py rebuild_suggest_terms
    venv/bin/python manage.py fill_entity_translations

## Free in-place repairs

These recompute one field across the corpus without a reindex. Use them after
changing the relevant logic; they are not part of the normal order because
`reindex` already computes all three.

    venv/bin/python manage.py map_categories --source ibay     # after taxonomy edits
    venv/bin/python manage.py backfill_phones                  # after contacts.py edits
    venv/bin/python manage.py dedupe_listings                  # after dedupe key edits
    venv/bin/python manage.py reindex_vectors                  # tsvector repair only

## One-time seeds (already applied)

`seed_taxonomy --apply`, `seed_spec_keys`, `seed_brands`. Re-run `seed_taxonomy`
after enough new source categories appear; it never overwrites curation.

## Maintenance

    venv/bin/python manage.py create_log_partitions --months 3
    venv/bin/python manage.py prune_logs --days 90
    venv/bin/python manage.py apply_proposals

`apply_proposals` is a safety net only. The correction endpoint evaluates quorum
inline, so this catches proposals stranded by a crash and reports the conflicted
backlog.

## Resolve depends on a fresh reindex

Not obvious and it cost a wrong conclusion once. `resolve_document` keys a
SERVICE entity on the provider's phone, which it reads from
`SearchDocument.contact_phone` -- and that column is computed by `reindex`. On
2026-08-20 a reindex populated it on 31,836 of 34,331 documents and the next
resolve pass jumped from 18,424 links to 23,271, with service links reaching
12,208. Nothing about identity had changed. So run step 3 before step 6, not
just after it.

## State as of 2026-08-20, after the iBay pass

| Table | Rows | Meaning |
|---|---|---|
| ibay.Product | 34,331 | raw, sync healthy |
| SearchDocument (ibay) | 34,331 | indexed |
| SearchDocument (gazette) | 5 | step 3 still never run for gazette |
| gazette.Iulaan | 186 | raw, invisible to search |
| DocumentSpec | populated | step 4 done |
| SuggestTerm | 10,088 | step 11 done, was 0 |
| EntityLink | 22,869 | step 6 done |
| Entity | 9,144 | 6,878 product, 2,266 service |
| ... with attrs.entity_id | 18,424 | published by step 9 without profiles |
| ... rendering as service cards | 9,738 | was 0; all showed product fields |
| EnrichedRecord | 51 | step 5 barely started |
| EntityField | 0 | step 8 never run since the re-key |

Entity precision is 92.68% (38 of 41 linked) against the 90% gate, coverage
82.00%, all 50 golden rows labelled. Read that as "no longer clearly failing"
rather than a precise figure: at 41 linked rows the gap between 87.8% and 92.68%
is about two rows. What is solid is that the systematic failure behind it -- a
case and a screen protector sharing an entity, 4% of multi-listing product
entities -- is gone, and the three that remain have named causes in
`catalog/eval/golden.yaml`.

The gazette rows are the honest gap. 186 iulaan are ingested and not indexed,
which makes every gazette-side feature -- identifier linking, spelling
correction -- untestable end to end until step 3 runs for it.
