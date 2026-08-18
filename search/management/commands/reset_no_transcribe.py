"""Undo the rows defect A marked terminal. One-shot, safe to re-run.

Only rows with the exact signature of the old --no-transcribe path are
touched: status='ocr_failed' AND method='none' AND transcribed=False AND
error=''. A genuine OCR failure records method='transcribed', so this cannot
resurrect a document that actually failed the vision model, and it cannot
touch a document a human marked off.
"""

from django.core.management.base import BaseCommand

from gazette.models import Attachment


class Command(BaseCommand):
    help = "Reset attachments wrongly marked ocr_failed by --no-transcribe."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        qs = Attachment.objects.filter(
            status="ocr_failed", method="none", transcribed=False, error=""
        )
        n = qs.count()
        self.stdout.write(
            f"{n} attachments carry the --no-transcribe signature "
            f"(ocr_failed / method=none / never transcribed / no error)"
        )
        if options["dry_run"]:
            return
        # page_count and chars_per_page are deliberately preserved: they are
        # the measurement, and they save a re-fetch on the next run.
        updated = qs.update(status="pending")
        self.stdout.write(self.style.SUCCESS(f"{updated} reset to pending"))
