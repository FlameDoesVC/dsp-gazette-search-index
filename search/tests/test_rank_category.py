import pytest

from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.query import search_page


@pytest.fixture
def ibay_corpus(db):
    """Phones, chargers, cases and screen protectors -- the distribution that
    put zero phones in the top 12 for 'iphone'."""
    drafts = []
    for i in range(20):
        drafts.append(DocumentDraft(
            source="ibay", source_key=f"phone{i}", doc_type="shopping",
            url=f"https://x/{i}", title_en=f"iPhone 15 Pro phone {i}",
            price=20000 + i,
            attrs={"category_path": ["Electronics", "Mobile Phones"],
                   "brand": "Apple"},
        ))
    for i in range(20):
        drafts.append(DocumentDraft(
            source="ibay", source_key=f"charger{i}", doc_type="shopping",
            url=f"https://x/c{i}", title_en=f"iPhone charger fast {i}",
            price=300 + i,
            attrs={"category_path": ["Electronics", "Charger"]},
        ))
    for i in range(20):
        drafts.append(DocumentDraft(
            source="ibay", source_key=f"case{i}", doc_type="shopping",
            url=f"https://x/k{i}", title_en=f"iPhone 15 case cover {i}",
            price=100 + i,
            attrs={"category_path": ["Electronics", "Cases, Protection & Skins"],
                   "condition": "New"},
        ))
    upsert_drafts(drafts)
    return None


@pytest.mark.django_db
def test_iphone_returns_an_actual_phone_on_page_one(ibay_corpus):
    """The reported defect. Measured before this task: zero phones in the top
    12, real phones at ranks 13, 30 and 38."""
    page = search_page("iphone", doc_type="shopping", per_page=10)
    leaves = [(r.card.get("category_leaf") or "") for r in page.results]
    assert "Mobile Phones" in leaves


@pytest.mark.django_db
def test_no_more_than_three_consecutive_results_share_a_leaf_category(ibay_corpus):
    page = search_page("iphone", doc_type="shopping", per_page=20)
    run, prev = 0, None
    for r in page.results:
        leaf = r.card.get("category_leaf")
        run = run + 1 if leaf == prev else 1
        prev = leaf
        assert run <= 3


@pytest.mark.django_db
def test_a_query_that_names_an_accessory_still_returns_accessories(ibay_corpus):
    """'iphone case' must not demote cases. The demotion is conditional on the
    query, or the fix breaks every accessory search."""
    page = search_page("iphone case", doc_type="shopping", per_page=10)
    leaves = [r.card.get("category_leaf") for r in page.results]
    assert any("Case" in (l or "") for l in leaves)


@pytest.mark.django_db
def test_category_is_available_as_a_facet(ibay_corpus):
    """The immediate escape hatch, and what a commerce site is expected to
    offer: 'Cases 310 / Screen Protection 226 / Mobile Phones 38'."""
    page = search_page("iphone", doc_type="shopping")
    assert any(f["key"] == "category_leaf" for f in page.facets)
