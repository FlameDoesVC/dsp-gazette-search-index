import pytest
from django.db import IntegrityError, connection
from search.models import SearchDocument


def _make(**kw):
    defaults = dict(
        source="gazette",
        source_key="407890",
        doc_type="news",
        url="https://gazette.gov.mv/iulaan/407890",
        title_en="Test notice",
    )
    defaults.update(kw)
    return SearchDocument.objects.create(**defaults)


@pytest.mark.django_db
def test_can_create_and_read_back():
    doc = _make()
    assert SearchDocument.objects.get(pk=doc.pk).title_en == "Test notice"


@pytest.mark.django_db
def test_source_and_source_key_are_unique_together():
    _make()
    with pytest.raises(IntegrityError):
        _make()


@pytest.mark.django_db
def test_same_source_key_allowed_under_a_different_source():
    _make(source="gazette", source_key="1")
    _make(source="other", source_key="1")
    assert SearchDocument.objects.filter(source_key="1").count() == 2


@pytest.mark.django_db
def test_doc_type_is_mutable_and_stays_in_the_same_partition():
    """Reclassification (spec 3.2) must be a plain UPDATE. If doc_type were the
    partition key this would migrate the row between partitions."""
    doc = _make(doc_type="news")
    doc.doc_type = "job"
    doc.save(update_fields=["doc_type"])
    assert SearchDocument.objects.get(pk=doc.pk).doc_type == "job"


@pytest.mark.django_db
def test_table_is_partitioned_by_source():
    with connection.cursor() as cur:
        cur.execute("""
            SELECT pg_get_partkeydef('search_searchdocument'::regclass)
        """)
        assert cur.fetchone()[0] == "LIST (source)"


@pytest.mark.django_db
def test_partitions_exist_for_gazette_and_a_default():
    with connection.cursor() as cur:
        cur.execute("""
            SELECT c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = 'search_searchdocument'::regclass
            ORDER BY c.relname
        """)
        names = [r[0] for r in cur.fetchall()]
    assert "search_searchdocument_gazette" in names
    assert "search_searchdocument_default" in names
