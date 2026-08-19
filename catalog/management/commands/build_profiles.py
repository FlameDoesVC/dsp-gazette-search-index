"""Stage 2 over entities. Costs money: reports the count before spending."""

from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand

from catalog.profile import run_profile_pass, select_entity_ids


class Command(BaseCommand):
    help = "Build entity profiles with the model. Costs one call per entity."

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=["product", "service"], default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--concurrency", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        ids = select_entity_ids(kind=opts["kind"], force=opts["force"],
                                limit=opts["limit"])
        self.stdout.write(f"{len(ids)} entities selected")
        if opts["dry_run"] or not ids:
            return
        counts = asyncio.run(run_profile_pass(
            ids, concurrency=opts["concurrency"]))
        self.stdout.write(self.style.SUCCESS(str(counts)))
