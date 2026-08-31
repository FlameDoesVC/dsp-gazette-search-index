import pytest
from django.core.management import call_command

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.project import candidate_keys
from search.specs.seed_data import SEED_KEYS


def test_the_seed_list_covers_the_measured_top_productinfo_keys():
    """Spec 4.4 lists these with their corpus counts. If a key here is absent,
    the most common filters in the corpus are not facetable on day one."""
    keys = {k["key"] for k in SEED_KEYS}
    assert {"item_condition", "type", "neighborhood", "brand", "room_facilities",
            "lift", "floor", "furnishing", "bedrooms", "bathrooms",
            "ideal_tenants", "square_feet", "position_type", "job_category",
            "employer", "salary_range", "apply_before"} <= keys


def test_every_seed_entry_is_well_formed():
    for k in SEED_KEYS:
        assert k["datatype"] in {"numeric", "enum", "bool"}
        assert k["widget"] in {"range", "checkbox", "toggle"}
        assert k["label_en"]
        assert isinstance(k.get("categories", []), list)


def test_type_is_category_scoped_because_it_means_four_different_things():
    t = next(k for k in SEED_KEYS if k["key"] == "type")
    assert t["categories"], "an unscoped `Type` merges Guest House with LED"


def test_brand_carries_the_apple_alias():
    b = next(k for k in SEED_KEYS if k["key"] == "brand")
    assert b["value_aliases"].get("Apple (iPhone)") == "Apple"


@pytest.mark.django_db
def test_seeding_is_idempotent():
    call_command("seed_spec_keys")
    n = SpecKey.objects.count()
    call_command("seed_spec_keys")
    assert SpecKey.objects.count() == n


@pytest.mark.django_db
def test_seeding_does_not_overwrite_a_curated_row():
    """An admin who set is_facetable or priority by hand must not lose it on
    the next deploy."""
    call_command("seed_spec_keys")
    k = SpecKey.objects.get(key="brand")
    k.priority = 1
    k.is_facetable = False
    k.save()
    call_command("seed_spec_keys")
    k.refresh_from_db()
    assert k.priority == 1 and k.is_facetable is False


@pytest.mark.django_db
def test_candidate_keys_ranks_unpromoted_keys_by_frequency():
    doc = SearchDocument.objects.create(source="other", source_key="1",
                                        doc_type="shopping", url="https://x")
    doc2 = SearchDocument.objects.create(source="other", source_key="2",
                                         doc_type="shopping", url="https://x")
    for d in (doc, doc2):
        DocumentSpec.objects.create(document_id=d.id, key_raw="warranty",
                                    value_text="1 year")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="colour",
                                value_text="black")

    got = candidate_keys()
    assert got[0]["key_raw"] == "warranty"
    assert got[0]["documents"] == 2
    assert got[0]["distinct_values"] == 1
