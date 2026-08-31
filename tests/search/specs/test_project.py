import pytest

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.project import specs_for_document, sync_document_specs


@pytest.fixture
def registry(db):
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V", unit_aliases=["v"], widget="range",
                           is_facetable=True)
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           value_aliases={"Apple (iPhone)": "Apple"},
                           widget="checkbox", is_facetable=True)
    SpecKey.objects.create(key="room_facilities", label_en="Facilities",
                           datatype="enum", widget="checkbox")


def _doc(**kw):
    base = dict(source="other", source_key="1", doc_type="shopping",
                url="https://x", attrs={}, card={})
    base.update(kw)
    return SearchDocument.objects.create(**base)


@pytest.mark.django_db
def test_units_are_extracted_from_the_title(registry):
    doc = _doc(title_en="KICO METAL POWER SUPPLY 24V-5A-120W / 7884445")
    rows = specs_for_document(doc)
    assert any(r["key_raw"] == "voltage" and r["value_num"] == 24 for r in rows)


@pytest.mark.django_db
def test_enrichment_specs_are_projected(registry):
    doc = _doc(attrs={"specs": [{"key_raw": "brand", "value_text": "Apple (iPhone)"}]})
    rows = specs_for_document(doc)
    # The alias collapses at projection time, so the facet has one value.
    assert any(r["value_text"] == "Apple" for r in rows)


@pytest.mark.django_db
def test_scraped_productinfo_is_projected(registry):
    """The largest and cheapest source: ProductInfo already supplies
    near-schema data for thousands of listings (spec 4.4)."""
    doc = _doc(attrs={"specs_raw": {"Brand": "Apple (iPhone)",
                                    "Item Condition": "Used"}})
    rows = specs_for_document(doc)
    values = {(r["key_raw"], r["value_text"]) for r in rows}
    assert ("brand", "Apple") in values
    assert ("item_condition", "Used") in values


@pytest.mark.django_db
def test_a_multi_value_productinfo_field_becomes_several_rows(registry):
    doc = _doc(attrs={"specs_raw": {"Room Facilities": "Air Conditioning, Fans, Towels"}})
    rows = [r for r in specs_for_document(doc) if r["key_raw"] == "room_facilities"]
    assert len(rows) == 3


@pytest.mark.django_db
def test_a_registered_key_is_linked_and_an_unregistered_one_is_not(registry):
    doc = _doc(attrs={"specs_raw": {"Brand": "Nokia", "Warranty": "1 year"}})
    rows = {r["key_raw"]: r for r in specs_for_document(doc)}
    assert rows["brand"]["key_id"] is not None
    assert rows["warranty"]["key_id"] is None


@pytest.mark.django_db
def test_sync_is_idempotent(registry):
    doc = _doc(title_en="24V power supply",
               attrs={"specs_raw": {"Brand": "Apple (iPhone)"}})
    sync_document_specs(doc)
    first = DocumentSpec.objects.filter(document_id=doc.id).count()
    sync_document_specs(doc)
    assert DocumentSpec.objects.filter(document_id=doc.id).count() == first


@pytest.mark.django_db
def test_sync_removes_specs_that_no_longer_apply(registry):
    doc = _doc(attrs={"specs_raw": {"Brand": "Nokia"}})
    sync_document_specs(doc)
    doc.attrs = {"specs_raw": {"Brand": "Samsung"}}
    doc.save()
    sync_document_specs(doc)
    values = list(DocumentSpec.objects.filter(document_id=doc.id)
                  .values_list("value_text", flat=True))
    assert values == ["Samsung"]


@pytest.mark.django_db
def test_a_document_with_no_specs_produces_no_rows(registry):
    assert specs_for_document(_doc(title_en="A thing")) == []
