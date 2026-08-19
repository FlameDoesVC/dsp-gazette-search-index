import pytest
from django.test import override_settings

from catalog.models import Entity, EntityField, FieldProposal
from catalog.proposals import apply_ready, evaluate_field, propose


@pytest.fixture
def entity(db):
    e = Entity.objects.create(kind="product", key="k", profile_status="ok")
    EntityField.objects.create(entity=e, key_raw="brand", value_text="Samsang",
                               provenance="inferred", is_winner=True)
    return e


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_one_proposal_changes_nothing(entity):
    propose(entity, "brand", value_text="Samsung", ip_hash="a")
    assert evaluate_field(entity, "brand") == "pending"
    assert not EntityField.objects.filter(entity=entity,
                                          provenance="correction").exists()


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_the_same_ip_cannot_reach_quorum_alone(entity):
    for _ in range(5):
        propose(entity, "brand", value_text="Samsung", ip_hash="a")
    assert FieldProposal.objects.count() == 1
    assert evaluate_field(entity, "brand") == "pending"


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_quorum_applies_the_correction(entity):
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
    assert evaluate_field(entity, "brand") == "applied"
    row = EntityField.objects.get(entity=entity, provenance="correction")
    assert row.value_text == "Samsung"
    assert row.support_count == 3
    assert row.is_winner is True


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_two_competing_values_conflict_and_nothing_applies(entity):
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
        propose(entity, "brand", value_text="Sony", ip_hash=ip + "2")
    assert evaluate_field(entity, "brand") == "conflicted"
    assert not EntityField.objects.filter(entity=entity,
                                          provenance="correction").exists()
    assert FieldProposal.objects.filter(status="conflicted").count() == 6


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_a_clear_lead_over_a_competitor_still_applies(entity):
    for ip in ("a", "b", "c", "d", "e"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
    propose(entity, "brand", value_text="Sony", ip_hash="z")
    assert evaluate_field(entity, "brand") == "applied"


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_an_empty_value_means_the_field_is_wrong(entity):
    """Applying it removes the winner rather than storing an empty string."""
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="", ip_hash=ip)
    assert evaluate_field(entity, "brand") == "applied"
    assert not EntityField.objects.filter(entity=entity,
                                          is_winner=True).exists()


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_a_correction_cannot_overwrite_a_scraped_value(entity):
    """The ladder still governs after a correction applies."""
    EntityField.objects.create(entity=entity, key_raw="item_condition",
                               value_text="New", provenance="scraped",
                               is_winner=True)
    for ip in ("a", "b", "c"):
        propose(entity, "item_condition", value_text="Used", ip_hash=ip)
    evaluate_field(entity, "item_condition")
    winner = EntityField.objects.get(entity=entity, key_raw="item_condition",
                                     is_winner=True)
    assert winner.provenance == "scraped"


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_apply_ready_sweeps_every_pending_field(entity):
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
    FieldProposal.objects.update(status="pending")
    assert apply_ready()["applied"] == 1
