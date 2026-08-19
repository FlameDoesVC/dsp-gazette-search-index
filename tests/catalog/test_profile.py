import pytest

from catalog.models import Entity, EntityField, EntityLink
from catalog.profile import build_profile_input, profile_one, select_entity_ids
from search.models import Category, SearchDocument


class FakeClient:
    """Stands in for EnrichClient. The provider chain is P4's and already
    tested; what needs testing here is what we do with the answer."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def run_chain(self, messages, *, rebuild=None):
        self.calls += 1
        return self.payload, "fake-model"

    async def aclose(self):
        pass


@pytest.fixture
def entity_with_listings(db):
    Category.objects.create(key="mobile-phones", label_en="Mobile Phones",
                            tier="primary")
    entity = Entity.objects.create(kind="product", key="k1", brand="Samsung",
                                   model_name="A15 128GB")
    for i, title in enumerate([
        "Samsung Galaxy A15 128GB blue 6.5 inch 7438649",
        "SAMSUNG A15 128GB free delivery 9663178",
    ]):
        SearchDocument.objects.create(
            source="ibay", source_key=str(i), doc_type="shopping",
            url=f"https://x/{i}", title_en=title,
            attrs={"specs_raw": {"Item Condition": "New"}})
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=str(i), method="identity_match")
    return entity


@pytest.mark.django_db
def test_build_profile_input_unions_the_listings(entity_with_listings):
    inp = build_profile_input(entity_with_listings)
    assert len(inp.listings) == 2
    assert "6.5 inch" in inp.union_text
    assert "mobile-phones" in inp.categories


@pytest.mark.django_db
def test_build_profile_input_returns_none_for_an_entity_with_no_links(db):
    orphan = Entity.objects.create(kind="product", key="k2")
    assert build_profile_input(orphan) is None


@pytest.mark.django_db(transaction=True)
def test_a_grounded_spec_is_stored_grounded(entity_with_listings):
    import asyncio

    client = FakeClient({
        "title_en": "Samsung Galaxy A15 128GB",
        "summary_en": "Samsung Galaxy A15 with 128GB storage.",
        "category_key": "mobile-phones",
        "product": {"brand": "Samsung", "model_name": "Galaxy A15",
                    "specs": [{"key_raw": "storage_gb", "value_num": 128,
                               "unit": "GB", "origin": "from_listings"}]},
    })
    inp = build_profile_input(entity_with_listings)
    asyncio.run(profile_one(inp, client))

    field = EntityField.objects.get(key_raw="storage_gb")
    assert field.provenance == "grounded"
    assert field.value_num == 128


@pytest.mark.django_db(transaction=True)
def test_an_unsupported_from_listings_claim_lands_as_inferred(entity_with_listings):
    import asyncio

    client = FakeClient({
        "title_en": "Samsung Galaxy A15",
        "category_key": "mobile-phones",
        "product": {"specs": [{"key_raw": "battery_mah", "value_num": 5000,
                               "unit": "mAh", "origin": "from_listings"}]},
    })
    asyncio.run(profile_one(build_profile_input(entity_with_listings), client))
    assert EntityField.objects.get(key_raw="battery_mah").provenance == "inferred"


@pytest.mark.django_db(transaction=True)
def test_an_invented_category_key_is_ignored(entity_with_listings):
    import asyncio

    client = FakeClient({"title_en": "X", "category_key": "not-a-real-key",
                         "product": {"specs": []}})
    asyncio.run(profile_one(build_profile_input(entity_with_listings), client))
    entity_with_listings.refresh_from_db()
    assert entity_with_listings.category_id is None
    assert entity_with_listings.profile_status == "ok"


@pytest.mark.django_db(transaction=True)
def test_a_provider_failure_is_a_stored_status_not_an_exception(entity_with_listings):
    import asyncio

    from enrich.client import ProviderError

    class Failing(FakeClient):
        async def run_chain(self, messages, *, rebuild=None):
            raise ProviderError("all stages failed")

    asyncio.run(profile_one(build_profile_input(entity_with_listings),
                            Failing(None)))
    entity_with_listings.refresh_from_db()
    assert entity_with_listings.profile_status == "failed"
    assert entity_with_listings.profile_error


@pytest.mark.django_db
def test_select_skips_an_entity_already_profiled_at_this_version(entity_with_listings):
    from catalog.prompts import PROFILE_PROMPT_VERSION

    entity_with_listings.profile_status = "ok"
    entity_with_listings.profile_prompt_version = PROFILE_PROMPT_VERSION
    entity_with_listings.save()
    assert select_entity_ids() == []
    assert select_entity_ids(force=True) == [entity_with_listings.id]
