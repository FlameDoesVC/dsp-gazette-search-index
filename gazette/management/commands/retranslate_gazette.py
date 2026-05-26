from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand

from gazette.models import Iulaan, IulaanType, Office
from gazette.translate import translate_auto_sync, sentence_boundary

_translate = translate_auto_sync


class Command(BaseCommand):
    help = "Retranslate all iulaan titles/bodies, offices, and types"

    def add_arguments(self, parser):
        parser.add_argument("--iulaans", action="store_true", help="Retranslate iulaans")
        parser.add_argument("--offices", action="store_true", help="Retranslate offices")
        parser.add_argument("--types", action="store_true", help="Retranslate iulaan types")
        parser.add_argument("--all", action="store_true", help="Retranslate everything")

    def handle(self, *args, **options):
        if not settings.GEMINI_API_KEY:
            self.stderr.write("GEMINI_API_KEY not set in .env")
            return

        do_all = options["all"] or not (
            options["iulaans"] or options["offices"] or options["types"]
        )
        if do_all or options["iulaans"]:
            self._retranslate_iulaans()
        if do_all or options["offices"]:
            self._retranslate_names(Office, "office")
        if do_all or options["types"]:
            self._retranslate_names(IulaanType, "iulaan type")

    def _retranslate_iulaans(self):
        updated = 0
        for iulaan in Iulaan.objects.exclude(title=""):
            changed = False

            trans = _translate(iulaan.title)
            if trans:
                iulaan.translated_title = trans
                changed = True
                self.stdout.write(f"  Title: {iulaan.title[:40]} → {trans[:60]}")

            if iulaan.body:
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
                iulaan.save(update_fields=["translated_title", "translated_body"])
                updated += 1

        self.stdout.write(f"Retranslated {updated} iulaans.")

    def _retranslate_names(self, model, label):
        updated = 0
        for obj in model.objects.exclude(name=""):
            trans = _translate(obj.name)
            if trans:
                obj.translated_name = trans
                obj.save(update_fields=["translated_name"])
                updated += 1
                self.stdout.write(f"  {label}: {obj.name[:40]} → {trans[:60]}")

        self.stdout.write(f"Retranslated {updated} {label}s.")
