from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand

from gazette.sync_service import (MAX_INDEX_PAGES, STOP_AFTER_SEEN_PAGES,
                                  sync_all)


class Command(BaseCommand):
    help = "Run a single gazette sync cycle"

    def add_arguments(self, parser):
        parser.add_argument(
            "--full", action="store_true",
            help="Crawl every page. Disables the early stop that ends the run "
                 "after consecutive pages holding nothing new, and lifts the "
                 "page cap. Use for a deliberate backfill.")
        parser.add_argument(
            "--max-pages", type=int, default=None,
            help=f"Index pages to check (default {MAX_INDEX_PAGES}). 0 for no cap.")
        parser.add_argument(
            "--stop-after-seen", type=int, default=None,
            help=f"Stop after this many consecutive pages with nothing new "
                 f"(default {STOP_AFTER_SEEN_PAGES}). 0 disables.")

    def handle(self, *args, **options):
        max_pages = options["max_pages"]
        stop_after_seen = options["stop_after_seen"]
        if options["full"]:
            # --full means every page, so both guards come off. An explicit
            # --max-pages alongside it still wins, since asking for both is a
            # narrower instruction than asking for everything.
            max_pages = 0 if max_pages is None else max_pages
            stop_after_seen = 0 if stop_after_seen is None else stop_after_seen
        async_to_sync(sync_all)(max_pages=max_pages,
                                stop_after_seen=stop_after_seen)
