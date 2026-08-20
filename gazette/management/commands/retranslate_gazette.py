from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError
from django.db.models import Q

from gazette.models import Iulaan, IulaanType, Office
from core.translate import translate_auto_sync, sentence_boundary

_translate = translate_auto_sync


class Command(BaseCommand):
    help = "Retranslate all iulaan titles/bodies, offices, and types"

    def add_arguments(self, parser):
        parser.add_argument("--iulaans", action="store_true", help="Retranslate iulaans")
        parser.add_argument("--offices", action="store_true", help="Retranslate offices")
        parser.add_argument("--types", action="store_true", help="Retranslate iulaan types")
        parser.add_argument("--all", action="store_true", help="Retranslate everything")
        parser.add_argument(
            "--only-missing", action="store_true",
            help="Translate only what has no translation yet. Makes the command "
                 "resumable, and fills gaps without re-doing work.")

    def handle(self, *args, **options):
        if not settings.GEMINI_API_KEY:
            self.stderr.write("GEMINI_API_KEY not set in .env")
            return

        do_all = options["all"] or not (
            options["iulaans"] or options["offices"] or options["types"]
        )
        only_missing = options["only_missing"]
        if do_all or options["iulaans"]:
            self._retranslate_iulaans(only_missing)
        if do_all or options["offices"]:
            self._retranslate_names(Office, "office", only_missing)
        if do_all or options["types"]:
            self._retranslate_names(IulaanType, "iulaan type", only_missing)

    def _retranslate_iulaans(self, only_missing=False):
        updated = skipped = failed = 0
        qs = Iulaan.objects.exclude(title="")
        if only_missing:
            qs = qs.filter(Q(translated_title="") | Q(translated_body=""))
        for iulaan in qs:
            changed = False

            if only_missing and iulaan.translated_title:
                skipped += 1
            else:
                trans = _translate(iulaan.title)
                if trans:
                    iulaan.translated_title = trans
                    changed = True
                    self.stdout.write(f"  Title: {iulaan.title[:40]} -> {trans[:60]}")

            if iulaan.body and not (only_missing and iulaan.translated_body):
                text = BeautifulSoup(iulaan.body, "html.parser").get_text().strip()
                if text:
                    parts = []
                    pos = 0
                    while pos < len(text):
                        size = sentence_boundary(text[pos:])
                        chunk = text[pos:pos + size]
                        pos += size
                        ctrans = _translate(chunk)
                        if ctrans:
                            parts.append(ctrans)
                    if parts:
                        iulaan.translated_body = " ".join(parts)
                        changed = True

            if changed:
                # Per row, because a pass over the whole corpus must not be
                # lost to one bad row. An over-long translation used to raise
                # DataError and abort everything, so a run that had already
                # translated a hundred iulaan committed none of them.
                try:
                    iulaan.save(
                        update_fields=["translated_title", "translated_body"])
                    updated += 1
                except DatabaseError as exc:
                    failed += 1
                    self.stderr.write(
                        f"  {iulaan.id}: {type(exc).__name__}: "
                        f"{str(exc).strip()[:120]}")

        self.stdout.write(f"Retranslated {updated} iulaans.")
        if skipped:
            self.stdout.write(f"Kept {skipped} titles that were already translated.")
        if failed:
            self.stdout.write(self.style.WARNING(
                f"{failed} iulaans could not be saved; see the errors above"))

    def _retranslate_names(self, model, label, only_missing=False):
        updated = 0
        qs = model.objects.exclude(name="")
        if only_missing:
            qs = qs.filter(translated_name="")
        for obj in qs:
            trans = _translate(obj.name)
            if trans:
                obj.translated_name = trans
                obj.save(update_fields=["translated_name"])
                updated += 1
                self.stdout.write(f"  {label}: {obj.name[:40]} → {trans[:60]}")

        self.stdout.write(f"Retranslated {updated} {label}s.")
