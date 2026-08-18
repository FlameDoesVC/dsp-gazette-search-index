import pytest
from django.db import IntegrityError

from search.models import DocumentSpec, SearchDocument, SpecKey


@pytest.mark.django_db
def test_speckey_is_unique_by_key():
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V")
    with pytest.raises(IntegrityError):
        SpecKey.objects.create(key="voltage", label_en="Volts", datatype="numeric")


@pytest.mark.django_db
def test_a_new_key_is_not_facetable_until_promoted():
    """Spec 4.4: extraction is open, faceting is curated. A key arrives
    invisible and a human makes it a filter."""
    k = SpecKey.objects.create(key="colour", label_en="Colour", datatype="enum")
    assert k.is_facetable is False


@pytest.mark.django_db
def test_value_aliases_collapse_variants():
    """'Apple (iPhone)' appears 999 times and 'Apple' 111. They are the same
    brand and must be one checkbox, or the most common filter in the corpus is
    wrong."""
    k = SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                               value_aliases={"Apple (iPhone)": "Apple",
                                              "APPLE": "Apple"})
    assert k.resolve_value("Apple (iPhone)") == "Apple"
    assert k.resolve_value("APPLE") == "Apple"
    assert k.resolve_value("Nokia") == "Nokia"
    assert k.resolve_value("  Apple (iPhone)  ") == "Apple"


@pytest.mark.django_db
def test_unit_aliases_match_case_insensitively():
    k = SpecKey.objects.create(key="voltage", label_en="Voltage",
                               datatype="numeric", unit="V",
                               unit_aliases=["volt", "volts", "v"])
    assert k.matches_unit("V") and k.matches_unit("volts") and k.matches_unit("VOLT")
    assert not k.matches_unit("A")


@pytest.mark.django_db
def test_categories_scope_a_key_to_where_it_means_something():
    """'Type' means Guest House for property, LED for televisions and
    Laptop/Notebook for computers. One key, four vocabularies -- so the
    registry is category-scoped, not global."""
    k = SpecKey.objects.create(key="type", label_en="Type", datatype="enum",
                               categories=["Televisions", "Computers"])
    assert "Televisions" in k.categories
    assert "Housing & Real Estate" not in k.categories


@pytest.mark.django_db
def test_documentspec_stores_numeric_and_text_values_separately():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="voltage",
                                value_num=24, unit="V")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="brand",
                                value_text="Apple")
    assert DocumentSpec.objects.filter(document_id=doc.id).count() == 2


@pytest.mark.django_db
def test_documentspec_survives_a_document_that_no_longer_exists():
    """SearchDocument is partitioned, so the FK carries db_constraint=False.
    A dangling spec row must be inert, not a 500."""
    DocumentSpec.objects.create(document_id=999999, key_raw="voltage",
                                value_num=24, unit="V")
    assert DocumentSpec.objects.count() == 1


@pytest.mark.django_db
def test_a_spec_row_is_unique_per_document_key_and_value():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="voltage",
                                value_num=24, unit="V")
    with pytest.raises(IntegrityError):
        DocumentSpec.objects.create(document_id=doc.id, key_raw="voltage",
                                    value_num=24, unit="V")
