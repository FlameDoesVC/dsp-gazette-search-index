"""Trilingual retrieval and blended ranking. Spec 7.

One SQL statement produces the candidate set, capped at 500 rows so ranking
cost is independent of corpus size (spec 12.3). Snippets come from the stored
summaries, never `ts_headline` -- no body text is read at query time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import connection

from search.facets import FACETS, FacetDef, facet_def
from search.filters import Filter, _array_expr, _expr, filter_sql
from search.interleave import interleave, interleave_by
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
    thumbnails: list = field(default_factory=list)
    category_leaf: str = ""


@dataclass(slots=True)
class SearchPage:
    results: list[SearchResult]
    total: int
    facets: list[dict]
    plan: QueryPlan
    applied_defaults: list[str] = field(default_factory=list)


def plan_for(q: str) -> QueryPlan:
    return build_query_plan(q)


def _tsquery(terms: list[str]) -> str:
    """OR the terms. Every term is already normalized, so quoting them keeps
    punctuation from being read as tsquery syntax."""
    return " | ".join(f"'{t}'" for t in terms if t)


_SORTS = {
    "relevance": "score DESC, id DESC",
    "newest": "published_at DESC NULLS LAST, id DESC",
    "price_asc": "price ASC NULLS LAST, score DESC",
    "price_desc": "price DESC NULLS LAST, score DESC",
    "salary_desc": "(attrs ->> 'estimated_net_min')::numeric DESC NULLS LAST, score DESC",
}

# The candidate CTE, materialized once. Both the page query and every facet
# aggregation read this same set, which is what makes counts match results
# (spec 7). MATERIALIZED is explicit: without it Postgres may inline the CTE
# into each aggregation and re-run the ranking N times.
_PAGE_SQL = """
WITH q AS (
    SELECT
        CASE WHEN %(has_en)s    THEN to_tsquery('english', %(q_en)s)    END AS q_en,
        CASE WHEN %(has_dv)s    THEN to_tsquery('simple',  %(q_dv)s)    END AS q_dv,
        CASE WHEN %(has_latin)s THEN to_tsquery('simple',  %(q_latin)s) END AS q_latin
),
candidates AS MATERIALIZED (
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
      AND NOT d.is_duplicate
      AND (
            (q.q_en    IS NOT NULL AND d.vector_en    @@ q.q_en)
         OR (q.q_dv    IS NOT NULL AND d.vector_dv    @@ q.q_dv)
         OR (q.q_latin IS NOT NULL AND d.vector_latin @@ q.q_latin)
      )
      AND (%(doc_type)s IS NULL OR d.doc_type = %(doc_type)s)
      {filters}
    LIMIT %(candidate_limit)s
),
scored AS (
    SELECT *, ({score_expr}) AS score FROM candidates
)
SELECT id, source, source_key, doc_type, url,
       title_en, title_dv, summary_en, summary_dv, card, price, thumbnails,
       category_leaf,
       r_en, r_dv, r_latin, score,
       count(*) OVER () AS total
FROM scored
ORDER BY {order_by}
LIMIT %(candidate_limit)s
"""

_SCORE_EXPR = """
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
"""


def _base_params(plan: QueryPlan, doc_type, candidate_limit) -> dict:
    r = settings.SEARCH_RANKING
    hl = r["freshness_half_life_days"]
    return {
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
        "w_en": r["w_en"], "w_dv": r["w_dv"], "w_latin": r["w_latin"],
        "w_trigram": r["w_trigram"], "w_same_lang": r["w_same_lang"],
        "w_freshness": r["w_freshness"], "w_quality": r["w_quality"],
        "expired_penalty": r["expired_penalty"],
        "hl_news": hl["news"], "hl_job": hl["job"],
        "hl_shopping": hl["shopping"], "hl_property": hl["property"],
    }


def _to_result(row, plan: QueryPlan) -> SearchResult:
    (doc_id, source, source_key, dtype, url,
     title_en, title_dv, summary_en, summary_dv, card,
     _price, thumbnails, category_leaf,
     r_en, r_dv, r_latin, score, _total) = row

    if r_dv and r_dv >= max(r_en, r_latin):
        matched = "dv"
    elif r_latin and r_latin >= r_en:
        matched = "latin"
    else:
        matched = "en"

    prefer_dv = plan.response_lang == "dv"
    return SearchResult(
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
        thumbnails=json.loads(thumbnails) if isinstance(thumbnails, str)
                   else (thumbnails or []),
        category_leaf=category_leaf or "",
    )


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

    params = _base_params(plan, doc_type, candidate_limit)

    sql = _PAGE_SQL.format(
        filters="",
        score_expr=_SCORE_EXPR,
        order_by=_SORTS["relevance"],
    )

    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [_to_result(row, plan) for row in rows[:limit]]


def search_page(
    q: str,
    *,
    doc_type: str | None = None,
    filters: list[Filter] | tuple = (),
    sort: str = "relevance",
    page: int = 1,
    per_page: int = 20,
    candidate_limit: int | None = None,
) -> SearchPage:
    plan = build_query_plan(q)
    if not (plan.terms_en or plan.terms_dv or plan.terms_latin):
        return SearchPage(results=[], total=0, facets=[], plan=plan)

    filters = list(filters)
    applied_defaults: list[str] = []
    # A job whose deadline passed is not a job, but a closed posting is still
    # useful for research -- so it is hidden by default, never deleted. The
    # default is reported, not silent, so the UI can offer to include them.
    if doc_type == "job" and not any(f.key == "deadline" for f in filters):
        d = facet_def("job", "deadline")
        filters.append(Filter(key="deadline", op="eq", values=["open"],
                              definition=d))
        applied_defaults.append("deadline:open")
    fsql, fparams = filter_sql(filters)

    params = _base_params(plan, doc_type, candidate_limit)
    params.update(fparams)

    sql = _PAGE_SQL.format(
        filters=fsql,
        score_expr=_SCORE_EXPR,
        order_by=_SORTS.get(sort, _SORTS["relevance"]),
    )

    # The candidate set is fetched in full (capped at 500) and paginated in
    # Python so interleaving can run over it BEFORE the page is cut. Six
    # chargers in the top 6 by score cannot be fixed by reordering the 20-row
    # page -- the phone ranked 13th has to be inside the set being interleaved
    # (P9 task 4).
    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    total = rows[0][-1] if rows else 0
    results = [_to_result(row, plan) for row in rows]
    if doc_type is None:
        results = interleave(results)
    elif doc_type == "shopping":
        # 4b (curated demotion) is superseded by P10 task 1.
        results = interleave_by(results, key="category_leaf", cap=3)
    offset = max(0, (page - 1) * per_page)
    results = results[offset:offset + per_page]

    facets = compute_facets(doc_type, filters, params, fsql)
    return SearchPage(results=results, total=int(total), facets=facets,
                      plan=plan, applied_defaults=applied_defaults)


# --- facet aggregation -------------------------------------------------------

_FACET_CTE = """
WITH q AS (
    SELECT
        CASE WHEN %(has_en)s    THEN to_tsquery('english', %(q_en)s)    END AS q_en,
        CASE WHEN %(has_dv)s    THEN to_tsquery('simple',  %(q_dv)s)    END AS q_dv,
        CASE WHEN %(has_latin)s THEN to_tsquery('simple',  %(q_latin)s) END AS q_latin
),
candidates AS MATERIALIZED (
    SELECT d.* FROM search_searchdocument d, q
    WHERE d.is_active
      AND NOT d.is_duplicate
      AND (
            (q.q_en    IS NOT NULL AND d.vector_en    @@ q.q_en)
         OR (q.q_dv    IS NOT NULL AND d.vector_dv    @@ q.q_dv)
         OR (q.q_latin IS NOT NULL AND d.vector_latin @@ q.q_latin)
      )
      AND (%(doc_type)s IS NULL OR d.doc_type = %(doc_type)s)
      {filters}
    LIMIT %(candidate_limit)s
)
"""


def compute_facets(doc_type, filters, params, fsql) -> list[dict]:
    """One statement per facet over the shared candidate CTE.

    Deliberately N small statements rather than one wide lateral join: the
    candidate set is capped at 500 rows so each aggregation is trivial, and a
    single statement returning heterogeneous shapes (enum counts, numeric
    bounds, boolean totals) would need either N UNION branches or client-side
    demultiplexing. This stays readable and P7 appends to it without surgery.
    """
    defs = FACETS.get(doc_type or "all", FACETS["all"])
    cte = _FACET_CTE.format(filters=fsql)
    out: list[dict] = []

    with connection.cursor() as cur:
        for d in defs:
            if d.key == "deadline":
                entry = _deadline_facet(cur, cte, params, d)
            elif d.widget == "checkbox":
                entry = _enum_facet(cur, cte, params, d)
            elif d.widget == "range":
                entry = _range_facet(cur, cte, params, d)
            else:
                entry = _toggle_facet(cur, cte, params, d)
            if entry is not None:
                out.append(entry)

    # Dynamic shopping facets append to the same ordered list, which is why
    # the API returns a list and not a map (spec 9). Universal facets are
    # computed above and are not subject to the discovery thresholds.
    if doc_type == "shopping":
        from search.specs.discovery import discover_facets
        with connection.cursor() as cur:
            out.extend(discover_facets(cte, params, cur))
    return out


def _deadline_facet(cur, cte, params, d: FacetDef) -> dict | None:
    """Derived buckets from now(), never aggregated raw: a gazette row is
    written once, so stored expiry timestamps would otherwise be garbage
    checkbox values (spec 8)."""
    sql = cte + """
        SELECT CASE
                 WHEN expires_at IS NULL
                      OR expires_at >= now() + interval '7 days' THEN 'open'
                 WHEN expires_at >= now() THEN 'closing_soon'
                 ELSE 'closed'
               END AS value, count(*)
        FROM candidates d
        GROUP BY 1 ORDER BY 2 DESC, 1
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        return None
    entry = _shell(d)
    entry["values"] = [{"value": str(v), "label": str(v), "count": int(c)}
                       for v, c in rows]
    return entry


def _shell(d: FacetDef) -> dict:
    return {"key": d.key, "label": d.label_en, "label_dv": d.label_dv,
            "widget": d.widget, "unit": d.unit, "values": [],
            "min": None, "max": None, "histogram": [], "count_true": None}


def _enum_facet(cur, cte, params, d: FacetDef) -> dict | None:
    if d.storage == "attrs_array":
        expr = f"jsonb_array_elements_text(COALESCE({_array_expr(d)}, '[]'::jsonb))"
        sql = (cte + f"SELECT v AS value, count(*) FROM candidates d, "
                     f"LATERAL {expr} v WHERE v <> '' "
                     f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %(facet_top_n)s")
    else:
        sql = (cte + f"SELECT {_expr(d)} AS value, count(*) FROM candidates d "
                     f"WHERE {_expr(d)} IS NOT NULL AND {_expr(d)} <> '' "
                     f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %(facet_top_n)s")
    p = {**params, "facet_top_n": d.top_n}
    cur.execute(sql, p)
    rows = cur.fetchall()
    if not rows:
        return None
    entry = _shell(d)
    entry["values"] = [{"value": str(v), "label": str(v), "count": int(c)}
                       for v, c in rows]
    return entry


def _range_facet(cur, cte, params, d: FacetDef) -> dict | None:
    expr = _expr(d) if d.storage == "column" else f"({_expr(d)})::numeric"
    sql = cte + (
        f"SELECT min({expr}), max({expr}), count(*) "
        f"FROM candidates d WHERE {expr} IS NOT NULL"
    )
    cur.execute(sql, params)
    lo, hi, n = cur.fetchone()
    if lo is None or n == 0:
        return None

    entry = _shell(d)
    entry["min"] = float(lo)
    entry["max"] = float(hi)

    buckets = d.buckets
    if float(hi) == float(lo):
        entry["histogram"] = [{"from": float(lo), "to": float(hi), "count": int(n)}]
        return entry

    width = (float(hi) - float(lo)) / buckets
    hist_sql = cte + (
        f"SELECT width_bucket({expr}, %(f_lo)s, %(f_hi)s, %(f_n)s) AS b, count(*) "
        f"FROM candidates d WHERE {expr} IS NOT NULL GROUP BY 1 ORDER BY 1"
    )
    cur.execute(hist_sql, {**params, "f_lo": float(lo), "f_hi": float(hi),
                           "f_n": buckets})
    counts = {int(b): int(c) for b, c in cur.fetchall() if b is not None}
    # width_bucket puts the maximum value in bucket n+1; fold it into the last.
    counts[buckets] = counts.get(buckets, 0) + counts.pop(buckets + 1, 0)
    entry["histogram"] = [
        {"from": float(lo) + width * (i - 1),
         "to": float(lo) + width * i,
         "count": counts.get(i, 0)}
        for i in range(1, buckets + 1)
    ]
    return entry


def _toggle_facet(cur, cte, params, d: FacetDef) -> dict | None:
    if d.key == "has_images":
        pred = "jsonb_array_length(d.thumbnails) > 0"
    elif d.key == "has_attachments":
        pred = (f"jsonb_array_length(COALESCE({_array_expr(d)}, '[]'::jsonb)) > 0")
    else:
        pred = f"{_expr(d)} = 'true'"
    cur.execute(cte + f"SELECT count(*) FROM candidates d WHERE {pred}", params)
    (n,) = cur.fetchone()
    if not n:
        return None
    entry = _shell(d)
    entry["count_true"] = int(n)
    return entry
