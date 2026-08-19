import pytest

from catalog.merge import promote_consensus, recompute_winners, winning_fields
from catalog.models import Entity, EntityField, EntityLink
from search.models import SearchDocument


@pytest.fixture
def entity(db):
    return Entity.objects.create(kind="product", key="k", brand="Samsung")


def add(entity, provenance, value_text="x", key_raw="colour", **kw):
    return EntityField.objects.create(entity=entity, key_raw=key_raw,
                                      value_text=value_text,
                                      provenance=provenance, **kw)


@pytest.mark.django_db
def test_scraped_beats_correction(entity):
    """A source's own structured field is never overwritten (spec 5.2)."""
    add(entity, "scraped", "New", key_raw="item_condition")
    add(entity, "correction", "Used", key_raw="item_condition")
    recompute_winners(entity)
    winners = {f.key_raw: f for f in winning_fields(entity)}
    assert winners["item_condition"].value_text == "New"


@pytest.mark.django_db
def test_correction_beats_consensus_grounded_and_inferred(entity):
    add(entity, "inferred", "black")
    add(entity, "grounded", "blue")
    add(entity, "consensus", "green")
    add(entity, "correction", "red")
    recompute_winners(entity)
    assert winning_fields(entity)[0].value_text == "red"


@pytest.mark.django_db
def test_a_same_tier_tie_produces_no_winner_and_flags_the_entity(entity):
    """Never pick a side by row order. Spec section 9.

    `brand` because the conflict rule applies only to keys that hold one value.
    Two values on a list key like service_offered is a list, not a tie -- see
    the two tests below, which cost 467 fields to learn.
    """
    add(entity, "inferred", "Sony", key_raw="brand", support_count=1)
    add(entity, "inferred", "Sigma", key_raw="brand", support_count=1)
    result = recompute_winners(entity)
    entity.refresh_from_db()
    assert result["unresolved"] == 1
    assert winning_fields(entity) == []
    assert entity.profile_status == "needs_review"


@pytest.mark.django_db
def test_support_count_breaks_a_same_tier_tie(entity):
    add(entity, "inferred", "black", key_raw="brand", support_count=3)
    add(entity, "inferred", "white", key_raw="brand", support_count=1)
    recompute_winners(entity)
    assert winning_fields(entity)[0].value_text == "black"


@pytest.mark.django_db
def test_every_value_of_a_list_key_wins(entity):
    """Measured: treating every key as single-valued left service_offered with
    no winner on 342 of 1,747 profiled service entities, so nothing reached the
    facet substrate and each was flagged for review for having a list."""
    for value in ("Electrical wiring", "Panel board installation",
                  "Ceiling fan repair"):
        add(entity, "grounded", value, key_raw="service_offered")
    result = recompute_winners(entity)
    assert result["unresolved"] == 0
    assert {f.value_text for f in winning_fields(entity)} == {
        "Electrical wiring", "Panel board installation", "Ceiling fan repair"}


@pytest.mark.django_db
def test_a_list_key_still_respects_the_ladder(entity):
    """Winning tier wins entire, but a lower tier does not sneak in with it."""
    add(entity, "grounded", "Electrical wiring", key_raw="service_offered")
    add(entity, "inferred", "Invented service", key_raw="service_offered")
    recompute_winners(entity)
    assert {f.value_text for f in winning_fields(entity)} == {"Electrical wiring"}


@pytest.mark.django_db
def test_a_list_key_does_not_flag_the_entity_for_review(entity):
    for value in ("Male'", "Hulhumale'", "Villingili"):
        add(entity, "grounded", value, key_raw="coverage")
    recompute_winners(entity)
    entity.refresh_from_db()
    assert entity.profile_status != "needs_review"


@pytest.mark.django_db
def test_consensus_needs_two_different_sellers(entity):
    """One seller repeating themselves is not agreement."""
    for i, seller in enumerate(["Miabulbul", "Miabulbul"]):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url=f"https://x/{i}",
                                      card={"seller_name": seller})
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=str(i), method="identity_match")
    add(entity, "grounded", "blue")
    assert promote_consensus(entity) == 0
    assert EntityField.objects.filter(entity=entity,
                                      provenance="consensus").count() == 0


@pytest.mark.django_db
def test_two_different_sellers_promote_to_consensus(entity):
    for i, seller in enumerate(["Miabulbul", "ExpartTechnician"]):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url=f"https://x/{i}",
                                      card={"seller_name": seller})
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=str(i), method="identity_match")
    add(entity, "grounded", "blue")
    assert promote_consensus(entity) == 1
    row = EntityField.objects.get(entity=entity, provenance="consensus")
    assert row.value_text == "blue"
    assert row.support_count == 2


@pytest.mark.django_db
def test_needs_review_is_cleared_once_the_conflict_is_gone(entity):
    """Recompute, never accumulate -- dedupe_listings states the same rule for
    its duplicate flag. Sticky status left 332 entities accused after the
    conflict that flagged them had been fixed."""
    a = add(entity, "inferred", "Sony", key_raw="brand", support_count=1)
    add(entity, "inferred", "Sigma", key_raw="brand", support_count=1)
    recompute_winners(entity)
    entity.refresh_from_db()
    assert entity.profile_status == "needs_review"

    a.delete()                          # the conflict is resolved
    recompute_winners(entity)
    entity.refresh_from_db()
    assert entity.profile_status == "ok"
