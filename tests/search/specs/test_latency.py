import time

import pytest
from django.core.management import call_command

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.query import search_page


@pytest.mark.django_db
@pytest.mark.slow
def test_discovery_stays_inside_the_facet_budget():
    """Discovery is N statements over a 500-row CTE. If this regresses it is
    the per-key statements, not the CTE -- and spec 16.4's Meilisearch
    re-entry condition names 'dynamic facet discovery proving too slow' as one
    of its two triggers."""
    for i in range(20):
        SpecKey.objects.create(key=f"spec{i}", label_en=f"Spec {i}",
                               datatype="numeric" if i % 2 else "enum",
                               unit="V" if i % 2 else "",
                               widget="range" if i % 2 else "checkbox",
                               is_facetable=True, priority=i)
    keys = list(SpecKey.objects.all())

    docs = SearchDocument.objects.bulk_create([
        SearchDocument(source="ibay", source_key=str(i), doc_type="shopping",
                       url=f"https://x/{i}", title_en=f"power supply {i}",
                       price=100 + i % 900, attrs={"category_path": ["Electronics"]})
        for i in range(20_000)
    ], batch_size=2000)
    DocumentSpec.objects.bulk_create([
        DocumentSpec(document_id=d.id, key=k, key_raw=k.key,
                     value_num=(i % 50) if k.datatype == "numeric" else None,
                     value_text="" if k.datatype == "numeric" else f"V{i % 7}")
        for i, d in enumerate(docs) for k in keys[:4]
    ], batch_size=5000)
    call_command("reindex_vectors")

    # The shared test DB rolls each test back, so the planner's stats still
    # think the spec table is empty while it holds 80k rows -- without this it
    # chooses a sequential-scan plan and the measurement is a lie.
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("ANALYZE search_searchdocument")
        cur.execute("ANALYZE search_documentspec")

    timings = []
    for _ in range(10):
        t = time.perf_counter()
        page = search_page("power supply", doc_type="shopping")
        timings.append((time.perf_counter() - t) * 1000)
        assert page.facets

    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]
    print(f"\nshopping search with discovery p95={p95:.0f}ms")
    assert p95 < 600, f"p95 {p95:.0f}ms exceeds the 600ms budget"
