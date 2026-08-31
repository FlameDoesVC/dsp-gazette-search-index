"""Test support for the search suite.

`transaction=True` tests (the enrichment end-to-end test) truncate every
table, including the migration-seeded `Source` rows, and --reuse-db means the
seed is not re-applied next session. Re-ensure the two registry rows so the
P1 source tests stay green regardless of run order.
"""

import pytest


@pytest.fixture(autouse=True)
def _ensure_source_seed(db):
    from search.models import Source

    seed = [
        ("other", "Other", "އެހެން", "https://other-source.example",
         "/static/sources/other.svg", "އެ", "#1f6feb"),
        ("gazette", "Gazette", "ގެޒެޓް", "https://gazette.gov.mv",
         "/sources/gazette.png", "ގ", "#0f766e"),
    ]
    for (key, label_en, label_dv, site_url, icon, fb, accent) in seed:
        Source.objects.update_or_create(
            key=key,
            defaults=dict(
                label_en=label_en, label_dv=label_dv, site_url=site_url,
                icon=icon, icon_fallback_text=fb, accent=accent,
                is_active=True,
            ),
        )
