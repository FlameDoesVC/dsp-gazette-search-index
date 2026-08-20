"""Document -> entity. Deterministic, no model call. Spec section 7."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from catalog.identity import (brand_vocabulary, clean_title,
                              discriminating_tokens, identity_confidence,
                              identity_stopwords, match_brand, model_tokens,
                              product_key, service_key)
from catalog.models import Entity, EntityLink
from search.contacts import primary_phone
from search.models import SearchDocument
from search.taxonomy import map_path

logger = logging.getLogger(__name__)

SERVICE_ROOTS = {"Services"}
PRODUCT_ROOTS = {"For Sale"}
# Spec section 2 scopes this project to iBay For Sale and Services. Everything
# else is out, and the gate has to be explicit rather than emergent: measured on
# the live corpus, without it 714 property listings resolved as PRODUCTS,
# because 'FACE2' in '3ROOM APARTMENT @HULHUMALE FACE2 VINARES' looks exactly
# like a model designator. Task 6 would then have paid a model call per
# apartment to write it a spec sheet.
#
# This gate reads the source's own root rather than the canonical tier on
# purpose: the taxonomy says what a thing IS, not whether this project handles
# it, and the seeder classifies housing as tier `primary` because an apartment
# is a primary listing in its own family. Scope and taxonomy are different
# questions and conflating them is what produced the defect.
IN_SCOPE_DOC_TYPES = {"shopping"}


def in_scope(doc: SearchDocument) -> bool:
    if doc.doc_type not in IN_SCOPE_DOC_TYPES:
        return False
    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    if not path:
        return False
    return path[0] in SERVICE_ROOTS or path[0] in PRODUCT_ROOTS


def _mapped_key(doc: SearchDocument) -> str:
    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    category = map_path(doc.source, path)
    return category.key if category else ""


def _is_service(doc: SearchDocument) -> bool:
    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    if path and path[0] in SERVICE_ROOTS:
        return True
    category = map_path(doc.source, path)
    return bool(category and category.tier == "service")


def _provider_key(doc: SearchDocument) -> str:
    phone = doc.contact_phone or primary_phone(doc.title_en, doc.summary_en)
    if phone:
        return phone
    seller = (doc.card or {}).get("seller_name") or ""
    return f"seller:{seller}" if seller else ""


def resolve_document(doc: SearchDocument, *, vocabulary=None,
                     stopwords=None) -> Entity | None:
    """The entity this document belongs to, creating it if new.

    Returns None when the document is out of scope, or when it carries no usable
    identity. The second is a deliberate miss: an entity built on a guessed
    brand puts wrong specs on a real listing, which is worse than no profile.
    """
    if not in_scope(doc):
        return None

    mapped = _mapped_key(doc)

    if _is_service(doc):
        provider = _provider_key(doc)
        if not provider and not mapped:
            return None
        key = service_key(provider, mapped)
        defaults = {
            "kind": "service",
            "provider_key": provider,
            "service_type": mapped,
            "identity_confidence": 0.9 if provider.isdigit() else 0.6,
        }
        method = "seller_service"
    else:
        vocabulary = brand_vocabulary() if vocabulary is None else vocabulary
        brand = match_brand(doc.title_en, vocabulary)
        tokens = model_tokens(doc.title_en)
        # A product entity needs DISCRIMINATING identity, not merely some
        # identity. Measured on the golden set, requiring only "a brand or any
        # token" scored 50% precision on products: brand-only put 214 different
        # Apple accessories in one entity, and the platform token PS5 put 291
        # different games in another. Both would then have shared one spec
        # sheet, which is the exact failure the deliberate-miss rule exists to
        # avoid. So a brand alone is a category, not an identity, and a token
        # every listing shares identifies nothing.
        keep = discriminating_tokens(tokens, stopwords if stopwords is not None
                                     else identity_stopwords())
        if not keep:
            return None
        tokens = keep
        key = product_key(brand, tokens, mapped)
        defaults = {
            "kind": "product",
            "brand": brand,
            "model_name": " ".join(tokens)[:128],
            # Graded, not both-or-nothing. This number gates whether inferred
            # specs reach DocumentSpec (spec section 9), so see
            # identity_confidence() in catalog/identity.py for why a model
            # designator alone outranks a brand alone.
            "identity_confidence": identity_confidence(brand, tokens),
        }
        method = "identity_match"

    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    category = map_path(doc.source, path)

    with transaction.atomic():
        entity, _ = Entity.objects.get_or_create(
            key=key, defaults={**defaults, "category": category})
        # update_or_create, not create: a document links to at most one entity,
        # so a re-resolution after the title changed must MOVE the link.
        EntityLink.objects.update_or_create(
            source=doc.source, source_key=doc.source_key,
            defaults={"entity": entity, "method": method,
                      "confidence": entity.identity_confidence},
        )
    return entity


def recount(entity_ids=None) -> int:
    """Refresh listing_count. Cheap, and wrong counts are user-visible."""
    from django.db.models import Count

    qs = Entity.objects.all()
    if entity_ids is not None:
        qs = qs.filter(id__in=entity_ids)
    updated = 0
    for entity in qs.annotate(n=Count("links")).only("id", "listing_count"):
        if entity.listing_count != entity.n:
            Entity.objects.filter(id=entity.id).update(listing_count=entity.n)
            updated += 1
    return updated


def resolve_source(source: str, *, limit=None, dry_run=False) -> dict:
    counts = {"seen": 0, "linked": 0, "missed": 0}
    vocabulary = brand_vocabulary()
    # Derived once per pass: it is a full scan of the corpus titles.
    stopwords = identity_stopwords()
    qs = (SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
          .filter(source=source)
          .only("id", "source", "source_key", "title_en", "attrs", "card",
                "contact_phone"))
    for doc in qs.iterator(chunk_size=500):
        if limit is not None and counts["seen"] >= limit:
            break
        counts["seen"] += 1
        if dry_run:
            continue
        entity = resolve_document(doc, vocabulary=vocabulary,
                                  stopwords=stopwords)
        counts["linked" if entity else "missed"] += 1
    recount()
    if not dry_run:
        # A change to identity extraction re-keys entities, and the rows under
        # the old keys are left holding nothing. They are not harmless: they
        # inflate the entity count that the profiling spend is estimated from,
        # and build_profiles selects them before discovering they have no
        # listings to profile.
        counts["pruned"] = Entity.objects.filter(links__isnull=True).delete()[0]
    return counts
