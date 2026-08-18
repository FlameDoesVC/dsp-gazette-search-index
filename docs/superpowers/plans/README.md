# Implementation plans

Spec: `docs/superpowers/specs/2026-08-17-search-engine-design.md`

## Status

| Plan | Phase | State |
|---|---|---|
| `2026-08-17-search-p1-foundation.md` | P1 Foundation | written, landed |
| `2026-08-18-search-p2-dhivehi.md` | P2 Dhivehi pipeline | written, landed |
| `2026-08-18-search-p3-attachments.md` | P3 Attachments | written |
| `2026-08-18-search-p4-enrichment.md` | P4 Enrichment | written |
| `2026-08-18-search-p5-api.md` | P5 API + logging | written |
| `2026-08-18-search-p6-frontend.md` | P6 Frontend | written |
| `2026-08-18-search-p7-facets.md` | P7 Dynamic facets | written |
| `2026-08-18-search-p8-hardening.md` | P8 Hardening | written |

## The measurements each phase produces

Plans P4-P8 were written ahead of their phases, so each one names the
measurement it depends on and where to read it. A plan whose input file is
missing should have that measurement taken first — the numbers decide real
things, and several of them swing costs five-fold.

| Phase produces | Recorded in | Which decides |
|---|---|---|
| P1 — index sizes, p50/p95 at 100k | `measurements/2026-08-p1-load.md` | further partitioning (12.7), P2 ranking budget |
| P2 — the relevance evaluation set | `search/eval/queries.yaml` | every later ranking change |
| P3 — measured CER, scanned fraction, real extracted text | `measurements/2026-08-p3-attachments.md` | P4's pre-extraction regexes; the $113/$153/$333 transcription range |
| P4 — final `attrs`/`card` shapes, `key_raw` frequency | `measurements/2026-08-p4-enrichment.md` | P5's response schemas, P7's `SpecKey` seed list |
| P5 — p95 latency, facet cost, zero-result queries | `measurements/2026-08-p5-api.md` | Meilisearch re-entry (16.4), P8's alias mining |
| P6 — bundle size, Lighthouse, RTL checklist | `measurements/2026-08-p6-frontend.md` | Thaana font subsetting |
| P7 — discovery cost, rejection reasons | `measurements/2026-08-p7-facets.md` | facet thresholds, Meilisearch re-entry |
| P8 — the v2 re-entry table | `measurements/2026-08-p8-hardening.md` | pgvector (16.1), learning to rank (16.2) |

**P8 must not start until the API has served real traffic for at least a week.**
Four of its six tasks read `QueryLog`, and tuning against an empty table
produces a ranking fitted to nothing.

## Cross-plan contract

These are the names later plans depend on. Changing one is a breaking change
across phases, so change it here and in every plan that references it.

**From P1 (`search/adapters/base.py`):**

```python
RawDocument(source: str, source_key: str, payload: dict)

DocumentDraft(
    source, source_key, doc_type, url,
    title_en="", title_dv="", title_latin="",
    summary_en="", summary_dv="",
    text_en="", text_dv="", text_latin="",     # consumed for vectors, never stored
    price=None, currency="MVR", location="", island="", atoll="",
    published_at=None, expires_at=None, is_active=True,
    attrs={}, card={}, thumbnails=[], quality=0.0, content_hash="",
)

class SourceAdapter(Protocol):
    key: str
    def iter_source_keys(self, **filters) -> Iterator[str]: ...
    def fetch_raw(self, source_key: str) -> RawDocument | None: ...
    def to_document(self, raw: RawDocument) -> DocumentDraft | None: ...

register(adapter) / get_adapter(key) / all_adapters()
```

**From P1 (`search/indexing.py`):**

```python
upsert_drafts(drafts: Iterable[DocumentDraft]) -> int
reindex_source(key, *, limit=None, only_stale=False, batch_size=500, **filters) -> int
apply_overlays(draft) -> DocumentDraft        # added by P4
```

**From P1/P5 (`search/query.py`):**

```python
SearchResult(id, source, source_key, doc_type, url, title, summary, card,
             score, matched_lang, thumbnails)
search(q, *, doc_type=None, limit=20, candidate_limit=500) -> list[SearchResult]

# P5:
SearchPage(results, total, facets, plan)      # + relaxations, suggestions in P8
search_page(q, *, doc_type=None, filters=(), sort="relevance",
            page=1, per_page=20, candidate_limit=None) -> SearchPage
compute_facets(doc_type, filters, params, fsql) -> list[dict]
```

**From P2 (`search/lang/`):** `normalize_dv`, `strip_fili`, `decode_keys`,
`looks_like_keys`, `translit_dv_to_latin`, `translit_latin_to_dv`,
`detect_script`, `build_query_plan` -> `QueryPlan`.

**From P3 (`gazette/models.py` + `search/extract/`):** `Attachment`,
`extract_attachment(attachment) -> ExtractionResult`.

**From P4 (`enrich/`):**

```python
EnrichedRecord                                  # keyed (source, source_key)
ATTRS_FOR_TYPE: dict[str, type[BaseModel]]      # job/property/shopping/news
estimate_net(comp, working_days=None) -> NetEstimate | None
salary_display(comp) -> str
extract_candidates(text) -> Candidates
ground(raw_attrs, *, doc_type, source_text, candidates, scraped) -> (model, report)
build_card(doc_type, attrs_model, *, base) -> dict
apply_enrichment(draft) -> DocumentDraft        # registered in SEARCH_DRAFT_OVERLAYS
```

**From P5 (`search/` + `api/`):**

```python
FacetDef, FACETS: dict[str, list[FacetDef]], facet_def(doc_type, key)
Filter, parse_filters(raw, doc_type) -> list[Filter], filter_sql(filters)
QueryLog, ClickLog, DocumentReport, SuggestTerm
log_query(request, *, raw, plan, doc_type, filters, result_count, latency_ms) -> int | None
log_click(query_id, document_id, position) -> None
```

**From P7 (`search/specs/`):**

```python
SpecKey, DocumentSpec
unit_vocabulary() -> list[str]
specs_for_document(doc) -> list[dict]
discover_facets(cte, params, cursor, *, max_facets=8) -> list[dict]
```

## Invariants every plan must hold

Spec 3.1, 5.7, 8, 12.1, 12.2, 12.4:

- Identity is `(source, source_key)`. `doc_type` is mutable and never in a
  unique constraint or partition key.
- `SearchDocument` is partitioned by `source`. FKs pointing at it need
  `db_constraint=False`.
- Body text is never stored on `SearchDocument` — vectors only.
- Streaming uses `.iterator(chunk_size=500)` on the `direct` alias.
- Nothing time-dependent goes in `card`. Raw dates only; `deadline_state`,
  freshness and relative time are computed per request.
- Gazette is write-once. `prompt_version` bumps never backfill it.
- `stale_marked_at` is the one reprocess trigger, and **only `reindex` clears
  it** — it is the last stage in the chain. P3's and P4's `--stale` commands
  read it and must leave it set.
- The model never does arithmetic and never transcribes a digit the regex
  pre-extractor did not find.
- Version control is jj, not git.

## Scheduled jobs

The full operational surface, assembled in P8 as `docs/RUNBOOK.md`:

| Command | Cadence | Consequence of missing it |
|---|---|---|
| `create_log_partitions --months 3` | monthly | rows land in DEFAULT; retention cannot drop them cheaply |
| `prune_logs --days 90` | monthly | query text retained past its window (16.3) |
| `rebuild_suggest_terms` | after each full reindex | autocomplete goes stale |
| `sync_specs --source ibay --prune` | after each shopping reindex | facets reflect old attributes |
| `translate_fields` | weekly | new documents show only one language |
| `archive_documents --days 365` | monthly, off-peak | working set grows into shared_buffers |
| `mine_aliases --days 30` | monthly, review before `--apply` | zero-result queries stay zero-result |
| `eval_search` | before and after any ranking change | a regression ships unnoticed |

## The reprocess chain

`stale_marked_at` is the single trigger and only the last stage clears it, so
the order is fixed:

```sql
UPDATE search_searchdocument SET stale_marked_at = now() WHERE <slice>;
```

```bash
python manage.py extract_attachments --stale     # P3, costs money
python manage.py enrich_documents --stale        # P4, costs money
python manage.py reindex --stale                 # clears the flag
```

Running `reindex --stale` first clears the flag and the paid stages then find
nothing. Every command in the chain reports its count before spending.

## Scheduled jobs

| Command | Cadence | Why |
|---|---|---|
| `create_log_partitions --months 3` | monthly | Log tables are RANGE-partitioned; a missed run lands rows in DEFAULT, where retention cannot drop them cheaply. |
| `prune_logs --days 90` | monthly | Raw query text expires (spec 16.3). |
| `rebuild_suggest_terms` | after each full reindex | The term table is derived from titles. |
