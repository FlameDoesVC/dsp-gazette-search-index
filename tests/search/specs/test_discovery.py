import pytest

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.discovery import (
    MAX_FACETS, dominant_category, normalized_entropy, score,
)
from search.query import search_page


# --- the pure scoring parts ---------------------------------------------

def test_entropy_is_zero_for_a_constant_distribution():
    assert normalized_entropy([100]) == 0.0
    assert normalized_entropy([50, 0, 0]) == 0.0


def test_entropy_is_one_for_a_uniform_distribution():
    assert normalized_entropy([10, 10, 10, 10]) == pytest.approx(1.0)


def test_entropy_is_between_for_a_skewed_distribution():
    e = normalized_entropy([90, 5, 5])
    assert 0.0 < e < 1.0


def test_score_rewards_both_coverage_and_distinctiveness():
    assert score(1.0, 1.0) > score(1.0, 0.2)
    assert score(1.0, 0.5) > score(0.2, 0.5)


def test_dominant_category_needs_a_supermajority():
    rows = [{"category": "Mobile Phones"}] * 8 + [{"category": "Computers"}] * 2
    assert dominant_category(rows) == "Mobile Phones"
    rows = [{"category": "Mobile Phones"}] * 6 + [{"category": "Computers"}] * 4
    assert dominant_category(rows) is None


# --- the integrated pass -------------------------------------------------

@pytest.fixture
def power_supplies(db):
    """A candidate set that should surface voltage, amperage and wattage."""
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V", unit_aliases=["v"], widget="range",
                           is_facetable=True, priority=40)
    SpecKey.objects.create(key="current", label_en="Current", datatype="numeric",
                           unit="A", unit_aliases=["a"], widget="range",
                           is_facetable=True, priority=41)
    SpecKey.objects.create(key="power", label_en="Power", datatype="numeric",
                           unit="W", unit_aliases=["w"], widget="range",
                           is_facetable=True, priority=42)
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True, priority=20)
    SpecKey.objects.create(key="warranty", label_en="Warranty", datatype="enum",
                           widget="checkbox", is_facetable=False)

    keys = {k.key: k for k in SpecKey.objects.all()}
    for i in range(30):
        doc = SearchDocument.objects.create(
            source="ibay", source_key=f"ps{i}", doc_type="shopping",
            url="https://x", title_en=f"power supply unit {i}",
            price=100 + (i % 900),
            attrs={"category_path": ["Electronics"]},
        )
        DocumentSpec.objects.create(document_id=doc.id, key=keys["voltage"],
                                    key_raw="voltage", value_num=12 + (i % 4) * 6,
                                    unit="V")
        DocumentSpec.objects.create(document_id=doc.id, key=keys["current"],
                                    key_raw="current", value_num=1 + (i % 5),
                                    unit="A")
        DocumentSpec.objects.create(document_id=doc.id, key=keys["power"],
                                    key_raw="power", value_num=60 + (i % 3) * 60,
                                    unit="W")
        # Constant across the whole set: must be discarded (spec 8.3 step 3).
        DocumentSpec.objects.create(document_id=doc.id, key=keys["brand"],
                                    key_raw="brand", value_text="KICO")
        # Not facetable: must never appear.
        DocumentSpec.objects.create(document_id=doc.id, key=keys["warranty"],
                                    key_raw="warranty", value_text="1 year")
        if i < 3:
            # Sparse: under the 8-document floor.
            DocumentSpec.objects.create(document_id=doc.id, key_raw="colour",
                                        value_text="black")
    from django.core.management import call_command
    call_command("reindex_vectors")


@pytest.mark.django_db
def test_power_supply_surfaces_its_unit_ranges(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    keys = [f["key"] for f in page.facets]
    assert {"voltage", "current", "power"} <= set(keys)


@pytest.mark.django_db
def test_a_constant_valued_key_is_discarded(power_supplies):
    """Every result is brand KICO. A filter that cannot partition the results
    is dead UI, and this is the check most implementations skip."""
    page = search_page("power supply", doc_type="shopping")
    assert "brand" not in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_a_sparse_key_is_discarded(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    assert "colour" not in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_a_non_facetable_key_never_appears(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    assert "warranty" not in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_universal_facets_survive_the_thresholds(power_supplies):
    """Price, condition, location and source are always available and are not
    subject to discovery. Spec 8.3."""
    page = search_page("power supply", doc_type="shopping")
    assert "price" in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_at_most_eight_dynamic_facets(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    dynamic = [f for f in page.facets if f.get("dynamic")]
    assert len(dynamic) <= MAX_FACETS


@pytest.mark.django_db
def test_a_numeric_facet_carries_a_ten_bucket_histogram(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    v = next(f for f in page.facets if f["key"] == "voltage")
    assert v["widget"] == "range"
    assert v["unit"] == "V"
    assert len(v["histogram"]) == 10


@pytest.mark.django_db
def test_an_enum_facet_is_capped_at_twelve_values(db):
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True)
    key = SpecKey.objects.get(key="brand")
    for i in range(40):
        doc = SearchDocument.objects.create(source="ibay", source_key=f"p{i}",
                                            doc_type="shopping", url="https://x",
                                            title_en=f"phone {i}")
        DocumentSpec.objects.create(document_id=doc.id, key=key, key_raw="brand",
                                    value_text=f"Brand{i % 20}")
    from django.core.management import call_command
    call_command("reindex_vectors")
    page = search_page("phone", doc_type="shopping")
    brand = next(f for f in page.facets if f["key"] == "brand")
    assert len(brand["values"]) == 12


@pytest.mark.django_db
def test_a_category_supermajority_overrides_the_scoring_order(db):
    """Spec 8.3 step 5: this is what makes a phone search reliably lead with
    brand and storage instead of whatever happened to be dense that day."""
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True, priority=1,
                           categories=["Mobile Phones"])
    SpecKey.objects.create(key="weight", label_en="Weight", datatype="numeric",
                           unit="kg", widget="range", is_facetable=True,
                           priority=90, categories=["Mobile Phones"])
    keys = {k.key: k for k in SpecKey.objects.all()}
    for i in range(20):
        doc = SearchDocument.objects.create(
            source="ibay", source_key=f"m{i}", doc_type="shopping",
            url="https://x", title_en=f"iphone {i}",
            attrs={"category_path": ["Electronics", "Mobile Phones"]})
        DocumentSpec.objects.create(document_id=doc.id, key=keys["brand"],
                                    key_raw="brand",
                                    value_text=["Apple", "Samsung"][i % 2])
        DocumentSpec.objects.create(document_id=doc.id, key=keys["weight"],
                                    key_raw="weight", value_num=0.1 + i * 0.01,
                                    unit="kg")
    from django.core.management import call_command
    call_command("reindex_vectors")

    page = search_page("iphone", doc_type="shopping")
    dynamic = [f["key"] for f in page.facets if f.get("dynamic")]
    # weight has higher entropy (20 distinct values vs 2) and would win on raw
    # score; the curated priority for the dominant category must beat it.
    assert dynamic.index("brand") < dynamic.index("weight")


@pytest.mark.django_db
def test_discovery_runs_only_for_shopping(power_supplies):
    page = search_page("power supply", doc_type="job")
    assert not [f for f in page.facets if f.get("dynamic")]


@pytest.mark.django_db
def test_an_empty_result_set_produces_no_dynamic_facets(power_supplies):
    page = search_page("zzzznothing", doc_type="shopping")
    assert page.facets == [] or not [f for f in page.facets if f.get("dynamic")]
