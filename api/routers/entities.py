"""Entity profiles and crowdsourced corrections. Spec section 11.1."""

from __future__ import annotations

from ninja import Router
from ninja.errors import HttpError

from api.logging import session_hash
from api.ratelimit import proposal_quota_exceeded
from api.schemas import AcceptedOut, EntityOut, ProposalIn
from catalog.merge import dominant_tier, winning_fields
from catalog.models import Entity, EntityLink
from catalog.proposals import evaluate_field, propose

router = Router()

MAX_KEY = 64
# One entity in the corpus links 453 listings. The detail response carries the
# evidence, not the whole feed.
MAX_SOURCES = 25


def _sources_for(entity) -> list[dict]:
    """The listings behind a profile, so an inferred field can be checked.

    Without this an entity renders as `listing_count: 453` with no way to verify
    any of it, while the design leans on every inference being visible. It is
    also what makes a correction meaningful: you look at the ads before claiming
    a field is wrong.

    Two queries, not one per link. EntityLink stores (source, source_key)
    because SearchDocument is partitioned and links must survive a reindex
    (spec section 6.2), so there is no FK to follow.
    """
    from search.models import SearchDocument

    pairs = list(EntityLink.objects.filter(entity=entity)
                 .values_list("source", "source_key")[:MAX_SOURCES])
    if not pairs:
        return []
    by_pair = {
        (d.source, d.source_key): d
        for d in SearchDocument.objects
        .filter(source__in={s for s, _k in pairs},
                source_key__in=[k for _s, k in pairs])
        .only("id", "source", "source_key", "url", "title_en")
    }
    out = []
    for source, source_key in pairs:
        doc = by_pair.get((source, source_key))
        if doc is not None:
            out.append({"document_id": doc.id, "source": doc.source,
                        "source_key": doc.source_key, "url": doc.url,
                        "title": doc.title_en})
    return out


@router.get("/entities/{int:entity_id}", response=EntityOut)
def entity_detail(request, entity_id: int):
    entity = (Entity.objects.filter(id=entity_id)
              .select_related("category").first())
    if entity is None or entity.profile_status == "failed":
        raise HttpError(404, "not found")

    fields = winning_fields(entity)
    return {
        "id": entity.id,
        "kind": entity.kind,
        "title_en": entity.title_en,
        "title_dv": entity.title_dv,
        "summary_en": entity.summary_en,
        "summary_dv": entity.summary_dv,
        "brand": entity.brand,
        "model_name": entity.model_name,
        "service_type": entity.service_type,
        "category_key": entity.category.key if entity.category_id else None,
        "identity_confidence": entity.identity_confidence,
        "profile_tier": dominant_tier(fields),
        "inferred_count": sum(1 for f in fields if f.provenance == "inferred"),
        "listing_count": entity.listing_count,
        "sources": _sources_for(entity),
        "fields": [
            {"key_raw": f.key_raw, "value_num": f.value_num,
             "value_text": f.value_text, "unit": f.unit,
             "provenance": f.provenance, "support_count": f.support_count}
            for f in fields
        ],
    }


@router.post("/entities/{int:entity_id}/propose", response={202: AcceptedOut})
def propose_correction(request, entity_id: int, payload: ProposalIn):
    """Always 202. Spec section 11.1: reporting acceptance, deduplication or
    throttling would be an oracle for probing the quorum, and the caller has no
    legitimate use for the difference."""
    ip_hash = session_hash(request)

    if proposal_quota_exceeded(ip_hash):
        return 202, {"status": "accepted"}

    key_raw = (payload.key_raw or "").strip()[:MAX_KEY]
    if not key_raw:
        return 202, {"status": "accepted"}

    entity = Entity.objects.filter(id=entity_id).first()
    if entity is None:
        return 202, {"status": "accepted"}

    propose(entity, key_raw, value_num=payload.value_num,
            value_text=payload.value_text, unit=payload.unit, ip_hash=ip_hash)
    # Evaluated inline: quorum is small, the query is two indexed counts, and a
    # correction that waits for a cron job looks broken to the person who made
    # it.
    evaluate_field(entity, key_raw)
    return 202, {"status": "accepted"}
