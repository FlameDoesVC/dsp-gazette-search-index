import pytest

from search.models import SearchDocument


@pytest.fixture
def docs(db, sources):
    SearchDocument.objects.create(
        source="other", source_key="1", doc_type="shopping", url="https://x/1",
        title_en="iPhone 13", summary_en="A used iPhone.",
        attrs={"brand": "Apple", "specs": [{"key_raw": "storage",
                                            "value_num": 128, "unit": "GB"}]},
        card={"source": "other", "title": "iPhone 13"},
        thumbnails=["https://x/1.jpg", "https://x/2.jpg"],
    )
    SearchDocument.objects.create(
        source="gazette", source_key="IUL-1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1", title_en="Tender",
        card={"source": "gazette"},
    )


@pytest.mark.django_db
def test_detail_returns_attrs_thumbnails_and_source(api, docs):
    d = SearchDocument.objects.get(source_key="1")
    body = api.get(f"/api/v1/documents/{d.id}").json()
    assert body["source"] == "other"
    assert body["attrs"]["brand"] == "Apple"
    assert body["thumbnails"] == ["https://x/1.jpg", "https://x/2.jpg"]
    assert body["url"] == "https://x/1"


@pytest.mark.django_db
def test_detail_404s_for_news(api, docs):
    """Spec 8.5: /documents/{id} serves shopping, jobs and property only.
    News links out; building an internal reader for content we do not own is
    work that helps nobody."""
    d = SearchDocument.objects.get(source_key="IUL-1")
    assert api.get(f"/api/v1/documents/{d.id}").status_code == 404


@pytest.mark.django_db
def test_detail_404s_for_a_missing_id(api, docs):
    assert api.get("/api/v1/documents/999999").status_code == 404


@pytest.mark.django_db
def test_detail_carries_the_full_spec_table_including_non_facetable_keys(api, docs):
    d = SearchDocument.objects.get(source_key="1")
    body = api.get(f"/api/v1/documents/{d.id}").json()
    # `provenance` is part of the spec item as of the catalog entity layer: a
    # filterable value that came from model knowledge has to say so. Empty here
    # because this fixture's row predates any entity.
    assert body["specs"] == [{"key_raw": "storage", "value_num": 128,
                              "value_text": "", "unit": "GB",
                              "provenance": ""}]


@pytest.mark.django_db
def test_detail_computes_deadline_state_rather_than_reading_it(api, docs):
    d = SearchDocument.objects.get(source_key="1")
    d.doc_type = "job"
    d.card = {"source": "other", "deadline": "2020-01-01"}
    d.save()
    body = api.get(f"/api/v1/documents/{d.id}").json()
    assert body["card"]["deadline_state"] == "closed"
