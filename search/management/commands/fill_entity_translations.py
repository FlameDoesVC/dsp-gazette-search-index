"""Populate Office.translated_name and IulaanType.translated_name.

183 rows, once. Every gazette document references an office and a type, so
this makes `employer` and `announcement_type` bilingual for the whole corpus
-- now and at 51,000 iulaan -- without a single per-document translation.

The columns already exist and the gazette adapter already reads them; they
have simply never been populated, so `office_en` has been falling back to the
Thaana name and polluting the English search vector.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from gazette.models import IulaanType, Office

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Translate office and iulaan-type names once (183 rows)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true",
                            help="Retranslate rows that already have a value.")

    def handle(self, *args, **opts):
        from core.translate import translate_dv_to_en_sync

        for model, label in ((Office, "office"), (IulaanType, "iulaan type")):
            qs = model.objects.all()
            if not opts["force"]:
                qs = qs.filter(translated_name="")
            self.stdout.write(f"{qs.count()} {label} rows to translate")
            if opts["dry_run"]:
                continue

            done = 0
            for row in qs.iterator(chunk_size=100):
                try:
                    out = translate_dv_to_en_sync(row.name[:256])
                except Exception:
                    logger.warning("translation failed for %s %s", label, row.pk,
                                   exc_info=True)
                    continue
                if out and out.strip():
                    row.translated_name = out.strip()[:255]
                    row.save(update_fields=["translated_name"])
                    done += 1
            self.stdout.write(self.style.SUCCESS(f"  {done} {label} rows filled"))

        self.stdout.write("Run `reindex --source gazette` to publish.")
