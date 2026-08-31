import pytest
from django.core.management import call_command

from search.models import DocumentSpec, SearchDocument, SpecKey


@pytest.mark.django_db
def test_sync_specs_streams_a_whole_source(capsys):
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True)
    for i in range(5):
        SearchDocument.objects.create(source="other", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      attrs={"specs_raw": {"Brand": "Nokia"}})
    call_command("sync_specs", "--source", "other")
    assert DocumentSpec.objects.count() == 5


@pytest.mark.django_db
def test_sync_specs_prunes_rows_for_deleted_documents():
    DocumentSpec.objects.create(document_id=999999, key_raw="brand",
                                value_text="Ghost")
    call_command("sync_specs", "--prune")
    assert DocumentSpec.objects.count() == 0


@pytest.mark.django_db
def test_limit_is_respected(capsys):
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum")
    for i in range(10):
        SearchDocument.objects.create(source="other", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      attrs={"specs_raw": {"Brand": "Nokia"}})
    call_command("sync_specs", "--source", "other", "--limit", "3")
    assert DocumentSpec.objects.values("document_id").distinct().count() == 3
