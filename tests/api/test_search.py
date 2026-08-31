import datetime as dt

import pytest
from django.utils import timezone

from search.models import SearchDocument


@pytest.fixture
def docs(db, sources):
    SearchDocument.objects.create(
        source="other", source_key="1", doc_type="shopping", url="https://x/1",
        title_en="iPhone 13", summary_en="A used iPhone.", price=9500,
        attrs={"brand": "Apple"}, card={"source": "other", "title": "iPhone 13"},
        thumbnails=["https://x/1.jpg"],
    )
    SearchDocument.objects.create(
        source="gazette", source_key="IUL-1", doc_type="job",
        url="https://gazette.gov.mv/iulaan/1",
        title_dv="އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ", summary_dv="ވަޒީފާގެ ފުރުޞަތު",
        title_en="", summary_en="",
        # The latin spelling is what makes an English query reach a Dhivehi
        # document -- in production enrichment keywords fill this role.
        title_latin="administrative officer",
        attrs={"job_category": "Admin"},
        card={"source": "gazette", "role": "Administrative Officer",
              "deadline": (timezone.now() + dt.timedelta(days=2)).date().isoformat()},
    )
    from django.core.management import call_command
    call_command("reindex_vectors")


@pytest.mark.django_db
def test_search_returns_the_documented_envelope(api, docs):
    body = api.get("/api/v1/search?q=iphone").json()
    assert set(body) >= {"query", "total", "page", "per_page", "results",
                         "facets", "suggestions", "query_id"}
    assert body["query"]["raw"] == "iphone"
    assert body["query"]["detected_lang"]
    assert body["query"]["response_lang"]


@pytest.mark.django_db
def test_a_result_carries_source_key_not_an_icon_path(api, docs):
    r = api.get("/api/v1/search?q=iphone").json()["results"][0]
    assert r["source"] == "other"
    assert r["card"]["source"] == "other"
    assert "icon" not in r["card"]


@pytest.mark.django_db
def test_title_falls_back_across_languages_and_says_so(api, docs):
    """Spec 9: title and summary resolve server-side to the response language,
    falling back with a `translated: true` flag. The frontend never chooses."""
    body = api.get("/api/v1/search?q=officer&lang=en").json()
    r = body["results"][0]
    assert r["title"]                    # not empty despite title_en being blank
    assert r["translated"] is True


@pytest.mark.django_db
def test_a_document_with_a_native_title_is_not_flagged_translated(api, docs):
    r = api.get("/api/v1/search?q=iphone&lang=en").json()["results"][0]
    assert r["translated"] is False


@pytest.mark.django_db
def test_deadline_state_is_computed_per_request_and_not_stored(api, docs):
    """Spec 8: a gazette card is written once. `deadline_state` must be derived
    from the raw date at response time or a closed vacancy advertises itself as
    open forever."""
    r = next(x for x in api.get("/api/v1/search?q=officer").json()["results"]
             if x["doc_type"] == "job")
    assert r["card"]["deadline_state"] == "closing_soon"
    stored = SearchDocument.objects.get(source_key="IUL-1")
    assert "deadline_state" not in stored.card


@pytest.mark.django_db
def test_a_past_deadline_renders_closed(api, docs):
    d = SearchDocument.objects.get(source_key="IUL-1")
    d.card["deadline"] = "2020-01-01"
    d.save()
    r = next(x for x in api.get("/api/v1/search?q=officer").json()["results"]
             if x["doc_type"] == "job")
    assert r["card"]["deadline_state"] == "closed"


@pytest.mark.django_db
def test_filters_are_accepted_as_repeated_query_params(api, docs):
    body = api.get("/api/v1/search?q=iphone&type=shopping&f=brand:Apple").json()
    assert body["total"] == 1


@pytest.mark.django_db
def test_an_unknown_filter_key_is_a_400_not_a_500(api, docs):
    r = api.get("/api/v1/search?q=iphone&type=shopping&f=nonsense:1")
    assert r.status_code == 400
    assert "unknown filter" in r.json()["detail"]


@pytest.mark.django_db
def test_per_page_is_clamped(api, docs):
    body = api.get("/api/v1/search?q=iphone&per_page=5000").json()
    assert body["per_page"] <= 100


@pytest.mark.django_db
def test_an_empty_query_returns_an_empty_envelope_not_a_400(api, docs):
    body = api.get("/api/v1/search?q=").json()
    assert body["total"] == 0 and body["results"] == []


@pytest.mark.django_db
def test_images_tab_flattens_thumbnails(api, docs):
    body = api.get("/api/v1/search?q=iphone&type=images").json()
    assert body["results"][0]["card"]["images"] == ["https://x/1.jpg"]


@pytest.mark.django_db
def test_the_response_carries_a_query_id_for_click_logging(api, docs):
    body = api.get("/api/v1/search?q=iphone").json()
    assert isinstance(body["query_id"], int)


@pytest.mark.django_db
def test_a_dhivehi_query_gets_a_dhivehi_response_language(api, docs):
    body = api.get("/api/v1/search?q=ވަޒީފާ").json()
    assert body["query"]["response_lang"] == "dv"
