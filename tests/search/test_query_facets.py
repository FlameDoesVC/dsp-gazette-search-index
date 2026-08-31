import pytest

from search.filters import parse_filters
from search.models import SearchDocument
from search.query import search_page


@pytest.fixture
def corpus(db):
    def mk(**kw):
        base = dict(source="other", doc_type="shopping", url="https://x",
                    is_active=True, attrs={}, card={}, thumbnails=[])
        base.update(kw)
        return SearchDocument.objects.create(**base)

    mk(source_key="1", title_en="iPhone 13 phone", price=9500,
       attrs={"brand": "Apple", "condition": "Used"}, thumbnails=["a.jpg"])
    mk(source_key="2", title_en="iPhone 12 phone", price=7500,
       attrs={"brand": "Apple", "condition": "New"})
    mk(source_key="3", title_en="Samsung phone", price=5500,
       attrs={"brand": "Samsung", "condition": "New"}, thumbnails=["b.jpg"])
    mk(source_key="4", title_en="Nokia phone", price=1500,
       attrs={"brand": "Nokia", "condition": "Used"})
    mk(source="gazette", source_key="IUL-1", doc_type="job",
       title_en="Accountant phone allowance",
       attrs={"job_category": "Accounting", "employer": "Ministry",
              "estimated_net_min": 14397.5,
              "compensation": {"salary_state": "listed"}})
    from django.core.management import call_command
    call_command("reindex_vectors")     # P1 helper; rebuilds vectors in place
    return None


@pytest.mark.django_db
def test_results_are_paginated_and_total_is_the_candidate_count(corpus):
    page = search_page("phone", per_page=2, page=1)
    assert len(page.results) == 2
    assert page.total == 5
    assert search_page("phone", per_page=2, page=3).results


@pytest.mark.django_db
def test_a_page_past_the_end_is_empty_not_an_error(corpus):
    assert search_page("phone", per_page=20, page=99).results == []


@pytest.mark.django_db
def test_doc_type_narrows_both_results_and_facets(corpus):
    page = search_page("phone", doc_type="shopping")
    assert {r.doc_type for r in page.results} == {"shopping"}
    assert {f["key"] for f in page.facets} >= {"price", "brand", "condition"}


@pytest.mark.django_db
def test_an_enum_filter_narrows_the_result_set(corpus):
    fs = parse_filters(["brand:Apple"], "shopping")
    page = search_page("phone", doc_type="shopping", filters=fs)
    assert page.total == 2


@pytest.mark.django_db
def test_a_multi_select_filter_ors_within_the_key(corpus):
    fs = parse_filters(["brand:Apple", "brand:Nokia"], "shopping")
    assert search_page("phone", doc_type="shopping", filters=fs).total == 3


@pytest.mark.django_db
def test_two_different_filters_and_together(corpus):
    fs = parse_filters(["brand:Apple", "condition:New"], "shopping")
    assert search_page("phone", doc_type="shopping", filters=fs).total == 1


@pytest.mark.django_db
def test_a_range_filter_on_a_column(corpus):
    fs = parse_filters(["price:5000..8000"], "shopping")
    assert search_page("phone", doc_type="shopping", filters=fs).total == 2


@pytest.mark.django_db
def test_a_range_filter_on_a_jsonb_number(corpus):
    fs = parse_filters(["net_estimate:10000..20000"], "job")
    assert search_page("phone", doc_type="job", filters=fs).total == 1


@pytest.mark.django_db
def test_a_toggle_filter(corpus):
    fs = parse_filters(["has_images:true"], "shopping")
    assert search_page("phone", doc_type="shopping", filters=fs).total == 2


@pytest.mark.django_db
def test_facet_counts_match_the_filtered_result_set(corpus):
    """Spec 7: facets aggregate over the same candidate CTE, so counts always
    match. A count computed over the unfiltered corpus is a bug users notice
    immediately."""
    fs = parse_filters(["condition:New"], "shopping")
    page = search_page("phone", doc_type="shopping", filters=fs)
    brand = next(f for f in page.facets if f["key"] == "brand")
    counts = {v["value"]: v["count"] for v in brand["values"]}
    assert counts == {"Apple": 1, "Samsung": 1}
    assert sum(counts.values()) == page.total


@pytest.mark.django_db
def test_a_range_facet_reports_min_max_and_a_histogram(corpus):
    page = search_page("phone", doc_type="shopping")
    price = next(f for f in page.facets if f["key"] == "price")
    assert price["widget"] == "range"
    assert price["min"] == 1500 and price["max"] == 9500
    assert len(price["histogram"]) == 10
    assert sum(b["count"] for b in price["histogram"]) == 4


@pytest.mark.django_db
def test_a_toggle_facet_reports_its_true_count(corpus):
    page = search_page("phone", doc_type="shopping")
    has_images = next(f for f in page.facets if f["key"] == "has_images")
    assert has_images["count_true"] == 2


@pytest.mark.django_db
def test_enum_facets_are_capped_and_sorted_by_count(corpus):
    page = search_page("phone", doc_type="shopping")
    brand = next(f for f in page.facets if f["key"] == "brand")
    counts = [v["count"] for v in brand["values"]]
    assert counts == sorted(counts, reverse=True)
    assert len(brand["values"]) <= 12


@pytest.mark.django_db
def test_a_facet_with_no_values_in_the_candidate_set_is_omitted(corpus):
    """An empty checkbox list is dead UI."""
    page = search_page("phone", doc_type="shopping")
    assert all(f["values"] or f["widget"] != "checkbox" for f in page.facets)


@pytest.mark.django_db
@pytest.mark.parametrize("sort", ["relevance", "newest", "price_asc", "price_desc"])
def test_every_declared_sort_runs(corpus, sort):
    assert search_page("phone", doc_type="shopping", sort=sort).results


@pytest.mark.django_db
def test_price_asc_orders_by_price(corpus):
    page = search_page("phone", doc_type="shopping", sort="price_asc")
    prices = [r.card.get("price") or 0 for r in page.results]
    ids = [r.source_key for r in page.results]
    assert ids[0] == "4"


@pytest.mark.django_db
def test_an_empty_query_returns_nothing_rather_than_the_whole_corpus(corpus):
    assert search_page("").results == []
