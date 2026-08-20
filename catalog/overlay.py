"""Fold the entity profile into a DocumentDraft.

Registered after enrich.overlay.apply_enrichment in SEARCH_DRAFT_OVERLAYS, so
the entity layer sees the enriched draft and wins over it: the entity profile is
built from every listing of the thing, and a per-document extraction is built
from one.

`profile_tier` on the card is what the frontend renders the caveat from. It is
the DOMINANT tier -- the one holding the most winning fields -- not the lowest.
Lowest was the original design, on the reasoning that a profile is only as
trustworthy as its weakest value, and measurement destroyed it: 7,641 of 7,794
entity-backed cards came out `inferred` because one inferred boolean such as
call_out dragged down an otherwise grounded profile. A caveat that fires on 98%
of cards tells a reader nothing.

`inferred_count` and `field_count` travel with it so the UI can be specific
("3 of 11 details from model knowledge") instead of blanket, and the detail page
still has per-field provenance for the exact ones.

A link publishes without a profile. The two halves are separate: the LINK knows
this listing is one of 34 for the same product, which needs no model call, while
the PROFILE knows what that product is, which does. Gating both on the profile
meant resolving 18,424 links published nothing at all until the profiling spend
landed, so the frontend had no grouping to show and no way to earn one. Only the
profile-derived keys -- profile_tier, inferred_count, field_count, spec chips,
and the service card's answers -- wait for a profile now.
"""

from __future__ import annotations

import logging

from catalog.cards import build_service_card, spec_chips
from catalog.merge import dominant_tier, winning_fields
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
    profiled = entity.profile_status in _USABLE

    fields = winning_fields(entity) if profiled else []
    tiers = [f.provenance for f in fields]
    dominant = dominant_tier(fields)
    inferred_count = sum(1 for t in tiers if t == "inferred")

    if entity.title_en:
        draft.title_en = entity.title_en
    if entity.title_dv:
        draft.title_dv = entity.title_dv
    if entity.summary_en:
        draft.summary_en = entity.summary_en
    if entity.summary_dv:
        draft.summary_dv = entity.summary_dv

    attrs = {
        **draft.attrs,
        "entity_id": entity.id,
        "entity_kind": entity.kind,
        "identity_confidence": entity.identity_confidence,
    }
    card = dict(draft.card)
    card["entity_id"] = entity.id
    card["listing_count"] = entity.listing_count
    if entity.category_id:
        card["category_leaf"] = entity.category.label_en

    if profiled:
        # A trust label computed from no fields is not "grounded" or
        # "inferred", it is absent. Writing an empty profile_tier onto every
        # linked document would make the frontend render a caveat slot for a
        # profile that does not exist.
        attrs["profile_tier"] = dominant
        attrs["inferred_count"] = inferred_count
        attrs["field_count"] = len(fields)
        card["profile_tier"] = dominant
        card["inferred_count"] = inferred_count
        card["field_count"] = len(fields)
    draft.attrs = attrs

    if entity.kind == "service":
        draft.card = build_service_card(entity, fields, card)
    else:
        card["kind"] = "product"
        card["title"] = strip_phones(entity.title_en or card.get("title", ""))
        if entity.brand:
            card["brand"] = entity.brand
        # Only ever added, never blanked: an unprofiled entity has no chips of
        # its own and must leave the ones the per-document extraction earned.
        chips = spec_chips(fields)
        if chips:
            card["spec_chips"] = chips
        draft.card = card

    return draft
