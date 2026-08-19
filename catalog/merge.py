"""The provenance ladder. Spec section 9.

scraped > correction > consensus > grounded > inferred

Two rules that are not generic precedence logic and exist for named reasons:

- `scraped` outranks a correction, because a source's own structured field is
  ground truth in this system (spec 5.2 rule 3) and letting a crowd overwrite it
  would make the most reliable data the easiest to vandalise.
- An unresolvable same-tier tie produces NO winner. Picking by row order would
  make the displayed value depend on insertion order, which is neither
  reproducible nor defensible to the user who reported it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from catalog.models import Entity, EntityField, EntityLink

PROVENANCE_ORDER = ("scraped", "correction", "consensus", "grounded", "inferred")
_RANK = {p: i for i, p in enumerate(PROVENANCE_ORDER)}

# Keys that hold exactly one value, where two values at the same tier really are
# a contradiction. Everything else is a list.
#
# Measured on 1,747 profiled service entities: treating every key as
# single-valued left 467 fields with no winner at all -- `service_offered` on 342
# entities and `coverage` on 149 -- because six grounded service lines at equal
# support look like an unresolvable tie. Nothing projected, and 467 entities were
# flagged for review for having done nothing wrong.
#
# Same-tier multiplicity is a list rather than a conflict for a structural
# reason, not a convenient one: one profile pass writes one value per
# single-valued key, a pass replaces the whole grounded/inferred set rather than
# adding to it, and evaluate_field() resolves competing corrections to a single
# row before writing. So two rows at one tier came from one list.
SINGLE_VALUED_KEYS = {
    "brand", "model", "model_name", "variant", "condition", "item_condition",
    "rate_basis", "call_out", "shop_visit", "availability", "service_type",
}


def dominant_tier(fields) -> str:
    """The tier holding the most winning fields, ties breaking toward the weaker.

    One definition, because it was briefly two. The card computed it in
    catalog/overlay.py and the API recomputed it in api/routers/entities.py, and
    when the rule changed from weakest-tier to dominant-tier only one of them was
    updated -- so the same entity read `grounded` on its card and `inferred` on
    its detail page. A displayed trust label that contradicts itself is worse
    than either version alone.
    """
    tiers = [f.provenance for f in fields]
    if not tiers:
        return ""
    counts = {t: tiers.count(t) for t in set(tiers)}
    best = max(counts.values())
    return max((t for t, n in counts.items() if n == best),
               key=PROVENANCE_ORDER.index)


def winning_fields(entity: Entity) -> list[EntityField]:
    return list(EntityField.objects.filter(entity=entity, is_winner=True)
                .select_related("key").order_by("key_raw"))


def _sellers_for(entity: Entity) -> dict[tuple[str, str], str]:
    """(source, source_key) -> seller name, for the entity's linked documents.

    Keyed on the pair, not the bare source_key: source_key is only unique
    within a source, and iBay listing ids will collide with another source's
    keys the moment a second source has entities.
    """
    from search.models import SearchDocument

    pairs = list(EntityLink.objects.filter(entity=entity)
                 .values_list("source", "source_key"))
    out: dict[tuple[str, str], str] = {}
    for source, source_key in pairs:
        doc = (SearchDocument.objects
               .filter(source=source, source_key=source_key)
               .only("card").first())
        if doc is not None:
            out[(source, source_key)] = (doc.card or {}).get("seller_name") or ""
    return out


def promote_consensus(entity: Entity) -> int:
    """Copy a grounded value to `consensus` when independent sellers agree.

    Independence is the whole point: 2,971 of the corpus's listings come from
    one advertiser, so 'appears twice' means nothing and 'appears for two
    sellers' means something.
    """
    sellers = {s for s in _sellers_for(entity).values() if s}
    if len(sellers) < settings.CATALOG_CONSENSUS_MIN_SELLERS:
        return 0

    promoted = 0
    grounded = EntityField.objects.filter(entity=entity, provenance="grounded")
    for row in grounded:
        _, created = EntityField.objects.update_or_create(
            entity=entity, key_raw=row.key_raw, provenance="consensus",
            value_num=row.value_num, value_text=row.value_text,
            defaults={"key": row.key, "unit": row.unit,
                      "support_count": len(sellers),
                      "confidence": row.confidence},
        )
        promoted += int(created)
    return promoted


def recompute_winners(entity: Entity) -> dict:
    """Mark exactly one winner per key_raw, or none when it cannot be decided."""
    rows = list(EntityField.objects.filter(entity=entity))
    by_key: dict[str, list[EntityField]] = {}
    for row in rows:
        by_key.setdefault(row.key_raw, []).append(row)

    winners = unresolved = 0
    with transaction.atomic():
        EntityField.objects.filter(entity=entity, is_winner=True).update(
            is_winner=False)
        for key_raw, candidates in by_key.items():
            candidates.sort(key=lambda r: (_RANK[r.provenance],
                                           -r.support_count))
            best = candidates[0]
            tied = [c for c in candidates
                    if c.provenance == best.provenance
                    and c.support_count == best.support_count]

            if len(tied) > 1 and key_raw in SINGLE_VALUED_KEYS:
                # A real contradiction on a field that holds one value. Show
                # nothing rather than picking a side by row order.
                unresolved += 1
                continue

            # The winning tier wins entire. For a list key that is every value;
            # for a single-valued key `tied` has one member anyway.
            ids = [c.id for c in candidates
                   if c.provenance == best.provenance
                   and c.support_count == best.support_count]
            EntityField.objects.filter(id__in=ids).update(is_winner=True)
            winners += len(ids)

        # Recompute, never accumulate -- the same rule dedupe_listings states for
        # its duplicate flag. A sticky needs_review leaves an entity accused
        # after the conflict is gone: fixing the list-key defect above cleared
        # 467 fields but left 332 entities still flagged, because nothing ever
        # cleared the status.
        if unresolved and entity.profile_status != "failed":
            Entity.objects.filter(id=entity.id).update(
                profile_status="needs_review")
        elif not unresolved and entity.profile_status == "needs_review":
            Entity.objects.filter(id=entity.id).update(profile_status="ok")

    return {"winners": winners, "unresolved": unresolved}


def recompute_all(*, kind=None) -> dict:
    totals = {"entities": 0, "winners": 0, "unresolved": 0, "consensus": 0}
    qs = Entity.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    for entity in qs.iterator(chunk_size=200):
        totals["consensus"] += promote_consensus(entity)
        result = recompute_winners(entity)
        totals["winners"] += result["winners"]
        totals["unresolved"] += result["unresolved"]
        totals["entities"] += 1
    return totals
