"""Fill contact_phone and card['phone'] without a reindex.

Same reasoning as map_categories: this is free and reindex is not.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from search.contacts import primary_phone, strip_phones
from search.models import SearchDocument


class Command(BaseCommand):
    help = "Backfill contact_phone and card['phone'] from existing text."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
        if opts["source"]:
            qs = qs.filter(source=opts["source"])

        found = 0
        batch: list[SearchDocument] = []
        for doc in qs.only("id", "title_en", "title_dv", "summary_en",
                           "summary_dv", "card", "contact_phone").iterator(
                               chunk_size=500):
            phone = primary_phone(doc.title_en, doc.title_dv,
                                  doc.summary_en, doc.summary_dv)
            if not phone:
                continue
            found += 1
            doc.contact_phone = phone
            card = dict(doc.card or {})
            card["phone"] = phone
            if card.get("title"):
                card["title"] = strip_phones(card["title"])
            doc.card = card
            batch.append(doc)
            if len(batch) >= 500 and not opts["dry_run"]:
                SearchDocument.objects.using(settings.STREAM_DB_ALIAS).bulk_update(
                    batch, ["contact_phone", "card"])
                batch.clear()

        if batch and not opts["dry_run"]:
            SearchDocument.objects.using(settings.STREAM_DB_ALIAS).bulk_update(
                batch, ["contact_phone", "card"])

        self.stdout.write(self.style.SUCCESS(f"{found} documents with a phone"))
