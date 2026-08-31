import datetime as dt

import pytest
from django.test import override_settings

from search.models import ClickLog, QueryLog, SearchDocument


@pytest.fixture
def doc(db):
    return SearchDocument.objects.create(source="other", source_key="1",
                                         doc_type="shopping", url="https://x",
                                         title_en="iPhone 13")


@pytest.mark.django_db
def test_a_search_writes_a_query_log(api, doc):
    from django.core.management import call_command
    call_command("reindex_vectors")
    api.get("/api/v1/search?q=iphone&type=shopping")
    log = QueryLog.objects.get()
    assert log.q_raw == "iphone"
    assert log.doc_type == "shopping"
    assert log.result_count >= 0
    assert log.latency_ms >= 0
    assert log.session_hash


@pytest.mark.django_db
def test_the_log_records_the_filters_that_were_applied(api, doc):
    from django.core.management import call_command
    call_command("reindex_vectors")
    api.get("/api/v1/search?q=iphone&type=shopping&f=condition:New")
    assert QueryLog.objects.get().filters == ["condition:New"]


@pytest.mark.django_db
def test_a_zero_result_query_is_logged(api, doc):
    """The immediate payoff, before any ranking work: zero-result queries
    become a measurable list. Spec 16.3."""
    api.get("/api/v1/search?q=zzzznothingmatchesthis")
    log = QueryLog.objects.get()
    assert log.result_count == 0


@pytest.mark.django_db
def test_no_raw_ip_or_user_agent_is_stored(api, doc):
    """There are no accounts and there must be no durable per-person search
    history. Spec 16.3."""
    api.get("/api/v1/search?q=iphone", HTTP_USER_AGENT="Mozilla/5.0 Secret",
            REMOTE_ADDR="203.0.113.9")
    log = QueryLog.objects.get()
    values = " ".join(str(v) for v in log.__dict__.values())
    assert "203.0.113.9" not in values
    assert "Mozilla" not in values


@pytest.mark.django_db
def test_the_session_salt_rotates_daily(api, doc, monkeypatch):
    from api import logging as apilog

    class _Req:
        META = {"REMOTE_ADDR": "203.0.113.9", "HTTP_USER_AGENT": "UA"}

    monkeypatch.setattr(apilog, "_today", lambda: dt.date(2026, 8, 18))
    a = apilog.session_hash(_Req())
    monkeypatch.setattr(apilog, "_today", lambda: dt.date(2026, 8, 19))
    b = apilog.session_hash(_Req())
    assert a != b


@pytest.mark.django_db
def test_a_logging_failure_never_fails_the_search(api, doc, monkeypatch):
    """Spec 16.3: logging must not add latency to or fail a search response."""
    from api import logging as apilog
    monkeypatch.setattr(apilog, "_write_query_log",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))
    r = api.get("/api/v1/search?q=iphone")
    assert r.status_code == 200


@pytest.mark.django_db
def test_click_endpoint_records_the_position(api, doc):
    from django.core.management import call_command
    call_command("reindex_vectors")
    qid = api.get("/api/v1/search?q=iphone").json()["query_id"]
    r = api.post("/api/v1/events/click",
                 data={"query_id": qid, "document_id": doc.id, "position": 3},
                 content_type="application/json")
    assert r.status_code == 202
    click = ClickLog.objects.get()
    assert click.position == 3
    assert click.document_id == doc.id


@pytest.mark.django_db
def test_a_click_on_an_unknown_query_is_accepted_and_dropped(api, doc):
    """A stale tab posting a click from yesterday's query must not 500."""
    r = api.post("/api/v1/events/click",
                 data={"query_id": 999999, "document_id": doc.id, "position": 1},
                 content_type="application/json")
    assert r.status_code == 202
    assert ClickLog.objects.count() == 0


@pytest.mark.django_db
def test_a_click_with_a_negative_position_is_rejected(api, doc):
    r = api.post("/api/v1/events/click",
                 data={"query_id": 1, "document_id": doc.id, "position": -1},
                 content_type="application/json")
    assert r.status_code in (400, 422)
