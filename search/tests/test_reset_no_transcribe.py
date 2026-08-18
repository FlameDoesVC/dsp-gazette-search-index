from io import StringIO

import pytest
from django.core.management import call_command

from gazette.models import Attachment, Iulaan


@pytest.fixture
def iulaan(db):
    return Iulaan.objects.create(id="IUL-1", title="t", additional_info={},
                                 attachments=[], body="b")


def _att(iulaan, **kw):
    base = dict(iulaan=iulaan, url=f"https://x/{kw.get('url_n', 0)}.pdf",
                role="main")
    base.pop("url_n", None)
    kw.pop("url_n", None)
    base.update(kw)
    return Attachment.objects.create(**base)


@pytest.mark.django_db
def test_the_no_transcribe_signature_is_reset(iulaan):
    _att(iulaan, url="https://x/1.pdf", status="ocr_failed", method="none",
         transcribed=False, error="", page_count=3, chars_per_page=10)
    call_command("reset_no_transcribe", stdout=StringIO())
    a = Attachment.objects.get()
    assert a.status == "pending"
    assert a.chars_per_page == 10       # the measurement survives


@pytest.mark.django_db
def test_a_genuine_ocr_failure_is_left_alone(iulaan):
    """It went to the vision model and came back unusable. That is terminal,
    and resurrecting it would re-bill the same document."""
    _att(iulaan, url="https://x/2.pdf", status="ocr_failed",
         method="transcribed", transcribed=True, error="CER 0.42")
    call_command("reset_no_transcribe", stdout=StringIO())
    assert Attachment.objects.get().status == "ocr_failed"


@pytest.mark.django_db
def test_an_ok_attachment_is_never_touched(iulaan):
    _att(iulaan, url="https://x/3.pdf", status="ok", method="pdftotext",
         text="body")
    call_command("reset_no_transcribe", stdout=StringIO())
    assert Attachment.objects.get().status == "ok"


@pytest.mark.django_db
def test_dry_run_changes_nothing(iulaan):
    _att(iulaan, url="https://x/4.pdf", status="ocr_failed", method="none",
         transcribed=False, error="")
    call_command("reset_no_transcribe", "--dry-run", stdout=StringIO())
    assert Attachment.objects.get().status == "ocr_failed"


@pytest.mark.django_db
def test_rerunning_is_a_no_op(iulaan):
    _att(iulaan, url="https://x/5.pdf", status="ocr_failed", method="none",
         transcribed=False, error="")
    call_command("reset_no_transcribe", stdout=StringIO())
    call_command("reset_no_transcribe", stdout=StringIO())
    assert Attachment.objects.get().status == "pending"
