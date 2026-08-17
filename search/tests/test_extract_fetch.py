import hashlib
import pytest
from gazette.models import Attachment, Iulaan
from search.extract import fetch


@pytest.fixture
def iulaan(db):
    return Iulaan.objects.create(
        id="1", title="Notice", additional_info={}, body="",
        attachments={
            "iulaan": "https://storage.googleapis.com/gazette.gov.mv/docs/iulaan/1.pdf",
            "vazeefa ah edhey form": "https://storage.googleapis.com/gazette.gov.mv/docs/iulaan/2.pdf",
        },
    )


@pytest.mark.django_db
def test_sync_creates_one_row_per_attachment(iulaan):
    assert fetch.sync_attachments(iulaan) == 2
    assert Attachment.objects.filter(iulaan=iulaan).count() == 2


@pytest.mark.django_db
def test_sync_assigns_roles_from_labels(iulaan):
    fetch.sync_attachments(iulaan)
    roles = set(Attachment.objects.values_list("role", flat=True))
    assert roles == {"main", "application_form"}


@pytest.mark.django_db
def test_sync_is_idempotent(iulaan):
    fetch.sync_attachments(iulaan)
    fetch.sync_attachments(iulaan)
    assert Attachment.objects.filter(iulaan=iulaan).count() == 2


@pytest.mark.django_db
def test_sync_handles_an_empty_attachments_dict(db):
    empty = Iulaan.objects.create(
        id="2", title="No files", additional_info={}, body="", attachments={}
    )
    assert fetch.sync_attachments(empty) == 0


@pytest.mark.django_db
def test_sync_records_the_guessed_mime(iulaan):
    fetch.sync_attachments(iulaan)
    assert all(
        a.mime == "application/pdf" for a in Attachment.objects.all()
    )


def test_fetch_bytes_returns_content_and_sha(monkeypatch):
    payload = b"%PDF-1.4 fake"

    class _Resp:
        status_code = 200
        content = payload

        def raise_for_status(self):
            pass

    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: _Resp())
    content, sha = fetch.fetch_bytes("https://x/1.pdf")
    assert content == payload
    assert sha == hashlib.sha256(payload).hexdigest()


def test_fetch_bytes_returns_none_on_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch.httpx, "get", _boom)
    assert fetch.fetch_bytes("https://x/1.pdf") is None


def test_fetch_bytes_refuses_oversized_files(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"x" * (fetch.MAX_BYTES + 1)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: _Resp())
    assert fetch.fetch_bytes("https://x/big.pdf") is None
