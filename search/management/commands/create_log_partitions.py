"""Create month partitions ahead of time.

Run monthly. The DEFAULT partition means a missed run does not lose data, but
rows in the default partition cannot be dropped by the retention policy
without a rewrite, so do not rely on it.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import connection

TABLES = ("search_querylog", "search_clicklog")


def month_bounds(d: dt.date) -> tuple[dt.date, dt.date]:
    start = d.replace(day=1)
    end = (start + dt.timedelta(days=32)).replace(day=1)
    return start, end


def create_partitions(months: int = 3, today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    made: list[str] = []
    with connection.cursor() as cur:
        cursor_date = today.replace(day=1)
        for _ in range(months):
            start, end = month_bounds(cursor_date)
            suffix = start.strftime("%Y%m")
            for table in TABLES:
                name = f"{table}_{suffix}"
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "
                    f"FOR VALUES FROM (%s) TO (%s)", [start, end]
                )
                made.append(name)
            cursor_date = end
    return made


class Command(BaseCommand):
    help = "Create month partitions for the log tables."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=3)

    def handle(self, *args, **opts):
        made = create_partitions(opts["months"])
        self.stdout.write(self.style.SUCCESS(f"{len(made)} partitions ensured"))
