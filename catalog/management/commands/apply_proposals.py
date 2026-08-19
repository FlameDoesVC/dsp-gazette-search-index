"""Sweep pending proposals. The endpoint evaluates inline, so this is the
safety net for proposals stranded by a crash mid-request, plus the report on
how big the conflicted backlog has grown.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.proposals import apply_ready, stale_conflicts


class Command(BaseCommand):
    help = "Apply proposals that have reached quorum; report conflicts."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--stale-days", type=int, default=30)

    def handle(self, *args, **opts):
        counts = apply_ready(limit=opts["limit"])
        stale = len(list(stale_conflicts(opts["stale_days"])))
        self.stdout.write(self.style.SUCCESS(str(counts)))
        if stale:
            self.stdout.write(self.style.WARNING(
                f"{stale} conflicted fields older than {opts['stale_days']} "
                f"days are waiting in the admin"))
