import pytest
from datetime import timedelta
from django.utils import timezone
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search import query


def _index(**kw):
    d = dict(
        source="gazette", source_key="1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
    )
    d.update(kw)
    upsert_drafts([DocumentDraft(**d)])


@pytest.mark.django_db
def test_thaana_query_finds_a_thaana_document():
    _index(title_dv="ވަޒީފާގެ ފުރުޞަތު", text_dv="ވަޒީފާގެ ފުރުޞަތު")
    assert len(query.search("ވަޒީފާގެ")) == 1


@pytest.mark.django_db
def test_keyboard_query_finds_the_same_thaana_document():
    _index(title_dv="މިގޮތައް", text_dv="މިގޮތައް")
    assert len(query.search("migotawq")) == 1


@pytest.mark.django_db
def test_mis_filied_query_still_matches_via_the_skeleton():
    """Recall half of the dual weighting (spec 6.2)."""
    _index(title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    assert len(query.search("ހިކަތި")) == 1


@pytest.mark.django_db
def test_correctly_filied_query_outranks_a_skeleton_collision():
    """Precision half. This is the minimal-pair regression guard from spec 14 --
    if it fails, skeleton indexing has silently taken over."""
    _index(source_key="1", title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    _index(source_key="2", title_dv="ހިކަތި", text_dv="ހިކަތި")
    results = query.search("ހަކަތަ")
    assert [r.source_key for r in results][:1] == ["1"]
    assert len(results) == 2, "the collision should still be found, just lower"


@pytest.mark.django_db
def test_phonetic_latin_query_finds_a_thaana_document():
    _index(title_dv="ކުއްޔަށް", text_dv="ކުއްޔަށް ދިނުން")
    assert len(query.search("dhinun")) >= 1


@pytest.mark.django_db
def test_english_search_still_works():
    _index(title_en="Washing machine", text_en="washing machine for sale")
    assert len(query.search("washing")) == 1


@pytest.mark.django_db
def test_same_language_match_outranks_a_cross_language_one():
    _index(source_key="1", title_dv="ފޯނު", text_dv="ފޯނު")
    _index(source_key="2", title_latin="fonu", text_latin="fonu")
    results = query.search("ފޯނު")
    assert results[0].source_key == "1"


@pytest.mark.django_db
def test_fresher_documents_rank_higher_all_else_equal():
    now = timezone.now()
    _index(source_key="1", title_en="Ferry notice", text_en="ferry notice",
           published_at=now - timedelta(days=200))
    _index(source_key="2", title_en="Ferry notice", text_en="ferry notice",
           published_at=now)
    assert [r.source_key for r in query.search("ferry")] == ["2", "1"]


@pytest.mark.django_db
def test_response_language_follows_the_query():
    _index(title_dv="ވަޒީފާ", text_dv="ވަޒީފާ")
    assert query.plan_for("ވަޒީފާ").response_lang == "dv"
    assert query.plan_for("washing").response_lang == "en"


@pytest.mark.django_db
def test_result_reports_which_language_matched():
    _index(title_dv="ވަޒީފާ", text_dv="ވަޒީފާ")
    assert query.search("ވަޒީފާ")[0].matched_lang == "dv"


@pytest.mark.django_db
def test_empty_query_returns_nothing():
    _index(title_en="anything", text_en="anything")
    assert query.search("") == []
