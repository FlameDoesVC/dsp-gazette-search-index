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

### 5. Enrichment (COSTS: DeepSeek)

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

### 8. Profiles (COSTS: DeepSeek, one call per entity)

    venv/bin/python manage.py build_profiles --kind service --dry-run
    venv/bin/python manage.py build_profiles --kind service --limit 50

Ordered by `listing_count` descending, so a `--limit` run buys the entities that
cover the most listings. Skips entities already profiled at the current
`PROFILE_PROMPT_VERSION` unless given `--force`.

Bumping `PROFILE_PROMPT_VERSION` re-selects every entity, and so does a re-resolve
that changes entity keys. Both mean paying again.

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

## State as of 2026-08-20

| Table | Rows | Meaning |
|---|---|---|
| ibay.Product | 34,331 | raw, sync healthy |
| gazette.Iulaan | 186 | raw |
| gazette.Attachment | 0 | step 2 never run |
| SearchDocument (ibay) | 33,917 | indexed |
| SearchDocument (gazette) | 5 | step 3 never run for gazette |
| DocumentSpec | 98,753 | populated |
| EnrichedRecord | 0 | step 5 never run past a 3-doc probe |
| Entity | 7,287 | 5,495 product, 1,792 service |
| EntityLink | 18,417 | resolved |
| SearchDocument with entity_id | 9,269 | step 9 owed |
| EntityField | 0 | step 8 never run, or wiped by re-resolve |
| SuggestTerm | 0 | step 11 never run |

Three of these are worth calling out.

`EntityField: 0` means every profile built before the identity fix is gone.
Re-resolving created entities under new keys, and the old rows went with the old
keys. Any profiling spend before an identity change is spend that has to be
repeated.

`SearchDocument (gazette): 5` is not a bug in the gazette adapter. It works;
`reindex --source gazette --limit 5` wrote those 5 rows on 2026-08-20 as a check.
Nobody had run it, so 186 iulaan are invisible to search.

`EnrichedRecord: 0` is consistent with the P4 measurements, which recorded a
3-document live validation and a dry-run count, never a full pass.
