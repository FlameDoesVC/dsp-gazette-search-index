import pytest
from decimal import Decimal
from django.db import connection
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import SearchDocument


def _draft(**kw):
    d = dict(
        source="gazette",
        source_key="1",
        doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
        title_en="Water supply notice",
        text_en="The ministry announces a water supply interruption",
    )
    d.update(kw)
    return DocumentDraft(**d)


@pytest.mark.django_db
def test_insert_creates_a_row():
    assert upsert_drafts([_draft()]) == 1
    assert SearchDocument.objects.count() == 1


@pytest.mark.django_db
def test_the_indexer_corrects_a_swapped_draft():
    upsert_drafts([DocumentDraft(
        source="gazette", source_key="IUL-1", doc_type="news", url="https://x",
        title_en="ވަޒީފާގެ ފުރުޞަތު", title_dv="Job Opportunity",
    )])
    d = SearchDocument.objects.get()
    assert d.title_en == "Job Opportunity"
    assert d.title_dv == "ވަޒީފާގެ ފުރުޞަތު"


@pytest.mark.django_db
def test_reinsert_updates_rather_than_duplicating():
    upsert_drafts([_draft()])
    upsert_drafts([_draft(title_en="Amended notice")])
    assert SearchDocument.objects.count() == 1
    assert SearchDocument.objects.get().title_en == "Amended notice"


@pytest.mark.django_db
def test_english_vector_is_populated():
    upsert_drafts([_draft()])
    doc = SearchDocument.objects.get()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT vector_en IS NOT NULL AND vector_en != '' "
            "FROM search_searchdocument WHERE id = %s",
            [doc.id],
        )
        assert cur.fetchone()[0] is True


@pytest.mark.django_db
def test_body_text_is_not_persisted():
    """Spec 12.1: only vectors, never the text they were built from."""
    upsert_drafts([_draft()])
    columns = {f.name for f in SearchDocument._meta.get_fields()}
    assert "text_en" not in columns
    assert "text_dv" not in columns


@pytest.mark.django_db
def test_reindex_clears_the_stale_flag():
    from django.utils import timezone
    upsert_drafts([_draft()])
    SearchDocument.objects.update(stale_marked_at=timezone.now())
    upsert_drafts([_draft()])
    assert SearchDocument.objects.get().stale_marked_at is None


@pytest.mark.django_db
def test_price_and_facets_survive_the_round_trip():
    upsert_drafts([_draft(source="ibay", price=Decimal("280.00"), location="Male")])
    doc = SearchDocument.objects.get(source="ibay")
    assert doc.price == Decimal("280.00")
    assert doc.location == "Male"


@pytest.mark.django_db
def test_index_is_fully_rebuildable_from_source_apps():
    """Spec 3.1: SearchDocument is a disposable projection."""
    from gazette.models import Iulaan, IulaanType
    from search.indexing import reindex_source

    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    for n in ("10", "11"):
        Iulaan.objects.create(
            id=n, title=f"Notice {n}", translated_title=f"Notice {n}",
            iulaan_type=jobs, additional_info={}, attachments={},
            body=f"<p>Body {n}</p>",
        )

    reindex_source("gazette")
    before = {
        (d.source, d.source_key, d.doc_type, d.title_en, d.content_hash)
        for d in SearchDocument.objects.filter(source="gazette")
    }
    assert before

    SearchDocument.objects.all().delete()
    assert SearchDocument.objects.count() == 0

    reindex_source("gazette")
    after = {
        (d.source, d.source_key, d.doc_type, d.title_en, d.content_hash)
        for d in SearchDocument.objects.filter(source="gazette")
    }
    assert after == before


@pytest.mark.django_db
def test_reclassification_is_an_in_place_update():
    """Spec 3.2 and 12.2: doc_type changes must not move a row between
    partitions or violate identity."""
    upsert_drafts([_draft(source_key="20", doc_type="news")])
    doc_id = SearchDocument.objects.get(source_key="20").id

    upsert_drafts([_draft(source_key="20", doc_type="job")])

    doc = SearchDocument.objects.get(source_key="20")
    assert doc.id == doc_id
    assert doc.doc_type == "job"
    assert SearchDocument.objects.filter(source_key="20").count() == 1
