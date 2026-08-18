import pytest
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import SearchDocument
from search import query


def _index(**kw):
    d = dict(
        source="gazette", source_key="1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
        title_en="Water supply interruption", summary_en="Ministry announcement",
        text_en="water supply interruption in Male",
    )
    d.update(kw)
    upsert_drafts([DocumentDraft(**d)])


@pytest.mark.django_db
def test_finds_a_document_by_title_term():
    _index()
    results = query.search("water")
    assert [r.source_key for r in results] == ["1"]


@pytest.mark.django_db
def test_returns_nothing_for_an_unmatched_term():
    _index()
    assert query.search("helicopter") == []


@pytest.mark.django_db
def test_stemming_works_via_the_english_config():
    _index()
    assert len(query.search("interruptions")) == 1


@pytest.mark.django_db
def test_title_match_outranks_summary_only_match():
    _index(source_key="1", title_en="Ferry schedule", summary_en="unrelated")
    _index(source_key="2", title_en="unrelated", summary_en="Ferry schedule")
    results = query.search("ferry")
    assert [r.source_key for r in results] == ["1", "2"]


@pytest.mark.django_db
def test_doc_type_filter_applies():
    _index(source_key="1", doc_type="news", title_en="Ferry schedule")
    _index(source_key="2", doc_type="job", title_en="Ferry captain wanted")
    assert [r.doc_type for r in query.search("ferry", doc_type="job")] == ["job"]


@pytest.mark.django_db
def test_inactive_documents_are_excluded():
    _index(is_active=False)
    assert query.search("water") == []


@pytest.mark.django_db
def test_empty_query_returns_nothing_rather_than_everything():
    _index()
    assert query.search("") == []
    assert query.search("   ") == []


@pytest.mark.django_db
def test_result_carries_the_card_payload():
    _index(card={"source": "gazette", "title": "Water supply interruption"})
    assert query.search("water")[0].card["source"] == "gazette"


@pytest.fixture
def job_corpus(db):
    import datetime as dt

    from django.core.management import call_command
    from django.utils import timezone

    now = timezone.now()

    def mk(**kw):
        base = dict(source="gazette", doc_type="job", url="https://x",
                    is_active=True, attrs={}, card={})
        base.update(kw)
        return SearchDocument.objects.create(**base)

    mk(source_key="1", title_en="Administrative officer role",
       expires_at=now + dt.timedelta(days=5), card={"deadline_state": "open"})
    mk(source_key="2", title_en="Senior officer vacancy",
       expires_at=now - dt.timedelta(days=2), card={"deadline_state": "closed"})
    mk(source_key="3", title_en="Undated officer posting",
       expires_at=None, card={"deadline_state": "open"})
    mk(source_key="4", title_en="Other officer job",
       expires_at=now + dt.timedelta(days=10), card={"deadline_state": "open"})
    call_command("reindex_vectors")


@pytest.mark.django_db
def test_closed_vacancies_are_hidden_from_the_jobs_tab_by_default(job_corpus):
    from search.query import search_page
    page = search_page("officer", doc_type="job")
    assert all(r.card.get("deadline_state") != "closed" for r in page.results)


@pytest.mark.django_db
def test_closed_vacancies_are_reachable_when_asked_for(job_corpus):
    from search.filters import parse_filters
    from search.query import search_page
    fs = parse_filters(["deadline:closed"], "job")
    assert search_page("officer", doc_type="job", filters=fs).results


@pytest.mark.django_db
def test_a_job_with_no_deadline_is_treated_as_open(job_corpus):
    """Absence of a deadline is not evidence the vacancy closed."""
    from search.query import search_page
    page = search_page("undated", doc_type="job")
    assert page.results


@pytest.mark.django_db
def test_the_default_is_reported_not_silent(job_corpus):
    from search.query import search_page
    page = search_page("officer", doc_type="job")
    assert "deadline:open" in page.applied_defaults
