"""Run the extraction ladder over gazette attachments. Spec 5.6.

Ladder, cheapest first:
  1. .docx via python-docx           -- free, no OCR
  2. PDF with a text layer           -- free, pdftotext
  3. scanned PDF                     -- Claude Haiku 4.5, batched
  4. give up, record ocr_failed

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
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument(
            "--no-transcribe",
            action="store_true",
            help="Skip the paid rung; scanned PDFs record ocr_failed.",
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

        to_transcribe: list[transcribe.TranscriptionItem] = []
        by_id: dict[str, Attachment] = {}
        done = 0

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
                    attachment.status = "ocr_failed"
                    attachment.page_count = result.page_count
                    attachment.chars_per_page = result.chars_per_page
                    attachment.save()
                    continue
                to_transcribe.append(
                    transcribe.TranscriptionItem(
                        custom_id=str(attachment.id), content=content
                    )
                )
                by_id[str(attachment.id)] = attachment
                attachment.page_count = result.page_count
                attachment.chars_per_page = result.chars_per_page
                continue

            self._store(attachment, result)
            done += 1

            if len(to_transcribe) >= options["batch_size"]:
                done += self._flush(to_transcribe, by_id)
                to_transcribe, by_id = [], {}

        if to_transcribe:
            done += self._flush(to_transcribe, by_id)

        self.stdout.write(self.style.SUCCESS(f"extracted {done} attachments"))

    def _flush(self, items, by_id) -> int:
        self.stdout.write(f"transcribing {len(items)} scanned PDFs...")
        results = transcribe.transcribe_batch(items)
        count = 0
        for custom_id, result in results.items():
            attachment = by_id.get(custom_id)
            if attachment is None:
                continue
            self._store(attachment, result)
            count += 1 if result.status == "ok" else 0
        return count

    def _store(self, attachment: Attachment, result) -> None:
        attachment.text = (result.text or "")[: Attachment.TEXT_CAP]
        attachment.method = result.method
        attachment.transcribed = getattr(result, "transcribed", False)
        attachment.error = (result.error or "")[:2000]
        if result.page_count is not None:
            attachment.page_count = result.page_count
        if result.chars_per_page is not None:
            attachment.chars_per_page = result.chars_per_page
        attachment.status = "ok" if result.status == "ok" and attachment.text else (
            "ocr_failed" if result.method == "transcribed" else "fetch_failed"
        )
        attachment.save()
