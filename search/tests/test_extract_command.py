import pytest
from io import StringIO
from django.core.management import call_command
from gazette.models import Attachment, Iulaan, IulaanType
from search.extract.local import ExtractionResult


@pytest.fixture
def job_with_pdf(db, monkeypatch):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    iulaan = Iulaan.objects.create(
        id="1", title="Job", iulaan_type=jobs, additional_info={}, body="",
        attachments={"iulaan": "https://x/1.pdf"},
    )
    from search.extract import fetch
    monkeypatch.setattr(
        fetch, "fetch_bytes", lambda url: (b"%PDF-1.4 fake", "deadbeef")
    )
    return iulaan


@pytest.mark.django_db
def test_dense_pdf_uses_the_text_layer(job_with_pdf, monkeypatch):
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="salary 10,750", page_count=2, chars_per_page=2000,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", stdout=StringIO())
    a = Attachment.objects.get()
    assert a.status == "ok"
    assert a.method == "pdftotext"
    assert a.transcribed is False
    assert "10,750" in a.text


@pytest.mark.django_db
def test_sparse_pdf_is_queued_for_transcription(job_with_pdf, monkeypatch):
    from search.extract import local, transcribe
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="", page_count=3, chars_per_page=0,
            method="pdftotext", status="ok",
        ),
    )
    monkeypatch.setattr(
        transcribe, "transcribe_batch",
        lambda items: {
            items[0].custom_id: ExtractionResult(
                text="ޓްރާންސްކްރައިބްޑް", method="transcribed",
                status="ok", transcribed=True,
            )
        },
    )
    call_command("extract_attachments", stdout=StringIO())
    a = Attachment.objects.get()
    assert a.method == "transcribed"
    assert a.transcribed is True


@pytest.mark.django_db
def test_no_transcribe_leaves_the_attachment_reprocessable(
    job_with_pdf, monkeypatch
):
    """The measurement pass must not consume the work it is measuring.

    ocr_failed is terminal (spec 5.7), so writing it here meant the paid run
    that follows silently skipped every scanned PDF. The routing decision is
    recorded; the status is not spent.
    """
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="x", page_count=3, chars_per_page=10,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", "--no-transcribe", stdout=StringIO())

    a = Attachment.objects.get()
    assert a.status == "pending"          # NOT ocr_failed
    assert a.chars_per_page == 10         # the measurement is still recorded
    assert a.page_count == 3


@pytest.mark.django_db
def test_measure_then_transcribe_actually_transcribes(job_with_pdf, monkeypatch):
    """The two-step workflow from the P3 runbook, end to end. This is the
    test whose absence let defect A ship."""
    from search.extract import local, transcribe
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="x", page_count=3, chars_per_page=10,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", "--no-transcribe", stdout=StringIO())

    def _fake_batch(items):
        assert items, "the paid run found nothing to do"
        return {
            i.custom_id: ExtractionResult(
                text="transcribed body", method="transcribed",
                status="ok", transcribed=True,
            )
            for i in items
        }

    monkeypatch.setattr(transcribe, "transcribe_batch", _fake_batch)
    call_command("extract_attachments", stdout=StringIO())

    a = Attachment.objects.get()
    assert a.status == "ok"
    assert a.transcribed is True


@pytest.mark.django_db
def test_no_transcribe_reports_the_scanned_fraction(job_with_pdf, monkeypatch):
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="x", page_count=3, chars_per_page=10,
            method="pdftotext", status="ok",
        ),
    )
    out = StringIO()
    call_command("extract_attachments", "--no-transcribe", stdout=out)
    assert "scanned" in out.getvalue().lower()


@pytest.mark.django_db
def test_already_ok_attachments_are_never_reprocessed(job_with_pdf, monkeypatch):
    """Spec 5.7: guarded by existence, because the failure mode costs money."""
    from search.extract import fetch
    call_command("extract_attachments", "--no-transcribe", stdout=StringIO())
    Attachment.objects.update(status="ok", text="already done", method="docx")

    def _explode(url):
        raise AssertionError("must not re-fetch an attachment already ok")

    monkeypatch.setattr(fetch, "fetch_bytes", _explode)
    call_command("extract_attachments", stdout=StringIO())
    assert Attachment.objects.get().text == "already done"


@pytest.mark.django_db
def test_stale_flag_overrides_the_existence_guard(job_with_pdf, monkeypatch):
    from django.utils import timezone
    from search.models import SearchDocument
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="fresh text", page_count=1, chars_per_page=900,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", stdout=StringIO())
    Attachment.objects.update(text="old text")
    SearchDocument.objects.create(
        source="gazette", source_key="1", doc_type="job",
        url="https://gazette.gov.mv/iulaan/1",
        stale_marked_at=timezone.now(),
    )
    call_command("extract_attachments", "--stale", stdout=StringIO())
    assert Attachment.objects.get().text == "fresh text"


@pytest.mark.django_db
def test_type_filter_restricts_the_run(job_with_pdf):
    call_command(
        "extract_attachments", "--type", "news", "--no-transcribe",
        stdout=StringIO(),
    )
    assert Attachment.objects.filter(status="pending").count() == 1


@pytest.fixture
def job_with_docx(db, monkeypatch):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    iulaan = Iulaan.objects.create(
        id="2", title="Job", iulaan_type=jobs, additional_info={}, body="",
        attachments={"iulaan": "https://x/2.docx"},
    )
    from search.extract import fetch
    monkeypatch.setattr(
        fetch, "fetch_bytes", lambda url: (b"PK\x03\x04 fake", "c0ffee")
    )
    return iulaan


@pytest.mark.django_db
def test_a_local_extraction_failure_is_not_recorded_as_a_fetch_failure(
    job_with_docx, monkeypatch
):
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_docx",
        lambda c: ExtractionResult(method="docx", status="failed",
                                   error="not a zip file"),
    )
    call_command("extract_attachments", stdout=StringIO())
    a = Attachment.objects.get()
    assert a.status == "extract_failed"
    assert "not a zip" in a.error


@pytest.mark.django_db
def test_the_transcription_queue_respects_batch_size(db, monkeypatch):
    """A run of consecutive scanned PDFs must not accumulate unbounded --
    every queued item holds the whole file in memory (spec 12.4)."""
    from search.extract import local, transcribe
    from gazette.models import Iulaan

    iulaan = Iulaan.objects.create(id="IUL-1", title="t", additional_info={},
                                   attachments=[], body="b")
    for i in range(7):
        Attachment.objects.create(iulaan=iulaan, url=f"https://x/{i}.pdf",
                                  role="main", status="pending")

    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(text="x", page_count=3, chars_per_page=10,
                                   method="pdftotext", status="ok"),
    )
    monkeypatch.setattr(
        "search.extract.fetch.fetch_bytes", lambda url: (b"pdf", "sha")
    )

    seen = []

    def _fake_batch(items):
        seen.append(len(items))
        return {
            i.custom_id: ExtractionResult(text="t", method="transcribed",
                                          status="ok", transcribed=True)
            for i in items
        }

    monkeypatch.setattr(transcribe, "transcribe_batch", _fake_batch)
    call_command("extract_attachments", "--batch-size", "3", stdout=StringIO())

    assert seen, "nothing was transcribed"
    assert max(seen) <= 3, f"batch grew to {max(seen)}, cap was 3"
