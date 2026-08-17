# Implementation plans

Spec: `docs/superpowers/specs/2026-08-17-search-engine-design.md`

## Why plans are written just-in-time

Each phase produces a measurement that decides the next. Writing every plan up
front converts those measurements into decoration:

| Phase produces | Which decides |
|---|---|
| P1 Task 11 — index sizes, p50/p95 at 100k | further partitioning (spec 12.7), P2 ranking budget |
| P2 — the relevance evaluation set | every later ranking change |
| P3 — measured CER, real extracted gazette text | P4's deterministic pre-extraction regexes |
| P4 — final `attrs` and `card` shapes | P5's response schemas |
| P5 — query and click logs | P7's `SpecKey` priority curation |

So a plan is written when the phase before it has landed, not before.

## Status

| Plan | Phase | State |
|---|---|---|
| `2026-08-17-search-p1-foundation.md` | P1 Foundation | written |
| `2026-08-18-search-p2-dhivehi.md` | P2 Dhivehi pipeline | written |
| `2026-08-18-search-p3-attachments.md` | P3 Attachments | written |
| — | P4 Enrichment | write after P3 lands |
| — | P5 API + logging | write after P4 lands |
| — | P6 Frontend | write after P5 lands |
| — | P7 Dynamic facets | write after P5 has logs |
| — | P8 Hardening | write after P6 lands |

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
```

**From P1 (`search/query.py`):**

```python
SearchResult(id, source, source_key, doc_type, url, title, summary, card, score)
search(q, *, doc_type=None, limit=20, candidate_limit=500) -> list[SearchResult]
```

**From P2 (`search/lang/`):** `normalize_dv`, `strip_fili`, `decode_keys`,
`looks_like_keys`, `translit_dv_to_latin`, `translit_latin_to_dv`,
`detect_script`, `build_query_plan` -> `QueryPlan`.

**From P3 (`gazette/models.py` + `search/`):** `Attachment`,
`extract_attachment(attachment) -> ExtractionResult`.

**Invariants every plan must hold** (spec 3.1, 12.1, 12.2, 12.4):

- Identity is `(source, source_key)`. `doc_type` is mutable and never in a
  unique constraint or partition key.
- `SearchDocument` is partitioned by `source`. FKs pointing at it need
  `db_constraint=False`.
- Body text is never stored on `SearchDocument` — vectors only.
- Streaming uses `.iterator(chunk_size=500)` on the `direct` alias.
- Version control is jj, not git.
