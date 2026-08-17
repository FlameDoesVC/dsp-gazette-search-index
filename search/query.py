"""Trilingual retrieval and blended ranking. Spec 7.

One SQL statement produces the candidate set, capped at 500 rows so ranking
cost is independent of corpus size (spec 12.3). Snippets come from the stored
summaries, never `ts_headline` -- no body text is read at query time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection

from search.lang import QueryPlan, build_query_plan


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
    matched_lang: str


def plan_for(q: str) -> QueryPlan:
    return build_query_plan(q)


def _tsquery(terms: list[str]) -> str:
    """OR the terms. Every term is already normalized, so quoting them keeps
    punctuation from being read as tsquery syntax."""
    return " | ".join(f"'{t}'" for t in terms if t)


_SQL = """
WITH q AS (
    SELECT
        CASE WHEN %(has_en)s    THEN to_tsquery('english', %(q_en)s)    END AS q_en,
        CASE WHEN %(has_dv)s    THEN to_tsquery('simple',  %(q_dv)s)    END AS q_dv,
        CASE WHEN %(has_latin)s THEN to_tsquery('simple',  %(q_latin)s) END AS q_latin
),
candidates AS (
    SELECT d.*,
           COALESCE(ts_rank_cd(d.vector_en,    q.q_en),    0) AS r_en,
           COALESCE(ts_rank_cd(d.vector_dv,    q.q_dv),    0) AS r_dv,
           COALESCE(ts_rank_cd(d.vector_latin, q.q_latin), 0) AS r_latin,
           GREATEST(
               similarity(d.title_en,    %(raw)s),
               similarity(d.title_dv,    %(raw)s),
               similarity(d.title_latin, %(raw)s)
           ) AS trg
    FROM search_searchdocument d, q
    WHERE d.is_active
      AND (
            (q.q_en    IS NOT NULL AND d.vector_en    @@ q.q_en)
         OR (q.q_dv    IS NOT NULL AND d.vector_dv    @@ q.q_dv)
         OR (q.q_latin IS NOT NULL AND d.vector_latin @@ q.q_latin)
      )
      AND (%(doc_type)s IS NULL OR d.doc_type = %(doc_type)s)
    LIMIT %(candidate_limit)s
)
SELECT id, source, source_key, doc_type, url,
       title_en, title_dv, summary_en, summary_dv, card,
       r_en, r_dv, r_latin, trg,
       (
           %(w_en)s    * r_en
         + %(w_dv)s    * r_dv
         + %(w_latin)s * r_latin
         + %(w_trigram)s * trg
         + %(w_same_lang)s * CASE
               WHEN %(response_lang)s = 'dv' AND r_dv > 0 THEN 1
               WHEN %(response_lang)s = 'en' AND r_en > 0 THEN 1
               ELSE 0 END
         + %(w_freshness)s * CASE
               WHEN published_at IS NULL THEN 0
               ELSE exp(
                   -ln(2) *
                   EXTRACT(EPOCH FROM (now() - published_at)) / 86400.0 /
                   CASE doc_type
                       WHEN 'news'     THEN %(hl_news)s
                       WHEN 'job'      THEN %(hl_job)s
                       WHEN 'property' THEN %(hl_property)s
                       ELSE %(hl_shopping)s
                   END
               ) END
         + %(w_quality)s * quality
         - CASE WHEN expires_at IS NOT NULL AND expires_at < now()
                THEN %(expired_penalty)s ELSE 0 END
       ) AS score
FROM candidates
ORDER BY score DESC, id DESC
LIMIT %(limit)s
"""


def search(
    q: str,
    *,
    doc_type: str | None = None,
    limit: int = 20,
    candidate_limit: int | None = None,
) -> list[SearchResult]:
    plan = build_query_plan(q)
    if not (plan.terms_en or plan.terms_dv or plan.terms_latin):
        return []

    r = settings.SEARCH_RANKING
    hl = r["freshness_half_life_days"]
    params = {
        "raw": plan.raw,
        "has_en": bool(plan.terms_en),
        "has_dv": bool(plan.terms_dv),
        "has_latin": bool(plan.terms_latin),
        "q_en": _tsquery(plan.terms_en) or "x",
        "q_dv": _tsquery(plan.terms_dv) or "x",
        "q_latin": _tsquery(plan.terms_latin) or "x",
        "doc_type": doc_type,
        "response_lang": plan.response_lang,
        "candidate_limit": candidate_limit or r["candidate_limit"],
        "limit": limit,
        "w_en": r["w_en"], "w_dv": r["w_dv"], "w_latin": r["w_latin"],
        "w_trigram": r["w_trigram"], "w_same_lang": r["w_same_lang"],
        "w_freshness": r["w_freshness"], "w_quality": r["w_quality"],
        "expired_penalty": r["expired_penalty"],
        "hl_news": hl["news"], "hl_job": hl["job"],
        "hl_shopping": hl["shopping"], "hl_property": hl["property"],
    }

    with connection.cursor() as cur:
        cur.execute(_SQL, params)
        rows = cur.fetchall()

    prefer_dv = plan.response_lang == "dv"
    results: list[SearchResult] = []
    for (
        doc_id, source, source_key, dtype, url,
        title_en, title_dv, summary_en, summary_dv, card,
        r_en, r_dv, r_latin, _trg, score,
    ) in rows:
        if r_dv and r_dv >= max(r_en, r_latin):
            matched = "dv"
        elif r_latin and r_latin >= r_en:
            matched = "latin"
        else:
            matched = "en"
        results.append(
            SearchResult(
                id=doc_id,
                source=source,
                source_key=source_key,
                doc_type=dtype,
                url=url,
                title=(title_dv or title_en) if prefer_dv else (title_en or title_dv),
                summary=(summary_dv or summary_en) if prefer_dv
                        else (summary_en or summary_dv),
                card=json.loads(card) if isinstance(card, str) else (card or {}),
                score=float(score),
                matched_lang=matched,
            )
        )
    return results
