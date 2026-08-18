from django.core.management.base import BaseCommand

from search.suggest import rebuild_terms


class Command(BaseCommand):
    help = "Rebuild the autocomplete term table from current document titles."

    def handle(self, *args, **opts):
        n = rebuild_terms()
        self.stdout.write(self.style.SUCCESS(f"{n} terms"))
