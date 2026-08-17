"""Draft-to-row conversion and bulk upsert.

Streaming discipline (spec 12.4): the source iteration uses `.iterator()` and
rows are written in batches, so reindexing 5M documents costs the same memory
as reindexing 5,000. Nothing here calls `list()` on a queryset.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models import Q

from search.adapters import base
from search.adapters.base import DocumentDraft
from search.models import SearchDocument

logger = logging.getLogger(__name__)

# Written by upsert; everything except the identity pair and `id`.
_UPDATE_FIELDS = [
    "doc_type", "url",
    "title_en", "title_dv", "title_latin",
    "summary_en", "summary_dv",
    "price", "currency", "location", "island", "atoll",
    "published_at", "expires_at", "is_active",
    "attrs", "card", "thumbnails", "quality", "content_hash",
    "stale_marked_at",
]


def _row(draft: DocumentDraft) -> SearchDocument:
    return SearchDocument(
        source=draft.source,
        source_key=draft.source_key,
        doc_type=draft.doc_type,
        url=draft.url,
        title_en=draft.title_en,
        title_dv=draft.title_dv,
        title_latin=draft.title_latin,
        summary_en=draft.summary_en,
        summary_dv=draft.summary_dv,
        price=draft.price,
        currency=draft.currency,
        location=draft.location,
        island=draft.island,
        atoll=draft.atoll,
        published_at=draft.published_at,
        expires_at=draft.expires_at,
        is_active=draft.is_active,
        attrs=draft.attrs,
        card=draft.card,
        thumbnails=draft.thumbnails,
        quality=draft.quality,
        content_hash=draft.content_hash,
        stale_marked_at=None,   # a successful pass clears the work ticket
    )


def upsert_drafts(drafts: Iterable[DocumentDraft]) -> int:
    """Insert or update rows for `drafts`, then rebuild their vectors.

    Returns the number of drafts written.
    """
    batch = [_row(d) for d in drafts]
    if not batch:
        return 0

    with transaction.atomic():
        SearchDocument.objects.bulk_create(
            batch,
            update_conflicts=True,
            unique_fields=["source", "source_key"],
            update_fields=_UPDATE_FIELDS,
            batch_size=500,
        )
        _rebuild_vectors(batch)
    return len(batch)


def _rebuild_vectors(batch: list[SearchDocument]) -> None:
    """Build tsvectors from title and summary only.

    P1 populates `vector_en`. `vector_dv` and `vector_latin` are written by P2,
    once the Dhivehi normalization pipeline exists -- writing them here with the
    wrong analysis would have to be undone.
    """
    keys = Q()
    for row in batch:
        keys |= Q(source=row.source, source_key=row.source_key)
    SearchDocument.objects.filter(keys).update(
        vector_en=(
            SearchVector("title_en", weight="A", config="english")
            + SearchVector("summary_en", weight="B", config="english")
        )
    )


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
        buffer.append(draft)
        if len(buffer) >= batch_size:
            written += upsert_drafts(buffer)
            buffer.clear()

    if buffer:
        written += upsert_drafts(buffer)
    return written
