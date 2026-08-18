from django.core.management.base import BaseCommand

from search.specs.project import prune_orphans, sync_specs


class Command(BaseCommand):
    help = "Project attrs and scraped ProductInfo into the DocumentSpec table."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--type", dest="doc_type", default="shopping")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--prune", action="store_true",
                            help="Also delete rows whose document is gone.")

    def handle(self, *args, **opts):
        if opts["prune"]:
            n = prune_orphans()
            self.stdout.write(f"pruned {n} orphan spec rows")
        counts = sync_specs(source=opts["source"], doc_type=opts["doc_type"],
                            limit=opts["limit"])
        self.stdout.write(self.style.SUCCESS(
            f"{counts['documents']} documents, {counts['specs']} spec rows"
        ))
