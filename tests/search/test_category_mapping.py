import pytest

from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import Category, SearchDocument, SourceCategoryMap
from search.taxonomy import path_key


def draft(path, **kw):
    return DocumentDraft(
        source="other", source_key=kw.pop("source_key", "1"), doc_type="shopping",
        url="https://other-source.example/x", title_en=kw.pop("title", "A charger"),
        attrs={"category_path": path}, **kw)


@pytest.fixture
def mapped(db):
    family = Category.objects.create(key="mobile", label_en="Mobile Phones & Accessories",
                                     tier="family")
    node = Category.objects.create(key="accessories-charger", label_en="Phone Charger",
                                   parent=family, tier="accessory")
    path = ["For Sale", "Mobile Phones & Accessories", "Accessories", "Charger"]
    SourceCategoryMap.objects.create(source="other", path=path,
                                     path_key=path_key("other", path), category=node)
    return node, path


@pytest.mark.django_db
def test_a_mapped_document_gets_the_canonical_category(mapped):
    node, path = mapped
    upsert_drafts([draft(path)])
    doc = SearchDocument.objects.get(source="other", source_key="1")
    assert doc.category_id == node.id


@pytest.mark.django_db
def test_category_leaf_comes_from_the_canonical_label_not_the_source_path(mapped):
    """The source leaf is 'Charger'; the canonical label is 'Phone Charger'.
    Ranking and facet priority key on category_leaf, so it must be the
    unambiguous one."""
    node, path = mapped
    upsert_drafts([draft(path)])
    doc = SearchDocument.objects.get(source="other", source_key="1")
    assert doc.category_leaf == "Phone Charger"


@pytest.mark.django_db
def test_an_unmapped_path_keeps_the_raw_leaf_and_no_category(mapped):
    """Nothing regresses for a path nobody has reviewed yet."""
    upsert_drafts([draft(["For Sale", "Unreviewed Thing"], source_key="2")])
    doc = SearchDocument.objects.get(source="other", source_key="2")
    assert doc.category_id is None
    assert doc.category_leaf == "Unreviewed Thing"


@pytest.mark.django_db
def test_a_document_with_no_path_is_valid(mapped):
    upsert_drafts([draft([], source_key="3")])
    doc = SearchDocument.objects.get(source="other", source_key="3")
    assert doc.category_id is None
    assert doc.category_leaf == ""


@pytest.mark.django_db
def test_map_categories_updates_in_place_without_a_reindex(mapped):
    """188 documents have no path and 278 paths get reviewed over time; a
    taxonomy edit must not require re-running the paid pipeline."""
    from django.core.management import call_command

    node, path = mapped
    upsert_drafts([draft(path, source_key="4")])
    SearchDocument.objects.filter(source_key="4").update(category=None,
                                                         category_leaf="Charger")
    call_command("map_categories", "--source", "other")
    doc = SearchDocument.objects.get(source="other", source_key="4")
    assert doc.category_id == node.id
    assert doc.category_leaf == "Phone Charger"


# --------------------------------------------------------------------------
# map_path is called once per document from _row, so its cost is multiplied by
# the corpus. These pin the cache and, more importantly, its invalidation.
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_map_path_does_not_query_once_per_document(mapped):
    """Uncached, a 20,445-document reindex issues 20,445 queries to answer 306
    distinct questions, and spec 12 projects 5M rows."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from search.taxonomy import map_path

    node, path = mapped
    map_path("other", path)                      # warm
    with CaptureQueriesContext(connection) as ctx:
        for _ in range(50):
            map_path("other", path)
    assert len(ctx) == 0


@pytest.mark.django_db
def test_an_unmapped_path_is_cached_too(mapped):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from search.taxonomy import map_path

    miss = ["For Sale", "Not Reviewed"]
    assert map_path("other", miss) is None
    with CaptureQueriesContext(connection) as ctx:
        for _ in range(20):
            assert map_path("other", miss) is None
    assert len(ctx) == 0


@pytest.mark.django_db
def test_editing_a_category_invalidates_the_cache(mapped):
    """An admin click must reach the web process without a restart. Without the
    signal this returns the stale label forever."""
    from search.taxonomy import map_path

    node, path = mapped
    assert map_path("other", path).label_en == "Phone Charger"
    node.label_en = "Mobile Phone Charger"
    node.save()
    assert map_path("other", path).label_en == "Mobile Phone Charger"


@pytest.mark.django_db
def test_deactivating_a_category_invalidates_the_cache(mapped):
    from search.taxonomy import map_path

    node, path = mapped
    assert map_path("other", path) is not None
    node.is_active = False
    node.save()
    assert map_path("other", path) is None


@pytest.mark.django_db
def test_remapping_a_path_invalidates_the_cache(mapped):
    """Repointing a SourceCategoryMap row is the other half of the admin's job."""
    from search.models import Category, SourceCategoryMap
    from search.taxonomy import map_path

    node, path = mapped
    assert map_path("other", path) == node
    other = Category.objects.create(key="something-else", label_en="Something Else",
                                    tier="primary")
    row = SourceCategoryMap.objects.get(source="other", path=path)
    row.category = other
    row.save()
    assert map_path("other", path) == other


@pytest.mark.django_db(transaction=True, databases=["default", "direct"])
def test_map_categories_works_when_streaming_over_the_direct_alias(mapped):
    """The production configuration, which the other tests cannot reach.

    conftest points STREAM_DB_ALIAS at `default` so streaming works inside a
    test transaction. Production points it at `direct`, and then a Category
    resolved over `default` cannot be assigned to a document streamed over
    `direct` -- Django's allow_relation check raises ValueError. This test is
    the only place that failure is visible, and it cost a live command run to
    find. transaction=True so the committed rows are visible to the second
    connection.
    """
    from django.core.management import call_command
    from django.test import override_settings

    node, path = mapped
    upsert_drafts([draft(path, source_key="direct-1")])
    SearchDocument.objects.filter(source_key="direct-1").update(
        category=None, category_leaf="Charger")

    with override_settings(STREAM_DB_ALIAS="direct"):
        call_command("map_categories", "--source", "other")

    doc = SearchDocument.objects.get(source="other", source_key="direct-1")
    assert doc.category_id == node.id
    assert doc.category_leaf == "Phone Charger"
