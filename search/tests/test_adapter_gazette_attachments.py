import pytest
from gazette.models import Attachment, Iulaan, IulaanType
from search.adapters.gazette import GazetteAdapter


@pytest.fixture
def iulaan(db):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    return Iulaan.objects.create(
        id="1", title="ވަޒީފާގެ ފުރުޞަތު", translated_title="Job Opportunity",
        iulaan_type=jobs, additional_info={}, attachments={},
        body='<table><tr><td><strong>އަސާސީ މުސާރަ:</strong></td>'
             '<td>މަހަކު 10,750 ރުފިޔާ</td></tr></table>',
    )


def _draft(iulaan):
    a = GazetteAdapter()
    return a.to_document(a.fetch_raw(iulaan.id))


@pytest.mark.django_db
def test_attachment_text_reaches_the_indexed_text(iulaan):
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="pdftotext", text="ޤަވާޢިދު ސާފުކުރުން 4,400",
    )
    assert "4,400" in _draft(iulaan).text_dv


@pytest.mark.django_db
def test_application_form_text_is_excluded(iulaan):
    """A blank form must not become the job description (spec 5.6)."""
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/2.pdf", role="application_form",
        status="ok", method="pdftotext", text="FORM BOILERPLATE ONLY",
    )
    assert "FORM BOILERPLATE" not in _draft(iulaan).text_dv


@pytest.mark.django_db
def test_failed_attachments_contribute_nothing(iulaan):
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/3.pdf", role="main",
        status="ocr_failed", method="transcribed", text="",
    )
    draft = _draft(iulaan)
    assert draft.card["detail_source"] == "listing"


@pytest.mark.django_db
def test_table_pairs_are_folded_into_the_text(iulaan):
    assert "10,750" in _draft(iulaan).text_dv


@pytest.mark.django_db
def test_card_reports_when_details_came_from_an_attachment(iulaan):
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="pdftotext", text="detail text here",
    )
    assert _draft(iulaan).card["detail_source"] == "attachment"


@pytest.mark.django_db
def test_transcribed_provenance_lowers_quality_and_flags_the_card(iulaan):
    """Spec 5.6.1: a salary read off a photographed letter is not the same
    claim as one read off a clean Word export."""
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="transcribed", transcribed=True,
        text="ޓްރާންސްކްރައިބްޑް ޓެކްސްޓް",
    )
    draft = _draft(iulaan)
    assert draft.card["transcribed"] is True
    assert draft.attrs["transcribed"] is True

    Attachment.objects.update(transcribed=False, method="pdftotext")
    clean = _draft(iulaan)
    assert clean.quality > draft.quality


@pytest.mark.django_db
def test_content_hash_covers_attachment_checksums(iulaan):
    """Spec 5.6: a re-published PDF must trigger re-enrichment."""
    before = _draft(iulaan).content_hash
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="pdftotext", text="new", content_sha="abc123",
    )
    assert _draft(iulaan).content_hash != before
