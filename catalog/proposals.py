"""Crowdsourced field corrections. Spec section 10.

Auto-apply on agreement, chosen over an approval queue deliberately: the corpus
has 16,608 listings behind about 5,300 entities and no reviewer is going to
clear that queue, so a correction that needs a human is a correction that never
lands.

The risk this accepts, stated where the code is: a quorum over IP hashes is
defeatable by anyone with a phone hotspot and patience. The mitigations are the
retained EntityField audit trail, revertibility, and the conflicted queue.
Prevention is not among them.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.merge import recompute_winners
from catalog.models import Entity, EntityField, FieldProposal


def _value_key(proposal) -> tuple:
    return (proposal.value_num, (proposal.value_text or "").strip().lower())


def propose(entity: Entity, key_raw: str, *, value_num=None, value_text="",
            unit="", ip_hash: str) -> None:
    """Record one proposal. Duplicates from one IP hash are silently dropped."""
    try:
        with transaction.atomic():
            FieldProposal.objects.create(
                entity=entity, key_raw=key_raw, value_num=value_num,
                value_text=(value_text or "")[:128], unit=(unit or "")[:16],
                proposer_ip_hash=ip_hash)
    except IntegrityError:
        return                    # already counted; the caller learns nothing


def evaluate_field(entity: Entity, key_raw: str) -> str:
    """Apply, conflict, or leave pending. Returns what happened."""
    quorum = settings.CATALOG_PROPOSAL_QUORUM
    margin = settings.CATALOG_PROPOSAL_MARGIN

    pending = list(FieldProposal.objects.filter(
        entity=entity, key_raw=key_raw, status="pending"))
    if not pending:
        return "pending"

    votes: dict[tuple, set[str]] = defaultdict(set)
    for proposal in pending:
        votes[_value_key(proposal)].add(proposal.proposer_ip_hash)

    ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
    top_value, top_voters = ranked[0]
    runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0

    if len(top_voters) < quorum:
        return "pending"

    if runner_up and len(top_voters) - runner_up < margin:
        # Genuine disagreement. Nothing applies, the field falls back to the
        # next tier, and a human sees it.
        FieldProposal.objects.filter(
            entity=entity, key_raw=key_raw, status="pending").update(
                status="conflicted")
        return "conflicted"

    value_num, value_text = top_value
    with transaction.atomic():
        EntityField.objects.filter(
            entity=entity, key_raw=key_raw, provenance="correction").delete()
        if value_num is not None or value_text:
            sample = next(p for p in pending if _value_key(p) == top_value)
            EntityField.objects.create(
                entity=entity, key_raw=key_raw, value_num=value_num,
                value_text=sample.value_text, unit=sample.unit,
                provenance="correction", support_count=len(top_voters),
                confidence=1.0)
        # An empty proposed value means "this field is wrong": no correction row
        # is written, and the losing tiers below it are removed so nothing shows.
        else:
            EntityField.objects.filter(
                entity=entity, key_raw=key_raw,
                provenance__in=("consensus", "grounded", "inferred")).delete()

        FieldProposal.objects.filter(
            entity=entity, key_raw=key_raw, status="pending").update(
                status="applied")

    recompute_winners(entity)
    return "applied"


def apply_ready(*, limit=None) -> dict:
    """Sweep every field with pending proposals. Idempotent."""
    fields = (FieldProposal.objects.filter(status="pending")
              .values_list("entity_id", "key_raw").distinct())
    if limit:
        fields = fields[:limit]

    counts = {"applied": 0, "conflicted": 0, "pending": 0}
    for entity_id, key_raw in list(fields):
        entity = Entity.objects.filter(id=entity_id).first()
        if entity is None:
            continue
        counts[evaluate_field(entity, key_raw)] += 1
    return counts


def stale_conflicts(days: int = 30):
    """Conflicted fields nobody has resolved. The admin queue's real backlog."""
    cutoff = timezone.now() - dt.timedelta(days=days)
    return (FieldProposal.objects.filter(status="conflicted",
                                         created_at__lt=cutoff)
            .values("entity_id", "key_raw").distinct())
