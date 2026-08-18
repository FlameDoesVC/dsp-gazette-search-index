from django.core.management.base import BaseCommand

from search.models import SpecKey
from search.specs.seed_data import SEED_KEYS


class Command(BaseCommand):
    help = "Create the initial SpecKey registry rows. Never overwrites curation."

    def handle(self, *args, **opts):
        created = 0
        for entry in SEED_KEYS:
            # get_or_create, not update_or_create: an admin who changed
            # is_facetable or priority by hand must not lose it on deploy.
            _, was_created = SpecKey.objects.get_or_create(
                key=entry["key"], defaults=entry
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(
            f"{created} created, {len(SEED_KEYS) - created} already present"
        ))
