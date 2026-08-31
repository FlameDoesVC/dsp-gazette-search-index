import pytest

from search.models import DocumentReport, SearchDocument


@pytest.fixture
def doc(db):
    return SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                         doc_type="news", url="https://x")


def _post(api, doc_id, reason="stale", note="", ip="203.0.113.1"):
    return api.post(f"/api/v1/documents/{doc_id}/report",
                    data={"reason": reason, "note": note},
                    content_type="application/json", REMOTE_ADDR=ip)


@pytest.mark.django_db
def test_a_report_is_accepted_and_queued(api, doc):
    assert _post(api, doc.id).status_code == 202
    r = DocumentReport.objects.get()
    assert r.reason == "stale"
    assert r.status == "open"
    assert r.reporter_ip_hash and "203.0.113.1" not in r.reporter_ip_hash


@pytest.mark.django_db
def test_a_report_never_marks_the_document_stale(api, doc):
    """The endpoint is public and reprocessing costs real money per document,
    so auto-reprocessing would be a billable denial-of-wallet vector. Reports
    are inert data; an admin action re-queues. Spec 5.7."""
    _post(api, doc.id)
    doc.refresh_from_db()
    assert doc.stale_marked_at is None


@pytest.mark.django_db
def test_a_duplicate_report_returns_202_and_creates_nothing(api, doc):
    """Always 202, new or duplicate: telling a caller which documents they
    have already reported leaks nothing useful and invites probing. Spec 9."""
    _post(api, doc.id)
    assert _post(api, doc.id).status_code == 202
    assert DocumentReport.objects.count() == 1


@pytest.mark.django_db
def test_a_different_reason_from_the_same_reporter_is_a_new_report(api, doc):
    _post(api, doc.id, reason="stale")
    _post(api, doc.id, reason="dead_link")
    assert DocumentReport.objects.count() == 2


@pytest.mark.django_db
def test_reports_are_rate_limited_per_ip(api, doc, settings):
    settings.REPORT_RATE_LIMIT = 3
    for i in range(3):
        d = SearchDocument.objects.create(source="other", source_key=f"r{i}",
                                          doc_type="shopping", url="https://x")
        assert _post(api, d.id).status_code == 202
    d = SearchDocument.objects.create(source="other", source_key="over",
                                      doc_type="shopping", url="https://x")
    r = _post(api, d.id)
    assert r.status_code == 202                 # still 202, deliberately
    assert not DocumentReport.objects.filter(document_id=d.id).exists()


@pytest.mark.django_db
def test_a_report_on_a_missing_document_is_202_and_creates_nothing(api, doc):
    assert _post(api, 999999).status_code == 202
    assert DocumentReport.objects.count() == 0


@pytest.mark.django_db
def test_an_invalid_reason_is_rejected(api, doc):
    r = api.post(f"/api/v1/documents/{doc.id}/report",
                 data={"reason": "i just dont like it"},
                 content_type="application/json")
    assert r.status_code == 422


@pytest.mark.django_db
def test_the_note_is_truncated_not_rejected(api, doc):
    _post(api, doc.id, note="x" * 10_000)
    assert len(DocumentReport.objects.get().note) <= 2000
