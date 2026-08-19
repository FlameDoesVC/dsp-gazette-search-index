"""Fold the entity profile into a DocumentDraft.

Registered after enrich.overlay.apply_enrichment in SEARCH_DRAFT_OVERLAYS, so
the entity layer sees the enriched draft and wins over it: the entity profile is
built from every listing of the thing, and a per-document extraction is built
from one.

`profile_tier` on the card is what the frontend renders the caveat from. It is
the lowest tier among the winning fields, because a profile is only as
trustworthy as its weakest displayed value.
"""

from __future__ import annotations

import logging

from catalog.cards import build_service_card, spec_chips
from catalog.merge import PROVENANCE_ORDER, winning_fields
from catalog.models import Entity, EntityLink
from search.adapters.base import DocumentDraft
from search.contacts import strip_phones

logger = logging.getLogger(__name__)

_USABLE = ("ok", "needs_review")


def apply_entity(draft: DocumentDraft) -> DocumentDraft:
    link = (EntityLink.objects
            .filter(source=draft.source, source_key=draft.source_key)
            .select_related("entity", "entity__category").first())
    if link is None:
        return draft
    entity: Entity = link.entity
    if entity.profile_status not in _USABLE:
        return draft

    fields = winning_fields(entity)
    tiers = [f.provenance for f in fields]
    lowest = max(tiers, key=PROVENANCE_ORDER.index) if tiers else ""

    if entity.title_en:
        draft.title_en = entity.title_en
    if entity.title_dv:
        draft.title_dv = entity.title_dv
    if entity.summary_en:
        draft.summary_en = entity.summary_en
    if entity.summary_dv:
        draft.summary_dv = entity.summary_dv

    draft.attrs = {
        **draft.attrs,
        "entity_id": entity.id,
        "entity_kind": entity.kind,
        "profile_tier": lowest,
        "identity_confidence": entity.identity_confidence,
    }

    card = dict(draft.card)
    card["entity_id"] = entity.id
    card["profile_tier"] = lowest
    card["listing_count"] = entity.listing_count
    if entity.category_id:
        card["category_leaf"] = entity.category.label_en

    if entity.kind == "service":
        draft.card = build_service_card(entity, fields, card)
    else:
        card["kind"] = "product"
        card["title"] = strip_phones(entity.title_en or card.get("title", ""))
        if entity.brand:
            card["brand"] = entity.brand
        chips = spec_chips(fields)
        if chips:
            card["spec_chips"] = chips
        draft.card = card

    return draft
