import pytest
from io import StringIO
from django.core.management import call_command
from gazette.models import Iulaan, IulaanType
from search.models import SearchDocument


@pytest.fixture
def two_iulaan(db):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    for n in ("1", "2"):
        Iulaan.objects.create(
            id=n, title=f"Notice {n}", translated_title=f"Notice {n}",
            iulaan_type=jobs, additional_info={}, attachments={},
            body=f"<p>Body {n}</p>",
        )


@pytest.mark.django_db
def test_reindex_indexes_a_source(two_iulaan):
    out = StringIO()
    call_command("reindex", "--source", "gazette", stdout=out)
    assert SearchDocument.objects.filter(source="gazette").count() == 2
    assert "gazette" in out.getvalue()


@pytest.mark.django_db
def test_reindex_respects_limit(two_iulaan):
    call_command("reindex", "--source", "gazette", "--limit", "1", stdout=StringIO())
    assert SearchDocument.objects.filter(source="gazette").count() == 1


@pytest.mark.django_db
def test_reindex_rejects_an_unknown_source(two_iulaan):
    from django.core.management.base import CommandError
    with pytest.raises(CommandError):
        call_command("reindex", "--source", "nope", stdout=StringIO())


@pytest.mark.django_db
def test_stale_only_pass_touches_only_marked_rows(two_iulaan):
    from django.utils import timezone
    call_command("reindex", "--source", "gazette", stdout=StringIO())
    SearchDocument.objects.filter(source_key="1").update(
        stale_marked_at=timezone.now(), title_en="STALE"
    )
    call_command("reindex", "--source", "gazette", "--stale", stdout=StringIO())
    assert SearchDocument.objects.get(source_key="1").title_en == "Notice 1"
    assert SearchDocument.objects.get(source_key="1").stale_marked_at is None
