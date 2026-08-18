"""The search endpoint. Spec 7, 8, 9.

Two things happen here that deliberately do not happen at index time:

- `title` and `summary` are resolved to the response language, with a
  `translated` flag when the fallback fired.
- Every time-dependent value is computed: deadline_state, and the Images tab's
  flattened thumbnails. `card` stores raw dates only (spec 8).
"""

from __future__ import annotations

import datetime as dt
import time

from django.http import HttpRequest
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError

from api.logging import log_query
from api.schemas import SearchOut
from search.filters import FilterError, parse_filters
from search.query import search_page

router = Router()

MAX_PER_PAGE = 100
CLOSING_SOON_DAYS = 7

# 'images' and 'all' are tabs, not doc_types.
_TAB_TO_DOC_TYPE = {"all": None, "images": None, "shopping": "shopping",
                    "job": "job", "property": "property", "news": "news"}


def _deadline_state(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        d = dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    today = timezone.localdate()
    if d < today:
        return "closed"
    if (d - today).days <= CLOSING_SOON_DAYS:
        return "closing_soon"
    return "open"


def annotate_time(card: dict, doc_type: str) -> dict:
    """Add every value derived from 'now'. Never stored, always computed."""
    out = dict(card)
    if doc_type == "job":
        state = _deadline_state(out.get("deadline"))
        if state:
            out["deadline_state"] = state
    return out


def resolve_display(result, lang: str) -> tuple[str, str, bool]:
    """Returns (title, summary, translated)."""
    # search_page already picked by response language; `translated` records
    # whether that pick came from the other language's field.
    translated = result.matched_lang != lang and bool(result.title)
    return result.title, result.summary, translated


@router.get("/search", response=SearchOut)
def search_endpoint(
    request: HttpRequest,
    q: str = "",
    type: str = "all",
    page: int = 1,
    per_page: int = 20,
    sort: str = "relevance",
    lang: str | None = None,
    f: list[str] = Query(default=[]),
):
    started = time.perf_counter()
    tab = type if type in _TAB_TO_DOC_TYPE else "all"
    doc_type = _TAB_TO_DOC_TYPE[tab]
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    page = max(1, page)

    try:
        filters = parse_filters(f, doc_type)
    except FilterError as exc:
        raise HttpError(400, str(exc)) from exc

    result_page = search_page(
        q, doc_type=doc_type, filters=filters, sort=sort,
        page=page, per_page=per_page,
    )

    response_lang = lang or result_page.plan.response_lang
    results = []
    for r in result_page.results:
        card = annotate_time(r.card, r.doc_type)
        if tab == "images":
            card = {**card, "images": r.thumbnails}
        title, summary, translated = resolve_display(r, response_lang)
        results.append({
            "id": r.id, "source": r.source, "doc_type": r.doc_type, "url": r.url,
            "title": title, "summary": summary, "translated": translated,
            "card": card, "score": r.score,
        })

    latency_ms = int((time.perf_counter() - started) * 1000)
    query_id = log_query(
        request,
        raw=q,
        plan=result_page.plan,
        doc_type=doc_type,
        filters=f,
        result_count=result_page.total,
        latency_ms=latency_ms,
    )

    return {
        "query": {
            "raw": q,
            "detected_lang": result_page.plan.lang,
            "response_lang": response_lang,
            "expanded_terms": (result_page.plan.terms_en
                               + result_page.plan.terms_dv
                               + result_page.plan.terms_latin
                               + result_page.plan.translated_terms),
        },
        "query_id": query_id,
        "total": result_page.total,
        "page": page,
        "per_page": per_page,
        "results": results,
        "facets": result_page.facets,
        "suggestions": [],
    }
