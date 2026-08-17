import pytest
from django.db import connection
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import SearchDocument


def _index(**kw):
    d = dict(
        source="gazette", source_key="1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
        title_dv="ވަޒީފާގެ ފުރުޞަތު",
        text_dv="ވަޒީފާގެ ފުރުޞަތު މިނިސްޓްރީ",
    )
    d.update(kw)
    upsert_drafts([DocumentDraft(**d)])
    return SearchDocument.objects.get(source_key=d["source_key"])


def _vector(doc, column):
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {column}::text FROM search_searchdocument WHERE id = %s",
            [doc.id],
        )
        return cur.fetchone()[0] or ""


@pytest.mark.django_db
def test_dv_vector_is_populated():
    doc = _index()
    assert _vector(doc, "vector_dv")


@pytest.mark.django_db
def test_dv_vector_contains_both_fili_and_skeleton_forms():
    """Dual weighting, spec 6.2: A carries fili-preserved lexemes, C the
    consonant skeleton."""
    doc = _index(title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    vec = _vector(doc, "vector_dv")
    assert "ހަކަތަ" in vec
    assert "ހކތ" in vec
    assert ":" in vec and "A" in vec


@pytest.mark.django_db
def test_latin_vector_and_title_are_populated_from_thaana():
    doc = _index()
    assert _vector(doc, "vector_latin")
    assert doc.title_latin
    assert "ވ" not in doc.title_latin


@pytest.mark.django_db
def test_no_body_text_column_appeared():
    """Spec 12.1 still holds after adding Dhivehi indexing."""
    columns = {f.name for f in SearchDocument._meta.get_fields()}
    assert "text_dv" not in columns
    assert "text_latin" not in columns


@pytest.mark.django_db
def test_english_only_document_gets_no_dv_vector():
    doc = _index(source_key="2", title_dv="", text_dv="",
                 title_en="Washing machine", text_en="washing machine")
    assert not _vector(doc, "vector_dv").strip()


@pytest.mark.django_db
def test_skeleton_mode_omits_the_fili_form(settings):
    settings.SEARCH_DV_INDEX_MODE = "skeleton"
    doc = _index(source_key="3", title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    vec = _vector(doc, "vector_dv")
    assert "ހކތ" in vec
    assert "ހަކަތަ" not in vec
