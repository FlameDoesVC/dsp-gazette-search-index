"""Run the extraction ladder over gazette attachments. Spec 5.6.

Ladder, cheapest first:
  1. .docx via python-docx      -- free, no OCR
  2. PDF with a text layer      -- free, pdftotext
  3. scanned PDF                -- Google Vision OCR, then fili repair by a
                                   60M T5 on CPU, then a consonant-skeleton
                                   gate and an anchor check against the
                                   iulaan's own title and office
  4. give up, record ocr_failed

Rung 3 replaced a Claude-native-PDF rung that fabricated on real scans (0%
anchor overlap). See docs/superpowers/measurements/2026-08-18-p3-attachments.md.

Order the corpus jobs-first: jobs are where attachment detail is load-bearing,
and only 16 of 306 iulaan state salary in the body.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from gazette.models import Attachment, Iulaan
from search.adapters.gazette import IULAAN_TYPE_DOC_TYPE
from search.extract import local, transcribe
from search.extract.fetch import fetch_bytes, sync_attachments
from search.models import SearchDocument

_TERMINAL = {"ok", "ocr_failed"}


class Command(BaseCommand):
    help = "Fetch and extract text from gazette attachments."

    def add_arguments(self, parser):
        parser.add_argument("--type", dest="doc_type", default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--no-transcribe",
            action="store_true",
            help="Measure the scanned fraction without spending. Leaves "
                 "scanned PDFs pending so a later paid run still picks them up.",
        )
        parser.add_argument(
            "--stale",
            action="store_true",
            help="Reprocess documents marked stale, overriding the guard.",
        )

    def handle(self, *args, **options):
        # Discovery is global and idempotent: the type filter restricts which
        # attachments get processed, not which are recorded.
        iulaan_qs = Iulaan.objects.all()

        stale_keys: set[str] = set()
        if options["stale"]:
            stale_keys = set(
                SearchDocument.objects.filter(
                    source="gazette", stale_marked_at__isnull=False
                ).values_list("source_key", flat=True)
            )
            iulaan_qs = iulaan_qs.filter(id__in=stale_keys)

        discovered = 0
        for iulaan in iulaan_qs.iterator(chunk_size=200):
            discovered += sync_attachments(iulaan)
        self.stdout.write(f"discovered {discovered} attachment references")

        pending = Attachment.objects.select_related("iulaan")
        if options["stale"]:
            pending = pending.filter(iulaan_id__in=stale_keys)
        else:
            pending = pending.exclude(status__in=_TERMINAL)
        # Blank application forms are not body text.
        pending = pending.exclude(role="application_form")

        if options["doc_type"]:
            if options["doc_type"] == "news":
                pending = pending.filter(
                    Q(iulaan__iulaan_type__isnull=True)
                    | ~Q(iulaan__iulaan_type__name__in=IULAAN_TYPE_DOC_TYPE)
                )
            else:
                wanted = {
                    name for name, dt in IULAAN_TYPE_DOC_TYPE.items()
                    if dt == options["doc_type"]
                }
                pending = pending.filter(
                    iulaan__iulaan_type__name__in=wanted
                )

        if options["limit"]:
            pending = pending[: options["limit"]]

        done = 0
        scanned = 0
        local_ok = 0

        for attachment in pending.iterator(chunk_size=100):
            attachment.attempts += 1
            fetched = fetch_bytes(attachment.url)
            if not fetched:
                attachment.status = "fetch_failed"
                attachment.save(update_fields=["status", "attempts", "updated_at"])
                continue
            content, sha = fetched
            attachment.content_sha = sha
            attachment.size_bytes = len(content)

            if attachment.url.lower().endswith(".docx"):
                result = local.extract_docx(content)
            elif attachment.url.lower().endswith(".pdf"):
                result = local.extract_pdf_text_layer(content)
            else:
                result = local.ExtractionResult(
                    status="skipped", error="unsupported type"
                )

            if result.method == "pdftotext" and local.needs_transcription(result):
                if options["no_transcribe"]:
                    # Record the routing decision, spend nothing, and leave the
                    # attachment reprocessable. `ocr_failed` here would be
                    # terminal (spec 5.7) and would silently disable the paid
                    # run this measurement exists to budget for.
                    attachment.status = "pending"
                    attachment.page_count = result.page_count
                    attachment.chars_per_page = result.chars_per_page
                    attachment.save()
                    scanned += 1
                    continue
                attachment.page_count = result.page_count
                attachment.chars_per_page = result.chars_per_page
                result = transcribe.transcribe_pdf(
                    content,
                    title=attachment.iulaan.title,
                    office=(attachment.iulaan.office.name
                            if attachment.iulaan.office else ""),
                    page_count=result.page_count,
                )

            self._store(attachment, result)
            if result.method == "transcribed":
                done += 1
            else:
                local_ok += 1

        if options["no_transcribe"]:
            total = scanned + local_ok
            pct = (100.0 * scanned / total) if total else 0.0
            self.stdout.write(self.style.SUCCESS(
                f"measured {total} PDFs: {scanned} scanned ({pct:.1f}%), "
                f"{local_ok} had a text layer. Nothing was spent."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f"extracted {done} attachments"))

    def _store(self, attachment: Attachment, result) -> None:
        attachment.text = (result.text or "")[: Attachment.TEXT_CAP]
        attachment.method = result.method
        attachment.transcribed = getattr(result, "transcribed", False)
        attachment.error = (result.error or "")[:2000]
        if result.page_count is not None:
            attachment.page_count = result.page_count
        if result.chars_per_page is not None:
            attachment.chars_per_page = result.chars_per_page
        if result.status == "ok" and attachment.text:
            attachment.status = "ok"
        elif result.method == "transcribed":
            # The vision model ran and produced nothing usable. Terminal:
            # retrying re-bills the same document.
            attachment.status = "ocr_failed"
        else:
            # The bytes arrived; the parser could not use them. Not terminal,
            # but not a fetch failure either.
            attachment.status = "extract_failed"
        attachment.save()
