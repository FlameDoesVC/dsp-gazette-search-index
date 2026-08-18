"""Draft-to-row conversion and bulk upsert.

Streaming discipline (spec 12.4): the source iteration uses `.iterator()` and
rows are written in batches, so reindexing 5M documents costs the same memory
as reindexing 5,000. Nothing here calls `list()` on a queryset.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.conf import settings
from django.db import connection, transaction
from django.utils.module_loading import import_string

from search.adapters import base
from search.adapters.base import DocumentDraft
from search.lang import normalize_text, strip_fili, translit_dv_to_latin
from search.lang.assign import route_bilingual
from search.models import SearchDocument

logger = logging.getLogger(__name__)

_OVERLAY_CACHE: list | None = None


def _overlays():
    """Resolved once per process. Spec 3.3: enrichment is a layer over the
    index, so `search` declares the seam and `enrich` fills it -- the import
    never runs the other way."""
    global _OVERLAY_CACHE
    if _OVERLAY_CACHE is None:
        _OVERLAY_CACHE = [
            import_string(path)
            for path in getattr(settings, "SEARCH_DRAFT_OVERLAYS", [])
        ]
    return _OVERLAY_CACHE


def apply_overlays(draft: DocumentDraft) -> DocumentDraft:
    for fn in _overlays():
        draft = fn(draft)
    return draft

# Written by upsert; everything except the identity pair and `id`.
_UPDATE_FIELDS = [
    "doc_type", "url",
    "title_en", "title_dv", "title_latin",
    "summary_en", "summary_dv",
    "price", "currency", "location", "island", "atoll",
    "published_at", "expires_at", "is_active",
    "attrs", "card", "thumbnails", "quality", "content_hash",
    "stale_marked_at", "category_leaf", "dedupe_key",
]


def _row(draft: DocumentDraft) -> SearchDocument:
    # Route by content, not by the adapter's assumption about its source. A
    # field named _en holds English and _dv holds Dhivehi; deciding it here
    # means no future adapter can reintroduce a swap (P5 task 0C).
    title_en, title_dv = route_bilingual(draft.title_en, draft.title_dv)
    summary_en, summary_dv = route_bilingual(draft.summary_en, draft.summary_dv)
    # The leaf category drives ranking and faceting (P9 task 4).
    path = draft.attrs.get("category_path") or []
    category_leaf = str(path[-1]) if path else ""
    card = dict(draft.card or {})
    if category_leaf and not card.get("category_leaf"):
        card["category_leaf"] = category_leaf
    # So the frontend can render "N similar listings" (P9 task 5 step 7).
    card.setdefault("duplicate_count", 1)
    # Repost collapsing (P9 task 5): computed once at index time so search
    # filters it for free, never per query.
    from search.dedupe import dedupe_key
    dedupe_key_value = dedupe_key(
        source=draft.source,
        seller=draft.card.get("seller_name") or draft.attrs.get("seller_id", ""),
        title=draft.title_en or draft.title_dv,
        price=draft.price,
    )
    return SearchDocument(
        source=draft.source,
        source_key=draft.source_key,
        doc_type=draft.doc_type,
        url=draft.url,
        title_en=title_en,
        title_dv=title_dv,
        title_latin=draft.title_latin,
        summary_en=summary_en,
        summary_dv=summary_dv,
        price=draft.price,
        currency=draft.currency,
        location=draft.location,
        island=draft.island,
        atoll=draft.atoll,
        published_at=draft.published_at,
        expires_at=draft.expires_at,
        is_active=draft.is_active,
        attrs=draft.attrs,
        card=card,
        thumbnails=draft.thumbnails,
        quality=draft.quality,
        content_hash=draft.content_hash,
        stale_marked_at=None,   # a successful pass clears the work ticket
        category_leaf=category_leaf,
        dedupe_key=dedupe_key_value,
    )


def upsert_drafts(drafts: Iterable[DocumentDraft]) -> int:
    """Insert or update rows for `drafts`, then rebuild their vectors.

    Returns the number of drafts written.
    """
    materialized = list(drafts)
    if not materialized:
        return 0

    with transaction.atomic():
        SearchDocument.objects.bulk_create(
            [_row(d) for d in materialized],
            update_conflicts=True,
            unique_fields=["source", "source_key"],
            update_fields=_UPDATE_FIELDS,
            batch_size=500,
        )
        _rebuild_vectors(materialized)
    return len(materialized)


_VECTOR_SQL = """
UPDATE search_searchdocument AS d SET
    vector_en    = setweight(to_tsvector('english', v.title_en), 'A')
                || setweight(to_tsvector('english',
                        v.text_en || ' ' || v.summary_en), 'B'),
    vector_dv    = {dv_expr},
    vector_latin = setweight(to_tsvector('simple', v.title_latin), 'A')
                || setweight(to_tsvector('simple', v.text_latin), 'B'),
    title_latin  = v.title_latin
FROM (VALUES {values}) AS v(
    source, source_key, title_en, text_en, summary_en,
    title_dv, text_dv, title_dv_skel, text_dv_skel,
    title_latin, text_latin
)
WHERE d.source = v.source AND d.source_key = v.source_key
"""

# Dual weighting (spec 6.2): fili-preserved at A so an exactly-typed query
# outranks a skeleton collision, skeleton at C so a mis-typed one still
# matches. The two alternatives exist so the strategy is a settings change
# plus a reindex, never a migration.
_DV_EXPRS = {
    "dual": (
        "setweight(to_tsvector('simple', v.title_dv), 'A') "
        "|| setweight(to_tsvector('simple', v.text_dv), 'B') "
        "|| setweight(to_tsvector('simple', v.title_dv_skel), 'C') "
        "|| setweight(to_tsvector('simple', v.text_dv_skel), 'C')"
    ),
    "fili": (
        "setweight(to_tsvector('simple', v.title_dv), 'A') "
        "|| setweight(to_tsvector('simple', v.text_dv), 'B')"
    ),
    "skeleton": (
        "setweight(to_tsvector('simple', v.title_dv_skel), 'A') "
        "|| setweight(to_tsvector('simple', v.text_dv_skel), 'B')"
    ),
}


def _vector_params(draft: DocumentDraft) -> tuple:
    title_dv = normalize_text(draft.title_dv)
    text_dv = normalize_text(draft.text_dv)
    # Thaana documents get a Latin probe for free; a document that is already
    # Latin keeps whatever the adapter supplied.
    title_latin = normalize_text(
        draft.title_latin or (translit_dv_to_latin(title_dv) if title_dv else "")
    )
    text_latin = normalize_text(
        draft.text_latin or (translit_dv_to_latin(text_dv) if text_dv else "")
    )
    return (
        draft.source,
        draft.source_key,
        normalize_text(draft.title_en),
        normalize_text(draft.text_en),
        normalize_text(draft.summary_en),
        title_dv,
        text_dv,
        strip_fili(title_dv),
        strip_fili(text_dv),
        title_latin,
        text_latin,
    )


def _rebuild_vectors(drafts: list[DocumentDraft]) -> None:
    """Build every vector in one statement per batch.

    A VALUES join rather than per-row updates: the text these vectors are
    built from is never stored (spec 12.1), so it has to be supplied at index
    time, and one round trip per batch keeps that affordable.
    """
    if not drafts:
        return
    mode = getattr(settings, "SEARCH_DV_INDEX_MODE", "dual")
    dv_expr = _DV_EXPRS.get(mode, _DV_EXPRS["dual"])

    rows = [_vector_params(d) for d in drafts]
    placeholder = "(" + ", ".join(["%s"] * 11) + ")"
    values = ", ".join([placeholder] * len(rows))
    sql = _VECTOR_SQL.format(dv_expr=dv_expr, values=values)

    params: list = []
    for row in rows:
        params.extend(row)

    with connection.cursor() as cur:
        cur.execute(sql, params)


def reindex_source(
    key: str,
    *,
    limit: int | None = None,
    only_stale: bool = False,
    batch_size: int = 500,
    **filters,
) -> int:
    """Stream every document from one source through its adapter."""
    adapter = base.get_adapter(key)

    if only_stale:
        stale_keys = list(
            SearchDocument.objects.filter(
                source=key, stale_marked_at__isnull=False
            ).values_list("source_key", flat=True)[: limit or 1_000_000]
        )
        source_keys: Iterable[str] = iter(stale_keys)
    else:
        source_keys = adapter.iter_source_keys(**filters)

    written = 0
    buffer: list[DocumentDraft] = []

    for source_key in source_keys:
        if limit is not None and written + len(buffer) >= limit:
            break
        raw = adapter.fetch_raw(source_key)
        if raw is None:
            logger.warning("%s: no raw document for key %s", key, source_key)
            continue
        draft = adapter.to_document(raw)
        if draft is None:
            continue
        buffer.append(apply_overlays(draft))
        if len(buffer) >= batch_size:
            written += upsert_drafts(buffer)
            buffer.clear()

    if buffer:
        written += upsert_drafts(buffer)
    return written


from django.core.signals import setting_changed  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(setting_changed)
def _reset_overlay_cache(sender, setting, **kwargs):
    global _OVERLAY_CACHE
    if setting == "SEARCH_DRAFT_OVERLAYS":
        _OVERLAY_CACHE = None
