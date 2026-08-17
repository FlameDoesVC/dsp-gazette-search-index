"""Stream source documents into the search index.

Runs on the `direct` database alias: streaming needs real server-side cursors,
which a transaction-mode connection pool forbids (spec 12.4).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from search.adapters import base
from search.indexing import reindex_source


class Command(BaseCommand):
    help = "Rebuild the search index for one source or all sources."

    def add_arguments(self, parser):
        parser.add_argument("--source", help="Source key; omit for all sources.")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--stale",
            action="store_true",
            help="Only documents with stale_marked_at set (spec 5.7).",
        )
        parser.add_argument(
            "--database",
            default="direct",
            help="Database alias. Defaults to `direct` -- never use a pooled "
                 "alias for a streaming reindex.",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"unknown database alias {alias!r}")

        if options["source"]:
            try:
                base.get_adapter(options["source"])
            except KeyError as exc:
                raise CommandError(str(exc)) from None
            keys = [options["source"]]
        else:
            keys = [a.key for a in base.all_adapters()]

        total = 0
        for key in keys:
            written = reindex_source(
                key,
                limit=options["limit"],
                only_stale=options["stale"],
                batch_size=options["batch_size"],
            )
            total += written
            self.stdout.write(f"{key}: indexed {written} documents")

        self.stdout.write(self.style.SUCCESS(f"total: {total} documents"))
