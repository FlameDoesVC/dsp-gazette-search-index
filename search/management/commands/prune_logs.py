"""Drop log partitions older than the retention window.

Raw rows expire; aggregates do not. Query text is the most sensitive data this
system holds (spec 16.3), and dropping a partition is instant and leaves no
dead tuples, unlike a DELETE over millions of rows.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import connection

TABLES = ("search_querylog", "search_clicklog")


class Command(BaseCommand):
    help = "Drop log partitions older than --days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        cutoff = (dt.date.today() - dt.timedelta(days=opts["days"])).replace(day=1)
        cutoff_suffix = cutoff.strftime("%Y%m")

        with connection.cursor() as cur:
            for table in TABLES:
                cur.execute(
                    "SELECT c.relname FROM pg_inherits i "
                    "JOIN pg_class c ON c.oid = i.inhrelid "
                    "JOIN pg_class p ON p.oid = i.inhparent "
                    "WHERE p.relname = %s", [table])
                for (name,) in cur.fetchall():
                    suffix = name.rsplit("_", 1)[-1]
                    if not suffix.isdigit() or suffix >= cutoff_suffix:
                        continue
                    self.stdout.write(f"dropping {name}")
                    if not opts["dry_run"]:
                        cur.execute(f"DROP TABLE {name}")
