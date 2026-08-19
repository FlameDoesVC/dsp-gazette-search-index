from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand

from ibay.sync_service import (MAX_PAGES_PER_CATEGORY, STOP_AFTER_SEEN_PAGES,
                               sync_all)


class Command(BaseCommand):
    help = "Run a single ibay sync cycle"

    def add_arguments(self, parser):
        parser.add_argument(
            "--full", action="store_true",
            help="Crawl every page of every category. Disables the early stop "
                 "that ends a category after consecutive pages holding nothing "
                 "new, and lifts the page cap. Use for a deliberate backfill.")
        parser.add_argument(
            "--max-pages", type=int, default=None,
            help=f"Pages per category (default "
                 f"{MAX_PAGES_PER_CATEGORY or 'unlimited'}). 0 for no cap.")
        parser.add_argument(
            "--stop-after-seen", type=int, default=None,
            help=f"Stop a category after this many consecutive pages with "
                 f"nothing new (default {STOP_AFTER_SEEN_PAGES}). 0 disables.")

    def handle(self, *args, **options):
        max_pages = options["max_pages"]
        stop_after_seen = options["stop_after_seen"]
        if options["full"]:
            max_pages = 0 if max_pages is None else max_pages
            stop_after_seen = 0 if stop_after_seen is None else stop_after_seen
        async_to_sync(sync_all)(max_pages=max_pages,
                                stop_after_seen=stop_after_seen)
