from django.core.management.base import BaseCommand

from catalog.resolve import resolve_source


class Command(BaseCommand):
    help = "Group documents into canonical entities. Deterministic, free."

    def add_arguments(self, parser):
        parser.add_argument("--source", default="ibay")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        counts = resolve_source(opts["source"], limit=opts["limit"],
                                dry_run=opts["dry_run"])
        rate = (100 * counts["missed"] / counts["seen"]) if counts["seen"] else 0
        self.stdout.write(self.style.SUCCESS(
            f"{counts['seen']} seen, {counts['linked']} linked, "
            f"{counts['missed']} missed ({rate:.1f}%)"))
