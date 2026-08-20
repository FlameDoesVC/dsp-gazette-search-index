"""Fold stored translations into a DocumentDraft.

Registered FIRST in SEARCH_DRAFT_OVERLAYS, before enrichment and the catalog,
because a machine translation is the weakest claim in the stack: anything a
model extracted from the document itself, or an entity profile built from every
listing of the thing, should win over it.

Only ever FILLS a field, never replaces one. If the adapter supplied real
Dhivehi from the source, that is ground truth and a translation of the English
has no business overwriting it.
"""

from __future__ import annotations

import hashlib
import logging

from search.adapters.base import DocumentDraft
from search.models import FieldTranslation

logger = logging.getLogger(__name__)


def source_hash(text: str) -> str:
    """Hash of the text that was translated. Whitespace-normalised, because a
    reflowed title is not a changed one."""
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


def apply_translations(draft: DocumentDraft) -> DocumentDraft:
    rows = FieldTranslation.objects.filter(
        source=draft.source, source_key=draft.source_key
    ).only("target_field", "source_field", "source_hash", "value")

    for row in rows:
        if getattr(draft, row.target_field, None):
            # Real content from the source. Leave it alone.
            continue
        origin = getattr(draft, row.source_field, "")
        if not origin or source_hash(origin) != row.source_hash:
            # The text this was translated from is gone or has changed, so the
            # translation describes something the document no longer says.
            continue
        setattr(draft, row.target_field, row.value)
    return draft


def remember(source: str, source_key: str, *, target_field: str,
             source_field: str, origin_text: str, value: str,
             model_name: str = "") -> None:
    """Store one translation so the next reindex can put it back."""
    FieldTranslation.objects.update_or_create(
        source=source, source_key=source_key, target_field=target_field,
        defaults={"source_field": source_field,
                  "source_hash": source_hash(origin_text),
                  "value": value, "model_name": model_name},
    )
