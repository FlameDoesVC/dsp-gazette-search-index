import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from search.models import DocumentSpec, SearchDocument, SpecKey


@pytest.fixture
def staff(db):
    user = User.objects.create_superuser("admin", "a@example.com", "pw")
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_the_candidate_queue_ranks_by_document_count(staff):
    for i in range(3):
        doc = SearchDocument.objects.create(source="ibay", source_key=str(i),
                                            doc_type="shopping", url="https://x")
        DocumentSpec.objects.create(document_id=doc.id, key_raw="warranty",
                                    value_text="1 year")
    doc = SearchDocument.objects.create(source="ibay", source_key="x",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="colour",
                                value_text="black")

    r = staff.get(reverse("admin:search_speckey_candidates"))
    assert r.status_code == 200
    body = r.content.decode()
    assert body.index("warranty") < body.index("colour")


@pytest.mark.django_db
def test_promoting_a_key_creates_it_and_links_existing_rows(staff):
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="warranty",
                                value_text="1 year")

    r = staff.post(reverse("admin:search_speckey_candidates"),
                   {"promote": "warranty"}, follow=True)
    assert r.status_code == 200

    key = SpecKey.objects.get(key="warranty")
    assert key.is_facetable is True
    assert DocumentSpec.objects.get(key_raw="warranty").key_id == key.id


@pytest.mark.django_db
def test_promotion_infers_the_datatype_from_the_stored_values(staff):
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="weight",
                                value_num=1.5, unit="kg")
    staff.post(reverse("admin:search_speckey_candidates"), {"promote": "weight"})
    key = SpecKey.objects.get(key="weight")
    assert key.datatype == "numeric" and key.widget == "range"
    assert key.unit == "kg"


@pytest.mark.django_db
def test_demoting_a_key_removes_it_from_facets_without_deleting_data(staff):
    key = SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                                 widget="checkbox", is_facetable=True)
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key=key, key_raw="brand",
                                value_text="Apple")
    staff.post(reverse("admin:search_speckey_changelist"),
               {"action": "demote", "_selected_action": [key.id]})
    key.refresh_from_db()
    assert key.is_facetable is False
    assert DocumentSpec.objects.count() == 1
