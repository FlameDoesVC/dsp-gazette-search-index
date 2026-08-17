import pytest
from django.core.management import call_command
from django.utils import timezone

from search.models import SearchDocument


@pytest.mark.django_db
def test_dry_run_reports_the_count_and_spends_nothing(capsys):
    """Spec 5.7: a WHERE clause can mark 51,000 rows as easily as one, so the
    command reports what it is about to process before spending anything."""
    for i in range(3):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      content_hash="h")
    call_command("enrich_documents", "--source", "ibay", "--dry-run")
    out = capsys.readouterr().out
    assert "3" in out
    from enrich.models import EnrichedRecord
    assert EnrichedRecord.objects.count() == 0


@pytest.mark.django_db
def test_limit_is_respected(capsys):
    for i in range(10):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      content_hash="h")
    call_command("enrich_documents", "--source", "ibay", "--limit", "4", "--dry-run")
    assert "4" in capsys.readouterr().out


@pytest.mark.django_db
def test_the_command_does_not_clear_stale_marked_at(capsys, monkeypatch):
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h",
                                  stale_marked_at=timezone.now())
    call_command("enrich_documents", "--source", "ibay", "--stale", "--dry-run")
    assert SearchDocument.objects.get().stale_marked_at is not None
