import time

import pytest
from django.core.management import call_command

from search.models import SearchDocument


@pytest.mark.django_db
@pytest.mark.slow
def test_faceted_search_stays_under_budget_at_100k():
    """The candidate set is capped at 500 rows (spec 12.3), so latency must be
    flat in corpus size. If this regresses, the facet aggregation is the
    suspect -- it is N statements over the same CTE."""
    SearchDocument.objects.bulk_create([
        SearchDocument(source="ibay", source_key=str(i), doc_type="shopping",
                       url=f"https://x/{i}", title_en=f"iPhone case model {i}",
                       price=100 + (i % 900),
                       attrs={"brand": ["Apple", "Samsung", "Nokia"][i % 3],
                              "condition": ["New", "Used"][i % 2]},
                       thumbnails=["a.jpg"] if i % 2 else [])
        for i in range(100_000)
    ], batch_size=2000)
    call_command("reindex_vectors")

    from search.query import search_page
    timings = []
    for _ in range(20):
        t = time.perf_counter()
        page = search_page("iphone", doc_type="shopping", per_page=20)
        timings.append((time.perf_counter() - t) * 1000)
        assert page.results

    timings.sort()
    p50, p95 = timings[len(timings) // 2], timings[int(len(timings) * 0.95)]
    print(f"\nfaceted search p50={p50:.0f}ms p95={p95:.0f}ms")
    assert p95 < 400, f"p95 {p95:.0f}ms exceeds the 400ms budget"
