"""Recompute the duplicate flag. Keeps the most recent listing of each
(dedupe_key) group and flags the rest.

Recompute rather than accumulate: every run clears the flag first, so a fresh
repost becomes the survivor and yesterday's is demoted -- the flag is not
sticky (spec 12.6: nothing is deleted).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction

from search.models import SearchDocument


class Command(BaseCommand):
    help = "Flag duplicate listings, keeping the most recent of each group."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS).exclude(dedupe_key="")
        if opts["source"]:
            qs = qs.filter(source=opts["source"])

        # Most recent first within each group, so the first row of every group
        # is the survivor.
        rows = list(
            qs.order_by("dedupe_key", "-published_at", "-id")
            .values_list("id", "dedupe_key")
        )

        groups: list[list[int]] = []
        current_key = None
        current: list[int] = []
        for doc_id, key in rows:
            if key != current_key:
                if current:
                    groups.append(current)
                current = [doc_id]
                current_key = key
            else:
                current.append(doc_id)
        if current:
            groups.append(current)

        groups = [g for g in groups if len(g) > 1]
        flagged = sum(len(g) - 1 for g in groups)

        self.stdout.write(f"{len(groups)} groups, {flagged} duplicate rows to flag")
        if opts["dry_run"] or not rows:
            return

        flagged_ids = [gid for g in groups for gid in g[1:]]
        with transaction.atomic(using=settings.STREAM_DB_ALIAS):
            # Every run recomputes: clear first, then set.
            qs.update(is_duplicate=False, duplicate_count=1)
            SearchDocument.objects.using(settings.STREAM_DB_ALIAS).filter(id__in=flagged_ids).update(
                is_duplicate=True
            )
        for group in groups:
            survivor = SearchDocument.objects.using(settings.STREAM_DB_ALIAS).get(id=group[0])
            survivor.duplicate_count = len(group)
            survivor.card = {**(survivor.card or {}), "duplicate_count": len(group)}
            survivor.save(update_fields=["duplicate_count", "card"])
        self.stdout.write(self.style.SUCCESS(
            f"{len(groups)} survivors kept, {flagged} rows flagged"
        ))
