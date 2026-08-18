import pytest


@pytest.mark.django_db
def test_suggest_endpoint(api, db):
    from search.models import SuggestTerm
    SuggestTerm.objects.create(term="iphone", frequency=9, script="latin",
                               doc_type="shopping")
    body = api.get("/api/v1/suggest?q=ipho").json()
    assert body["suggestions"][0]["term"] == "iphone"


@pytest.mark.django_db
def test_suggest_with_no_query_is_empty_not_an_error(api, db):
    assert api.get("/api/v1/suggest?q=").json()["suggestions"] == []
