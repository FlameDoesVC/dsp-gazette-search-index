"""Lexical retrieval.

P1 is English-only: one tsquery against `vector_en`. The trilingual expansion,
trigram fallback and blended scoring described in spec 7 arrive in P2, which is
why `candidate_limit` already exists here -- the 500-row cap is what keeps
ranking and faceting independent of corpus size (spec 12.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import ExpressionWrapper, F, FloatField, Value

from search.models import SearchDocument

CANDIDATE_LIMIT = 500


@dataclass(slots=True)
class SearchResult:
    id: int
    source: str
    source_key: str
    doc_type: str
    url: str
    title: str
    summary: str
    card: dict[str, Any]
    score: float


def search(
    q: str,
    *,
    doc_type: str | None = None,
    limit: int = 20,
    candidate_limit: int = CANDIDATE_LIMIT,
) -> list[SearchResult]:
    q = (q or "").strip()
    if not q:
        return []

    tsquery = SearchQuery(q, config="english", search_type="websearch")

    qs = SearchDocument.objects.filter(is_active=True, vector_en=tsquery)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)

    # A small quality nudge on top of lexical rank. The blended multi-signal
    # score in spec 7 replaces this in P2; keeping it explicit here means the
    # weights are visible rather than buried once more signals arrive.
    qs = (
        qs.annotate(
            score=ExpressionWrapper(
                SearchRank(F("vector_en"), tsquery) + F("quality") * Value(0.1),
                output_field=FloatField(),
            )
        )
        .order_by("-score", "-id")[:candidate_limit]
    )

    return [
        SearchResult(
            id=row.id,
            source=row.source,
            source_key=row.source_key,
            doc_type=row.doc_type,
            url=row.url,
            title=row.title_en or row.title_dv,
            summary=row.summary_en or row.summary_dv,
            card=row.card,
            score=float(row.score),
        )
        for row in qs[:limit]
    ]
