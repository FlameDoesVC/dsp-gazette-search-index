import pytest

from catalog.models import Entity, EntityField, EntityLink
from catalog.overlay import apply_entity
from search.adapters.base import DocumentDraft
from search.models import Category, DocumentSpec, SearchDocument, SpecKey
from search.specs.project import sync_document_specs


@pytest.fixture
def linked(db):
    node = Category.objects.create(key="mobile-phones", label_en="Mobile Phones",
                                   tier="primary")
    entity = Entity.objects.create(
        kind="product", key="k", brand="Samsung", model_name="Galaxy A15",
        title_en="Samsung Galaxy A15 128GB", summary_en="A phone.",
        category=node, profile_status="ok", identity_confidence=0.9)
    EntityLink.objects.create(entity=entity, source="ibay", source_key="1",
                              method="identity_match")
    SpecKey.objects.create(key="storage_gb", label_en="Storage",
                           datatype="numeric", unit="GB", is_facetable=True)
    return entity


def draft():
    return DocumentDraft(source="ibay", source_key="1", doc_type="shopping",
                         url="https://x/1", title_en="SAMSUNG A15 128GB 7438649",
                         card={"title": "SAMSUNG A15 128GB 7438649"})


@pytest.mark.django_db
def test_the_entity_title_replaces_the_seller_title_for_display(linked):
    out = apply_entity(draft())
    assert out.card["title"] == "Samsung Galaxy A15 128GB"
    assert out.attrs["entity_id"] == linked.id


@pytest.mark.django_db
def test_an_unlinked_document_passes_through_untouched(linked):
    d = DocumentDraft(source="ibay", source_key="999", doc_type="shopping",
                      url="https://x/999", title_en="Untouched")
    assert apply_entity(d).title_en == "Untouched"


@pytest.mark.django_db
def test_winning_entity_specs_reach_documentspec_with_provenance(linked):
    # The title must NOT contain 128GB. The unit extractor is input 1 of
    # specs_for_document and the entity is input 4, and push() dedupes on
    # (key_raw, value_num, value_text) with first-write-wins -- so a value the
    # title already states is claimed by the extractor, which carries no
    # provenance because it is grounded by construction. Asserting 'inferred'
    # on such a value tests the dedupe, not the projection.
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=128, unit="GB", provenance="inferred",
                               is_winner=True)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        title_en="SAMSUNG A15 smartphone", attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    row = DocumentSpec.objects.get(document_id=doc.id, key_raw="storage_gb")
    assert row.value_num == 128
    assert row.provenance == "inferred"


@pytest.mark.django_db
def test_a_non_winning_field_is_not_projected(linked):
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=64, unit="GB", provenance="inferred",
                               is_winner=False)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    assert not DocumentSpec.objects.filter(document_id=doc.id,
                                           key_raw="storage_gb").exists()


@pytest.mark.django_db
def test_low_identity_confidence_keeps_inferred_specs_out_of_the_substrate(linked):
    """Filterable, but only above the confidence floor (spec section 18)."""
    linked.identity_confidence = 0.4
    linked.save()
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=128, unit="GB", provenance="inferred",
                               is_winner=True)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    assert not DocumentSpec.objects.filter(document_id=doc.id,
                                           key_raw="storage_gb").exists()


@pytest.mark.django_db
def test_a_grounded_spec_ignores_the_confidence_floor(linked):
    linked.identity_confidence = 0.4
    linked.save()
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=128, unit="GB", provenance="grounded",
                               is_winner=True)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    assert DocumentSpec.objects.filter(document_id=doc.id,
                                       key_raw="storage_gb").exists()


@pytest.mark.django_db
def test_a_service_entity_gets_the_service_card(db):
    entity = Entity.objects.create(kind="service", key="s1",
                                   provider_key="7438649",
                                   service_type="electrical-wiring",
                                   title_en="Electrical wiring and repair",
                                   profile_status="ok")
    EntityLink.objects.create(entity=entity, source="ibay", source_key="2",
                              method="seller_service")
    EntityField.objects.create(entity=entity, key_raw="coverage",
                               value_text="Male'", provenance="grounded",
                               is_winner=True)
    d = DocumentDraft(source="ibay", source_key="2", doc_type="shopping",
                      url="https://x/2", title_en="wiring 7438649",
                      card={"title": "wiring 7438649", "phone": "7438649"})
    card = apply_entity(d).card
    assert card["kind"] == "service"
    assert card["coverage"] == ["Male'"]
    assert card["phone"] == "7438649"


@pytest.mark.django_db
def test_profile_tier_is_the_dominant_tier_not_the_weakest(linked):
    """Measured: taking the lowest tier labelled 7,641 of 7,794 entity-backed
    cards 'inferred' because one inferred boolean outvoted everything else. A
    caveat that fires on 98% of cards is noise."""
    for i in range(4):
        EntityField.objects.create(entity=linked, key_raw=f"k{i}",
                                   value_text=f"v{i}", provenance="grounded",
                                   is_winner=True)
    EntityField.objects.create(entity=linked, key_raw="call_out",
                               value_text="yes", provenance="inferred",
                               is_winner=True)
    card = apply_entity(draft()).card
    assert card["profile_tier"] == "grounded"
    assert card["inferred_count"] == 1
    assert card["field_count"] == 5


@pytest.mark.django_db
def test_an_evenly_split_profile_reads_as_the_weaker_tier(linked):
    """A tie must not flatter the profile."""
    EntityField.objects.create(entity=linked, key_raw="a", value_text="x",
                               provenance="grounded", is_winner=True)
    EntityField.objects.create(entity=linked, key_raw="b", value_text="y",
                               provenance="inferred", is_winner=True)
    assert apply_entity(draft()).card["profile_tier"] == "inferred"


@pytest.mark.django_db
def test_a_wholly_inferred_profile_still_says_inferred(linked):
    EntityField.objects.create(entity=linked, key_raw="a", value_text="x",
                               provenance="inferred", is_winner=True)
    card = apply_entity(draft()).card
    assert card["profile_tier"] == "inferred"
    assert card["inferred_count"] == card["field_count"] == 1


@pytest.mark.django_db
def test_a_link_publishes_without_a_profile(linked):
    """The link and the profile are separate facts. The LINK knows this listing
    is one of N for the same product and costs no model call; the PROFILE knows
    what the product is and does. Gating both on the profile meant 18,424
    resolved links published nothing at all, so the frontend had no grouping to
    show and no way to earn one."""
    linked.profile_status = "pending"
    linked.listing_count = 34
    linked.save(update_fields=["profile_status", "listing_count"])

    out = apply_entity(draft())

    assert out.attrs["entity_id"] == linked.id
    assert out.attrs["entity_kind"] == "product"
    assert out.card["entity_id"] == linked.id
    assert out.card["listing_count"] == 34
    assert out.card["category_leaf"] == "Mobile Phones"


@pytest.mark.django_db
def test_an_unprofiled_entity_claims_no_trust_label(linked):
    """A tier computed from no fields is absent, not 'grounded'. Writing an
    empty profile_tier onto every linked document would have the frontend
    render a caveat slot for a profile that does not exist."""
    linked.profile_status = "pending"
    linked.save(update_fields=["profile_status"])

    out = apply_entity(draft())

    for key in ("profile_tier", "inferred_count", "field_count"):
        assert key not in out.attrs, key
        assert key not in out.card, key


@pytest.mark.django_db
def test_an_unprofiled_entity_does_not_blank_earned_spec_chips(linked):
    """The enrichment overlay runs first and its chips came from this listing's
    own text. An entity with nothing to say must not overwrite them."""
    linked.profile_status = "pending"
    linked.save(update_fields=["profile_status"])
    d = draft()
    d.card["spec_chips"] = ["128GB"]

    out = apply_entity(d)

    assert out.card["spec_chips"] == ["128GB"]


@pytest.mark.django_db
def test_an_unprofiled_service_still_renders_as_a_service(linked):
    """Its doc_type is 'shopping' by design, so without the entity's kind the
    frontend routes it to ShoppingCard and shows condition, price and brand --
    every one a field a service does not have."""
    linked.kind = "service"
    linked.profile_status = "pending"
    linked.save(update_fields=["kind", "profile_status"])

    out = apply_entity(draft())

    assert out.card["kind"] == "service"
    assert out.card["services_offered"] == []
