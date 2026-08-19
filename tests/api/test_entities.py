import pytest
from django.test import override_settings

from catalog.models import Entity, EntityField, FieldProposal

# The `api` fixture is tests/api/conftest.py's Client, and the mount prefix is
# /api/v1/ (beynunehcheh/urls.py). Both match the existing report tests.


@pytest.fixture
def entity(db):
    e = Entity.objects.create(kind="product", key="k", title_en="Galaxy A15",
                              profile_status="ok", listing_count=3)
    EntityField.objects.create(entity=e, key_raw="storage_gb", value_num=128,
                               unit="GB", provenance="inferred", is_winner=True)
    return e


@pytest.mark.django_db
def test_get_entity_returns_the_profile_with_provenance(api, entity):
    r = api.get(f"/api/v1/entities/{entity.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title_en"] == "Galaxy A15"
    assert body["listing_count"] == 3
    assert body["fields"][0]["provenance"] == "inferred"


@pytest.mark.django_db
def test_get_entity_returns_the_listings_behind_the_profile(api, entity):
    """A profile with no traceable evidence is not inspectable, and an inferred
    field is exactly what a reader needs to check against the source ads."""
    from catalog.models import EntityLink
    from search.models import SearchDocument

    SearchDocument.objects.create(
        source="ibay", source_key="9001", doc_type="shopping",
        url="https://ibay.com.mv/thing-o9001.html", title_en="A thing")
    EntityLink.objects.create(entity=entity, source="ibay", source_key="9001",
                              method="identity_match")

    body = api.get(f"/api/v1/entities/{entity.id}").json()
    assert body["sources"][0]["url"] == "https://ibay.com.mv/thing-o9001.html"
    assert body["sources"][0]["source_key"] == "9001"


@pytest.mark.django_db
def test_get_a_missing_entity_is_404(api, db):
    assert api.get("/api/v1/entities/999999").status_code == 404


@pytest.mark.django_db
def test_propose_is_always_202(api, entity):
    r = api.post(f"/api/v1/entities/{entity.id}/propose",
                 {"key_raw": "storage_gb", "value_num": 256},
                 content_type="application/json")
    assert r.status_code == 202
    assert FieldProposal.objects.count() == 1


@pytest.mark.django_db
def test_proposing_on_a_missing_entity_is_also_202(api, db):
    """The endpoint must not confirm what exists. Same rule as /report."""
    r = api.post("/api/v1/entities/999999/propose",
                 {"key_raw": "brand", "value_text": "Sony"},
                 content_type="application/json")
    assert r.status_code == 202
    assert FieldProposal.objects.count() == 0


@pytest.mark.django_db
def test_a_duplicate_from_one_caller_is_202_and_counted_once(api, entity):
    """session_hash is derived from IP plus user agent, so every request from
    the test client shares one hash -- which is exactly the real duplicate
    case."""
    for _ in range(3):
        api.post(f"/api/v1/entities/{entity.id}/propose",
                 {"key_raw": "brand", "value_text": "Sony"},
                 content_type="application/json")
    assert FieldProposal.objects.count() == 1


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_RATE_LIMIT=2)
def test_over_the_rate_limit_is_still_202_and_stores_nothing(api, entity):
    for i in range(5):
        r = api.post(f"/api/v1/entities/{entity.id}/propose",
                     {"key_raw": f"key_{i}", "value_text": "x"},
                     content_type="application/json")
        assert r.status_code == 202
    assert FieldProposal.objects.count() == 2


@pytest.mark.django_db
def test_the_api_and_the_card_agree_on_the_trust_label(api, entity):
    """They briefly did not: the rule changed from weakest-tier to dominant-tier
    and only catalog/overlay.py was updated, so one entity read 'grounded' on its
    card and 'inferred' on its detail page. Both now call dominant_tier()."""
    from catalog.models import EntityField
    from catalog.overlay import apply_entity
    from search.adapters.base import DocumentDraft
    from catalog.models import EntityLink
    from search.models import SearchDocument

    for i in range(3):
        EntityField.objects.create(entity=entity, key_raw=f"g{i}", value_text="x",
                                   provenance="grounded", is_winner=True)
    EntityLink.objects.create(entity=entity, source="ibay", source_key="7777",
                              method="identity_match")
    SearchDocument.objects.create(source="ibay", source_key="7777",
                                 doc_type="shopping", url="https://x/7777",
                                 title_en="t")

    body = api.get(f"/api/v1/entities/{entity.id}").json()
    draft = apply_entity(DocumentDraft(
        source="ibay", source_key="7777", doc_type="shopping",
        url="https://x/7777", title_en="t", card={"title": "t"}))

    assert body["profile_tier"] == draft.card["profile_tier"] == "grounded"
    assert body["inferred_count"] == draft.card["inferred_count"] == 1
