"""Projection into the DocumentSpec side table. Spec 4.4.

Three inputs, one table:

  1. the unit extractor over title and summary  -- catches '24V-5A-120W'
  2. attrs['specs']      -- what P4's enrichment assigned semantic keys to
  3. attrs['specs_raw']  -- iBay ProductInfo, already near-schema and free

Source 3 is the largest by volume, which is the strongest argument for the
whole typed-attribute design: `Item Condition` (7,098), `Type` (4,194),
`Brand` (2,313) and friends are structure the source gave us and that a
language model should not be paid to re-derive.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db import transaction

from search.models import DocumentSpec, SearchDocument, SpecKey

# Read off the model rather than repeated here, so a migration cannot leave
# these truncations silently wrong.
_UNIT_MAX = DocumentSpec._meta.get_field("unit").max_length
_VALUE_TEXT_MAX = DocumentSpec._meta.get_field("value_text").max_length
_KEY_RAW_MAX = DocumentSpec._meta.get_field("key_raw").max_length
from search.specs.extract import extract_units
from search.specs.normalize import normalize_value

logger = logging.getLogger(__name__)

_SLUG = re.compile(r"[^a-z0-9]+")

# Keys carried on SearchDocument columns or already faceted statically. Storing
# them again would double-count in discovery.
SKIP_KEYS = {"price", "location", "source", "doc_type"}


def slugify_key(raw: str) -> str:
    return _SLUG.sub("_", (raw or "").strip().lower()).strip("_")[:64]


def _registry() -> dict[str, SpecKey]:
    return {k.key: k for k in SpecKey.objects.all()}


def specs_for_document(doc: SearchDocument, registry=None) -> list[dict]:
    registry = registry if registry is not None else _registry()
    rows: list[dict] = []
    seen: set[tuple[str, float | None, str]] = set()

    def push(key_raw, *, value_num=None, value_text="", unit="",
             provenance=""):
        key_raw = slugify_key(key_raw)
        if not key_raw or key_raw in SKIP_KEYS:
            return
        ident = (key_raw, value_num, value_text)
        if ident in seen:
            return
        seen.add(ident)
        spec_key = registry.get(key_raw)
        # A unit is short by nature: GB, mm, W, MVR, mAh. Anything that does not
        # fit DocumentSpec.unit is not a unit -- it is a VALUE the model filed in
        # the wrong slot. Measured over the enriched shopping corpus: 31 of 494
        # distinct units overflowed, and they read 'Action and Adventure',
        # 'H.265+/H.265/H.264+/H.264', 'Capture & reduction of growth'.
        #
        # So the fix is to drop it, not to widen the column. Widening would let
        # a sentence become a facet value and pollute the vocabulary the whole
        # facet system keys on. It is the opposite call from
        # Iulaan.translated_title, which was widened because the content there
        # was legitimate and the column was simply too small for it -- same
        # symptom, and the data decides which one it is.
        #
        # Dropping only the unit keeps the spec: the key and value are usually
        # right even when the unit is nonsense. Before this the DataError killed
        # the whole pass, so 20,494 enriched records projected 192 rows.
        if unit and len(unit) > _UNIT_MAX:
            if not value_text:
                value_text = unit[:_VALUE_TEXT_MAX]
            unit = ""
        rows.append({
            "key_id": spec_key.id if spec_key else None,
            "key_raw": key_raw[:_KEY_RAW_MAX],
            "value_num": value_num,
            "value_text": value_text[:_VALUE_TEXT_MAX],
            "unit": unit,
            "provenance": provenance,
        })

    # 1. the deterministic extractor over whatever text we have
    text = " ".join(t for t in (doc.title_en, doc.title_dv, doc.title_latin,
                                doc.summary_en) if t)
    for u in extract_units(text):
        push(u["key_raw"], value_num=u["value"], unit=u["unit"])

    # 2. enrichment output
    for s in (doc.attrs.get("specs") or []):
        if not isinstance(s, dict):
            continue
        key_raw = slugify_key(s.get("key_raw", ""))
        spec_key = registry.get(key_raw)
        if s.get("value_num") is not None:
            push(key_raw, value_num=float(s["value_num"]), unit=s.get("unit", ""))
        elif s.get("value_text"):
            values = (normalize_value(spec_key, s["value_text"]) if spec_key
                      else [s["value_text"][:128]])
            for v in values:
                push(key_raw, value_text=v)

    # 3. scraped ProductInfo
    for raw_key, raw_value in (doc.attrs.get("specs_raw") or {}).items():
        key_raw = slugify_key(raw_key)
        spec_key = registry.get(key_raw)
        values = (normalize_value(spec_key, str(raw_value)) if spec_key
                  else [v[:128] for v in _split_plain(str(raw_value))])
        for v in values:
            push(key_raw, value_text=v)

    # 4. winning entity fields (catalog spec section 11). Inferred values are
    # filterable, which is the point, but only above the identity-confidence
    # floor: a filter built on a guessed identity narrows to the wrong thing.
    entity_id = doc.attrs.get("entity_id")
    if entity_id:
        from catalog.merge import winning_fields
        from catalog.models import Entity

        entity = Entity.objects.filter(id=entity_id).first()
        if entity is not None:
            floor = settings.CATALOG_INFERRED_MIN_CONFIDENCE
            for f in winning_fields(entity):
                if (f.provenance == "inferred"
                        and entity.identity_confidence < floor):
                    continue
                if f.value_num is not None:
                    push(f.key_raw, value_num=f.value_num, unit=f.unit,
                         provenance=f.provenance)
                elif f.value_text:
                    push(f.key_raw, value_text=f.value_text,
                         provenance=f.provenance)

    return rows


def _split_plain(raw: str) -> list[str]:
    from enrich.preextract import split_multivalue
    return split_multivalue(raw) or ([raw.strip()] if raw.strip() else [])


def sync_document_specs(doc: SearchDocument, registry=None) -> int:
    """Replace this document's spec rows. Idempotent by construction."""
    rows = specs_for_document(doc, registry)
    with transaction.atomic():
        DocumentSpec.objects.filter(document_id=doc.id).delete()
        if rows:
            DocumentSpec.objects.bulk_create(
                [DocumentSpec(document_id=doc.id, **r) for r in rows],
                batch_size=500,
            )
    return len(rows)


def sync_specs(*, source=None, doc_type="shopping", limit=None,
               batch_size=500) -> dict:
    registry = _registry()
    qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
    if source:
        qs = qs.filter(source=source)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)

    counts = {"documents": 0, "specs": 0}
    for doc in qs.only(
        "id", "title_en", "title_dv", "title_latin", "summary_en", "attrs"
    ).iterator(chunk_size=batch_size):
        if limit is not None and counts["documents"] >= limit:
            break
        counts["specs"] += sync_document_specs(doc, registry)
        counts["documents"] += 1
    return counts


def prune_orphans() -> int:
    """Spec rows whose document no longer exists. The FK is db_constraint=False
    because SearchDocument is partitioned, so nothing cascades for us."""
    live = set(SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
               .values_list("id", flat=True))
    orphans = [
        s.id for s in DocumentSpec.objects.using(settings.STREAM_DB_ALIAS)
        .only("id", "document_id")
        .iterator(chunk_size=1000) if s.document_id not in live
    ]
    DocumentSpec.objects.filter(id__in=orphans).delete()
    return len(orphans)


def candidate_keys(limit: int = 50) -> list[dict]:
    """Unpromoted key_raw values ranked by how many documents carry them.

    This is the admin promotion queue's data source (spec 4.4): the frequency
    ranking is what turns an open attribute space into a manageable list of
    one-click decisions.
    """
    from django.db.models import Count

    rows = (
        DocumentSpec.objects.filter(key__isnull=True)
        .values("key_raw")
        .annotate(documents=Count("document_id", distinct=True),
                  distinct_values=Count("value_text", distinct=True))
        .order_by("-documents")[:limit]
    )
    return list(rows)
