# P5 API and Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the index over a typed HTTP API — search with filters and facets, suggest, detail, the source registry, and a report endpoint — and start recording query and click history from the first request.

**Architecture:** django-ninja over the existing `search.query.search()`. The candidate CTE from P1/P2 grows a whitelisted filter clause and a second statement that aggregates facet counts over the same CTE, so counts always match the result set. Facet definitions are a static per-`doc_type` registry in `search/facets.py`; the dynamic shopping half is P7 and slots into the same response shape. Logging runs on a small thread pool, never on the request path, into month-partitioned tables with BRIN on `created_at`.

**Tech Stack:** django-ninja, Pydantic v2 (already present from P4), PostgreSQL 18 declarative partitioning, pytest + pytest-django.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 7, 8, 8.5, 9, 12.3, 16.3, and 5.7's report rules.

**Depends on:** P1 (`search.query.search`, `search.models`), P2 (`search.lang.build_query_plan`), P4 (`enrich.schemas`, `attrs` shapes, `card` payloads).

---

## Global Constraints

- **Logging is never on the hot path.** It must not add latency to, or fail, a search response. Spec 16.3.
- **`position` is recorded at click time.** It is impossible to reconstruct later, and without it there is no MRR, no nDCG and no usable ranking feature. Spec 16.3.
- **No user identity.** `session_hash` is a salted hash with a daily-rotating salt. There are no accounts and there must be no durable per-person search history. Spec 16.3.
- **A report never triggers reprocessing.** The endpoint is public and transcription plus enrichment cost real money per document, so auto-reprocessing is a denial-of-wallet vector. Reports are inert data; an admin action re-queues. Spec 5.7.
- **The report endpoint always returns 202**, new or duplicate. Telling a caller which documents they have already reported leaks nothing useful and invites probing. Spec 9.
- **Filter keys are whitelisted against the facet registry.** A filter key that is not in the registry is a 400, never a query fragment. Facet values reach SQL as bound parameters only.
- **Facets aggregate over the same candidate CTE as the results.** Counts that do not match the result set are worse than no counts. Spec 7.
- **`title` and `summary` resolve server-side** to the response language with a `translated: true` flag when falling back. The frontend never chooses. Spec 9.
- **`facets` is an ordered list, not a map.** For shopping the ordering is computed per query (P7) and that ordering is meaningful. Spec 9.
- **`/documents/{id}` serves shopping, jobs and property only.** News links out. Spec 8.5.
- **Nothing time-dependent is read from `card`.** `deadline_state` and relative time are computed in the response serializer from the raw dates. Spec 8.
- Version control is **jj**, not git.

---

## File Structure

```
api/
  __init__.py
  apps.py
  urls.py                     the NinjaAPI instance
  schemas.py                  request and response models
  routers/
    __init__.py
    search.py                 /search
    suggest.py                /suggest
    documents.py              /documents/{id}, /documents/{id}/report
    meta.py                   /meta
    events.py                 /events/click
  logging.py                  fire-and-forget log writer
  ratelimit.py                DB-backed limiter, no new infrastructure

search/facets.py              static facet registry + aggregation SQL
search/filters.py             filter parsing and whitelisting
search/query.py               MODIFIED: filters, pagination, facet CTE reuse
search/interleave.py          the All-tab type cap
search/suggest.py             term table + trigram suggest
search/models.py              MODIFIED: DocumentReport, QueryLog, ClickLog, SuggestTerm
search/migrations/000X_*.py   partitioned log tables (raw SQL)
search/management/commands/
  create_log_partitions.py
  rebuild_suggest_terms.py
  prune_logs.py

beynunehcheh/urls.py          MODIFIED: mount /api/v1
enrich/overlay.py             MODIFIED: write estimated_net_min into attrs

tests/api/...
```

Why `api/` is its own app rather than routers inside `search/`: the API is a presentation layer over three domains (`search`, `enrich`, `gazette`) and putting it in `search` would make `search` import `enrich`, which P4 task 9 went to some trouble to avoid.

---

### Task 1: django-ninja mount and `/api/v1/meta`

**Files:**
- Create: `api/__init__.py`, `api/apps.py`, `api/urls.py`, `api/schemas.py`, `api/routers/__init__.py`, `api/routers/meta.py`
- Modify: `beynunehcheh/urls.py`, `beynunehcheh/settings.py`, `requirements.txt`
- Test: `tests/api/test_meta.py`

**Interfaces:**
- Consumes: `search.models.Source`.
- Produces: `api.urls.api` (the `NinjaAPI` instance), `SourceOut`, `TabOut`, `MetaOut`, `GET /api/v1/meta`.

`/meta` exists so nothing about tabs, labels or source icons is hardcoded in the frontend. It is the first endpoint because every other response references source keys that resolve through it.

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
django-ninja==1.5.0
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

`tests/api/__init__.py` — empty.

`tests/api/conftest.py`:

```python
import pytest
from django.test import Client


@pytest.fixture
def api(db):
    return Client()


@pytest.fixture
def sources(db):
    from search.models import Source
    Source.objects.create(key="ibay", label_en="iBay", label_dv="އައިބޭ",
                          site_url="https://ibay.com.mv",
                          icon="/static/sources/ibay.svg", icon_fallback_text="iB")
    Source.objects.create(key="gazette", label_en="Gazette", label_dv="ގެޒެޓް",
                          site_url="https://gazette.gov.mv",
                          icon="/static/sources/gazette.svg",
                          icon_fallback_text="ގ")
    Source.objects.create(key="retired", label_en="Retired", site_url="https://x",
                          is_active=False)
```

`tests/api/test_meta.py`:

```python
import pytest


@pytest.mark.django_db
def test_meta_lists_active_sources_with_icons(api, sources):
    r = api.get("/api/v1/meta")
    assert r.status_code == 200
    body = r.json()
    keys = [s["key"] for s in body["sources"]]
    assert keys == ["gazette", "ibay"]          # ordered, deterministic
    gazette = body["sources"][0]
    assert gazette["label_dv"] == "ގެޒެޓް"
    assert gazette["icon"] == "/static/sources/gazette.svg"
    assert gazette["icon_fallback_text"] == "ގ"
    assert gazette["site_url"].startswith("https://")


@pytest.mark.django_db
def test_meta_omits_inactive_sources(api, sources):
    assert "retired" not in [s["key"] for s in api.get("/api/v1/meta").json()["sources"]]


@pytest.mark.django_db
def test_meta_lists_the_six_tabs_in_order(api, sources):
    tabs = api.get("/api/v1/meta").json()["tabs"]
    assert [t["key"] for t in tabs] == [
        "all", "shopping", "job", "property", "news", "images"
    ]
    assert all(t["label_en"] and t["label_dv"] for t in tabs)


@pytest.mark.django_db
def test_meta_is_cacheable_and_carries_no_per_request_state(api, sources):
    r = api.get("/api/v1/meta")
    assert "no-store" not in r.headers.get("Cache-Control", "")


@pytest.mark.django_db
def test_openapi_schema_is_served(api):
    assert api.get("/api/v1/openapi.json").status_code == 200
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/api/test_meta.py -v`
Expected: FAIL — 404 on `/api/v1/meta`.

- [ ] **Step 4: Create the app**

`api/__init__.py` — empty.

`api/apps.py`:

```python
from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
```

- [ ] **Step 5: Write the shared schemas**

`api/schemas.py`:

```python
"""Request and response models. Spec 9.

django-ninja generates the OpenAPI document from these and the frontend
generates its TypeScript from that, so a name here is a name in the browser.
Rename deliberately.
"""

from __future__ import annotations

from typing import Any, Literal

from ninja import Schema


class SourceOut(Schema):
    key: str
    label_en: str
    label_dv: str
    icon: str
    icon_fallback_text: str
    accent: str
    site_url: str


class TabOut(Schema):
    key: str
    label_en: str
    label_dv: str
    doc_type: str | None      # None for 'all' and 'images'


class MetaOut(Schema):
    tabs: list[TabOut]
    sources: list[SourceOut]
    doc_types: list[str]
    sorts: list[str]


class FacetValueOut(Schema):
    value: str
    label: str
    count: int


class HistogramBucketOut(Schema):
    from_: float
    to: float
    count: int

    class Config(Schema.Config):
        # `from` is a Python keyword; the wire name is what the frontend sees.
        alias_generator = None


class FacetOut(Schema):
    key: str
    label: str
    label_dv: str = ""
    widget: Literal["checkbox", "range", "toggle"]
    unit: str = ""
    values: list[FacetValueOut] = []
    min: float | None = None
    max: float | None = None
    histogram: list[dict] = []
    count_true: int | None = None


class ResultOut(Schema):
    id: int
    source: str
    doc_type: str
    url: str
    title: str
    summary: str
    translated: bool
    card: dict[str, Any]
    score: float


class QueryEchoOut(Schema):
    raw: str
    detected_lang: str
    response_lang: str
    expanded_terms: list[str]


class SearchOut(Schema):
    query: QueryEchoOut
    query_id: int | None
    total: int
    page: int
    per_page: int
    results: list[ResultOut]
    facets: list[FacetOut]
    suggestions: list[str] = []


class SuggestOut(Schema):
    suggestions: list[dict]


class ReportIn(Schema):
    reason: Literal["stale", "wrong_details", "dead_link", "spam", "other"]
    note: str = ""


class ClickIn(Schema):
    query_id: int
    document_id: int
    position: int


class AcceptedOut(Schema):
    status: str = "accepted"
```

- [ ] **Step 6: Write the meta router and the API instance**

`api/routers/__init__.py` — empty.

`api/routers/meta.py`:

```python
"""The registry endpoint. Spec 4.3.3, 8.5, 9.

Nothing about tabs, labels or icons is hardcoded in the frontend. `card`
payloads carry a source key and the browser resolves it here, once per
session, which is why a card never issues its own request for an icon.
"""

from ninja import Router

from api.schemas import MetaOut
from search.models import Source

router = Router()

# The six tabs from spec 8. 'all' interleaves types; 'images' runs the same
# query and flattens thumbnails. Neither maps to a single doc_type.
TABS = [
    {"key": "all", "label_en": "All", "label_dv": "ހުރިހާ", "doc_type": None},
    {"key": "shopping", "label_en": "Shopping", "label_dv": "ވިޔަފާރި",
     "doc_type": "shopping"},
    {"key": "job", "label_en": "Jobs", "label_dv": "ވަޒީފާ", "doc_type": "job"},
    {"key": "property", "label_en": "Property", "label_dv": "ބިންވެރި",
     "doc_type": "property"},
    {"key": "news", "label_en": "News", "label_dv": "ޚަބަރު", "doc_type": "news"},
    {"key": "images", "label_en": "Images", "label_dv": "ފޮޓޯ", "doc_type": None},
]

SORTS = ["relevance", "newest", "price_asc", "price_desc", "salary_desc"]


@router.get("/meta", response=MetaOut)
def meta(request):
    sources = [
        {
            "key": s.key,
            "label_en": s.label_en,
            "label_dv": s.label_dv or s.label_en,
            "icon": s.icon,
            "icon_fallback_text": s.icon_fallback_text,
            "accent": s.accent,
            "site_url": s.site_url,
        }
        for s in Source.objects.filter(is_active=True).order_by("key")
    ]
    return {
        "tabs": TABS,
        "sources": sources,
        "doc_types": ["shopping", "job", "property", "news"],
        "sorts": SORTS,
    }
```

`api/urls.py`:

```python
from ninja import NinjaAPI

from api.routers import meta

api = NinjaAPI(
    title="Beynunehcheh",
    version="1.0.0",
    urls_namespace="api-v1",
    # CSRF is off because every endpoint here is either read-only or an
    # anonymous append-only event; there is no session-authenticated state to
    # protect. The report and click endpoints are rate-limited instead.
    csrf=False,
)

api.add_router("", meta.router, tags=["meta"])
```

- [ ] **Step 7: Mount it**

In `beynunehcheh/settings.py`, add `'api',` to `INSTALLED_APPS`.

In `beynunehcheh/urls.py`:

```python
from api.urls import api as api_v1

urlpatterns = [
    # ... existing entries stay ...
    path("api/v1/", api_v1.urls),
]
```

- [ ] **Step 8: Run the tests**

Run: `pytest tests/api/test_meta.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 9: Commit**

```bash
jj commit -m "P5 task 1: django-ninja mount and /meta registry"
```

---

### Task 2: Filter parsing and the static facet registry

**Files:**
- Create: `search/filters.py`, `search/facets.py`
- Test: `tests/search/test_filters.py`, `tests/search/test_facets.py`

**Interfaces:**
- Consumes: nothing at runtime; the registry describes `SearchDocument` columns and `attrs` JSONB paths.
- Produces: `FacetDef`, `FACETS: dict[str, list[FacetDef]]`, `facet_def(doc_type, key) -> FacetDef | None`, `Filter`, `parse_filters(raw, doc_type) -> list[Filter]`, `FilterError`, `filter_sql(filters) -> tuple[str, dict]`, `facet_sql(doc_type) -> tuple[str, dict]`.

This is the task where an injection bug would live, so it is written before anything executes a query with it.

- [ ] **Step 1: Write the failing test**

`tests/search/test_filters.py`:

```python
import pytest

from search.filters import Filter, FilterError, filter_sql, parse_filters


def test_enum_filter():
    fs = parse_filters(["job_category:Accounting"], "job")
    assert fs == [Filter(key="job_category", op="eq", values=["Accounting"])]


def test_repeated_key_becomes_an_or_within_the_key():
    fs = parse_filters(["brand:Apple", "brand:Samsung"], "shopping")
    assert len(fs) == 1
    assert fs[0].values == ["Apple", "Samsung"]


def test_range_filter():
    fs = parse_filters(["price:1000..5000"], "shopping")
    assert fs[0].op == "range"
    assert fs[0].lo == 1000.0 and fs[0].hi == 5000.0


def test_open_ended_ranges():
    assert parse_filters(["price:1000.."], "shopping")[0].hi is None
    assert parse_filters(["price:..5000"], "shopping")[0].lo is None


def test_toggle_filter():
    fs = parse_filters(["has_lift:true"], "property")
    assert fs[0].op == "bool" and fs[0].values == [True]


def test_an_unknown_key_is_rejected():
    """Whitelisted against the facet registry. An unknown key must be a 400,
    never a query fragment."""
    with pytest.raises(FilterError) as exc:
        parse_filters(["'; DROP TABLE search_searchdocument; --:x"], "job")
    assert "unknown filter" in str(exc.value)


def test_a_key_valid_for_another_type_is_rejected_for_this_one():
    with pytest.raises(FilterError):
        parse_filters(["bedrooms:3"], "job")


def test_a_malformed_range_is_rejected():
    with pytest.raises(FilterError):
        parse_filters(["price:cheap..expensive"], "shopping")


def test_a_filter_with_no_colon_is_rejected():
    with pytest.raises(FilterError):
        parse_filters(["price"], "shopping")


def test_values_never_reach_sql_as_text():
    """Every value must arrive as a bound parameter. The generated SQL must
    contain no literal from the user."""
    sql, params = filter_sql(parse_filters(["job_category:O'Brien & Co"], "job"))
    assert "O'Brien" not in sql
    assert "O'Brien & Co" in params.values()


def test_filter_sql_for_a_column_backed_facet():
    sql, params = filter_sql(parse_filters(["price:1000..5000"], "shopping"))
    assert "d.price" in sql
    assert 1000.0 in params.values() and 5000.0 in params.values()


def test_filter_sql_for_a_jsonb_backed_facet():
    sql, params = filter_sql(parse_filters(["job_category:Accounting"], "job"))
    assert "attrs" in sql
    assert "->>" in sql


def test_filter_sql_for_an_array_backed_facet():
    """tenant_preference is a JSON array; membership, not equality."""
    sql, _ = filter_sql(parse_filters(["tenant_preference:family"], "property"))
    assert "jsonb_array_elements_text" in sql or "?|" in sql


def test_no_filters_is_an_empty_clause_not_a_syntax_error():
    sql, params = filter_sql([])
    assert sql.strip() == ""
    assert params == {}
```

`tests/search/test_facets.py`:

```python
import pytest

from search.facets import FACETS, facet_def


def test_every_doc_type_has_a_facet_set():
    assert set(FACETS) == {"job", "property", "shopping", "news", "all"}


@pytest.mark.parametrize(
    "doc_type,expected",
    [
        # Spec 8.1
        ("job", {"job_category", "position_type", "salary_state", "employer",
                 "grade", "location", "source", "net_estimate"}),
        # Spec 8.2
        ("property", {"listing_kind", "price", "unit_kind", "is_shared",
                      "bedrooms", "bathrooms", "furnishing", "neighborhood",
                      "island", "atoll", "has_lift", "square_feet",
                      "tenant_preference", "source"}),
        # Spec 8.3 universal half; the dynamic half is P7
        ("shopping", {"price", "condition", "brand", "location", "seller_type",
                      "has_images", "source"}),
        # Spec 8.4
        ("news", {"source", "office", "announcement_type", "has_attachments",
                  "is_tender"}),
    ],
)
def test_the_spec_facet_sets_are_all_present(doc_type, expected):
    keys = {f.key for f in FACETS[doc_type]}
    assert expected <= keys, f"missing from {doc_type}: {expected - keys}"


def test_every_facet_declares_a_widget_and_a_bilingual_label():
    for doc_type, defs in FACETS.items():
        for f in defs:
            assert f.widget in {"checkbox", "range", "toggle"}, (doc_type, f.key)
            assert f.label_en and f.label_dv, (doc_type, f.key)


def test_every_facet_declares_where_its_value_lives():
    for defs in FACETS.values():
        for f in defs:
            assert f.storage in {"column", "attrs", "attrs_array"}, f.key
            assert f.path, f.key


def test_source_is_a_facet_on_every_type():
    """Spec 8.5: the source facet is part of the consistent attribution
    system, not a per-type extra."""
    for doc_type in ("job", "property", "shopping", "news"):
        assert facet_def(doc_type, "source") is not None


def test_rent_ranges_are_declared_per_period_and_currency():
    """Spec 8.2: a 300-per-day guest house room and a 7,000-per-month
    apartment on one slider is meaningless."""
    price = facet_def("property", "price")
    assert price.split_by == ["currency", "price_period"]


def test_no_facet_is_time_dependent():
    """Spec 8. `deadline` appears as a computed response field, never as a
    stored facet value."""
    for defs in FACETS.values():
        for f in defs:
            assert f.key not in {"deadline_state", "days_left", "is_open"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_filters.py tests/search/test_facets.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the facet registry**

`search/facets.py`:

```python
"""The static facet registry. Spec 8.1, 8.2, 8.3, 8.4.

Every facet declares three things: the widget the frontend renders, where the
value lives (a SearchDocument column, an attrs JSONB scalar, or an attrs JSONB
array), and its bilingual label. That is enough for both filtering (filters.py)
and counting (the aggregation SQL below).

The dynamic shopping facets are P7. They produce entries of exactly this shape
at request time and are appended to the static list, which is why the API
returns an ordered list rather than a map.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FacetDef:
    key: str
    label_en: str
    label_dv: str
    widget: str                      # checkbox | range | toggle
    storage: str                     # column | attrs | attrs_array
    path: str                        # column name, or dotted attrs path
    unit: str = ""
    top_n: int = 12
    buckets: int = 10
    # Ranges that are only comparable within a group. A rent slider that mixes
    # currencies or periods is meaningless (spec 8.2).
    split_by: tuple[str, ...] = ()
    always_available: bool = True


def _f(**kw) -> FacetDef:
    return FacetDef(**kw)


_SOURCE = _f(key="source", label_en="Source", label_dv="މަޞްދަރު",
             widget="checkbox", storage="column", path="source")
_LOCATION = _f(key="location", label_en="Location", label_dv="ތަން",
               widget="checkbox", storage="column", path="location")

JOB_FACETS = [
    _f(key="job_category", label_en="Category", label_dv="ބާވަތް",
       widget="checkbox", storage="attrs", path="job_category"),
    _f(key="position_type", label_en="Position type", label_dv="ވަޒީފާގެ ބާވަތް",
       widget="checkbox", storage="attrs", path="position_type"),
    _f(key="net_estimate", label_en="Take-home", label_dv="ލިބޭ މުސާރަ",
       widget="range", storage="attrs", path="estimated_net_min", unit="MVR"),
    _f(key="salary_state", label_en="Salary", label_dv="މުސާރަ",
       widget="checkbox", storage="attrs", path="compensation.salary_state"),
    _f(key="employer", label_en="Employer", label_dv="ވަޒީފާދޭ ފަރާތް",
       widget="checkbox", storage="attrs", path="employer", top_n=20),
    _f(key="grade", label_en="Grade", label_dv="ގްރޭޑް",
       widget="checkbox", storage="attrs", path="grade"),
    _LOCATION,
    _SOURCE,
]

PROPERTY_FACETS = [
    _f(key="listing_kind", label_en="Listing", label_dv="ބާވަތް",
       widget="checkbox", storage="attrs", path="listing_kind"),
    _f(key="price", label_en="Rent", label_dv="ކުލި", widget="range",
       storage="column", path="price",
       split_by=("currency", "price_period")),
    _f(key="unit_kind", label_en="Unit", label_dv="ޔުނިޓް",
       widget="checkbox", storage="attrs", path="occupancy.unit_kind"),
    _f(key="is_shared", label_en="Shared", label_dv="ޙިއްޞާކުރެވޭ",
       widget="toggle", storage="attrs", path="occupancy.is_shared"),
    _f(key="bedrooms", label_en="Bedrooms", label_dv="ކޮޓަރި",
       widget="checkbox", storage="attrs", path="bedrooms"),
    _f(key="bathrooms", label_en="Bathrooms", label_dv="ފާޚާނާ",
       widget="checkbox", storage="attrs", path="bathrooms"),
    _f(key="furnishing", label_en="Furnishing", label_dv="ފަރުނީޗަރު",
       widget="checkbox", storage="attrs", path="furnishing"),
    _f(key="neighborhood", label_en="Neighbourhood", label_dv="އަވަށް",
       widget="checkbox", storage="attrs", path="neighborhood", top_n=20),
    _f(key="island", label_en="Island", label_dv="ރަށް",
       widget="checkbox", storage="column", path="island"),
    _f(key="atoll", label_en="Atoll", label_dv="އަތޮޅު",
       widget="checkbox", storage="column", path="atoll"),
    _f(key="has_lift", label_en="Lift", label_dv="ލިފްޓް",
       widget="toggle", storage="attrs", path="has_lift"),
    _f(key="square_feet", label_en="Square feet", label_dv="އަކަފޫޓު",
       widget="range", storage="attrs", path="square_feet", unit="sqft"),
    _f(key="tenant_preference", label_en="Tenants", label_dv="ކުއްޔަށްހިފާ ފަރާތް",
       widget="checkbox", storage="attrs_array", path="tenant_preference"),
    _SOURCE,
]

SHOPPING_FACETS = [
    _f(key="price", label_en="Price", label_dv="އަގު", widget="range",
       storage="column", path="price", split_by=("currency",)),
    _f(key="condition", label_en="Condition", label_dv="ޙާލަތު",
       widget="checkbox", storage="attrs", path="condition"),
    _f(key="brand", label_en="Brand", label_dv="ބްރޭންޑް",
       widget="checkbox", storage="attrs", path="brand", top_n=20),
    _f(key="seller_type", label_en="Seller", label_dv="ވިއްކާ ފަރާތް",
       widget="checkbox", storage="attrs", path="seller_type"),
    _f(key="has_images", label_en="Has photos", label_dv="ފޮޓޯ ހުރި",
       widget="toggle", storage="column", path="thumbnails"),
    _LOCATION,
    _SOURCE,
]

NEWS_FACETS = [
    _SOURCE,
    _f(key="office", label_en="Office", label_dv="އޮފީސް",
       widget="checkbox", storage="attrs", path="office", top_n=20),
    _f(key="announcement_type", label_en="Type", label_dv="ބާވަތް",
       widget="checkbox", storage="attrs", path="announcement_type"),
    _f(key="has_attachments", label_en="Has documents", label_dv="ލިޔުން ހުރި",
       widget="toggle", storage="attrs", path="documents"),
    _f(key="is_tender", label_en="Tender or auction", label_dv="ބީލަން",
       widget="toggle", storage="attrs", path="is_tender"),
]

# The 'all' tab offers only what is meaningful across every type.
ALL_FACETS = [_SOURCE, _LOCATION,
              _f(key="doc_type", label_en="Type", label_dv="ބާވަތް",
                 widget="checkbox", storage="column", path="doc_type")]

FACETS: dict[str, list[FacetDef]] = {
    "job": JOB_FACETS,
    "property": PROPERTY_FACETS,
    "shopping": SHOPPING_FACETS,
    "news": NEWS_FACETS,
    "all": ALL_FACETS,
}


def facet_def(doc_type: str | None, key: str) -> FacetDef | None:
    for f in FACETS.get(doc_type or "all", ALL_FACETS):
        if f.key == key:
            return f
    return None
```

- [ ] **Step 4: Write the filter parser**

`search/filters.py`:

```python
"""Filter parsing and SQL generation. Spec 9.

Wire format is `key:value` for enums and `key:min..max` for ranges, repeated
for multi-select. The frontend does not need to know which facets exist for
which query, which is the whole point of the facet registry.

Security: every key is looked up in the registry and every value is bound as a
parameter. No user string is ever concatenated into SQL. A key that is not in
the registry is a 400, not a query fragment -- the registry is a whitelist and
that is its second job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from search.facets import FacetDef, facet_def

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


class FilterError(ValueError):
    """A filter the API must reject with 400."""


@dataclass
class Filter:
    key: str
    op: str                       # eq | range | bool
    values: list = field(default_factory=list)
    lo: float | None = None
    hi: float | None = None
    definition: FacetDef | None = None

    def __eq__(self, other):      # definition is incidental to identity
        return (self.key, self.op, self.values, self.lo, self.hi) == (
            other.key, other.op, other.values, other.lo, other.hi)


def parse_filters(raw: list[str] | None, doc_type: str | None) -> list[Filter]:
    if not raw:
        return []

    by_key: dict[str, Filter] = {}
    for item in raw:
        if ":" not in item:
            raise FilterError(f"malformed filter {item!r}: expected key:value")
        key, _, value = item.partition(":")
        key = key.strip()

        if not _IDENT.match(key):
            raise FilterError(f"unknown filter {key!r}")
        d = facet_def(doc_type, key)
        if d is None:
            raise FilterError(f"unknown filter {key!r} for type {doc_type!r}")

        if d.widget == "range":
            lo, hi = _parse_range(value, key)
            by_key[key] = Filter(key=key, op="range", lo=lo, hi=hi, definition=d)
        elif d.widget == "toggle":
            v = value.strip().lower()
            if v in _TRUE:
                b = True
            elif v in _FALSE:
                b = False
            else:
                raise FilterError(f"filter {key!r} expects a boolean, got {value!r}")
            by_key[key] = Filter(key=key, op="bool", values=[b], definition=d)
        else:
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = Filter(key=key, op="eq", values=[value],
                                     definition=d)
            else:
                existing.values.append(value)

    return list(by_key.values())


def _parse_range(value: str, key: str) -> tuple[float | None, float | None]:
    if ".." not in value:
        raise FilterError(f"filter {key!r} expects min..max, got {value!r}")
    lo_s, _, hi_s = value.partition("..")

    def num(s):
        s = s.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise FilterError(f"filter {key!r} expects numbers, got {s!r}") from None

    lo, hi = num(lo_s), num(hi_s)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _expr(d: FacetDef) -> str:
    """The SQL expression that yields this facet's value for one row.

    The path is registry-controlled, never user input, so building the JSONB
    accessor by string is safe here and only here.
    """
    if d.storage == "column":
        return f"d.{d.path}"
    parts = d.path.split(".")
    if len(parts) == 1:
        return f"d.attrs ->> '{parts[0]}'"
    inner = " -> ".join(f"'{p}'" for p in parts[:-1])
    return f"d.attrs -> {inner} ->> '{parts[-1]}'"


def _array_expr(d: FacetDef) -> str:
    parts = d.path.split(".")
    if len(parts) == 1:
        return f"d.attrs -> '{parts[0]}'"
    return "d.attrs -> " + " -> ".join(f"'{p}'" for p in parts)


def filter_sql(filters: list[Filter]) -> tuple[str, dict]:
    """Returns an AND-joined clause fragment and its bound parameters."""
    if not filters:
        return "", {}

    clauses: list[str] = []
    params: dict = {}

    for i, f in enumerate(filters):
        d = f.definition or facet_def(None, f.key)
        p = f"flt{i}"

        if f.op == "range":
            expr = _expr(d)
            cast = expr if d.storage == "column" else f"({expr})::numeric"
            if f.lo is not None:
                clauses.append(f"{cast} >= %({p}_lo)s")
                params[f"{p}_lo"] = f.lo
            if f.hi is not None:
                clauses.append(f"{cast} <= %({p}_hi)s")
                params[f"{p}_hi"] = f.hi

        elif f.op == "bool":
            if d.key == "has_images":
                clauses.append(
                    "jsonb_array_length(d.thumbnails) > 0"
                    if f.values[0] else "jsonb_array_length(d.thumbnails) = 0"
                )
            elif d.key == "has_attachments":
                clauses.append(
                    f"jsonb_array_length(COALESCE({_array_expr(d)}, '[]'::jsonb)) > 0"
                    if f.values[0] else
                    f"jsonb_array_length(COALESCE({_array_expr(d)}, '[]'::jsonb)) = 0"
                )
            else:
                clauses.append(f"{_expr(d)} = %({p})s")
                params[p] = "true" if f.values[0] else "false"

        elif d.storage == "attrs_array":
            clauses.append(
                f"EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                f"COALESCE({_array_expr(d)}, '[]'::jsonb)) v "
                f"WHERE v = ANY(%({p})s))"
            )
            params[p] = list(f.values)

        else:
            clauses.append(f"{_expr(d)} = ANY(%({p})s)")
            params[p] = list(f.values)

    return " AND " + " AND ".join(f"({c})" for c in clauses), params
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/search/test_filters.py tests/search/test_facets.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P5 task 2: facet registry and filter whitelisting"
```

---

### Task 3: Faceted, filtered, paginated search

**Files:**
- Modify: `search/query.py`, `enrich/overlay.py`
- Create: `search/interleave.py`
- Test: `tests/search/test_query_facets.py`, `tests/search/test_interleave.py`

**Interfaces:**
- Consumes: `search.filters`, `search.facets`.
- Produces: `SearchPage(results, total, facets, plan)`, `search_page(q, *, doc_type=None, filters=(), sort="relevance", page=1, per_page=20) -> SearchPage`, `interleave(results, cap=3) -> list[SearchResult]`.

The candidate CTE is materialized once and both the result page and the facet counts read it, so counts always match the result set.

- [ ] **Step 1: Amend the enrich overlay for the salary facet**

`net_estimate` is a range facet but a computed value, and spec 4.3.2 names `estimated_net_min` as the sortable, facetable figure. Add it to `attrs` at enrichment time so it is a plain JSONB number rather than something the query has to recompute.

In `enrich/overlay.py`, after `draft.attrs = {**draft.attrs, **attrs_model.model_dump()}`, insert:

```python
    # The only figure comparable across ads that itemize differently, so it is
    # what the salary facet and the salary sort read. Spec 4.3.2, 7.
    if draft.doc_type == "job":
        from enrich.compensation import estimate_net
        est = estimate_net(attrs_model.compensation)
        draft.attrs["estimated_net_min"] = (
            round(est.value, 2) if est else attrs_model.compensation.basic_salary
        )
```

Add to `tests/enrich/test_overlay.py`:

```python
@pytest.mark.django_db
def test_estimated_net_min_is_written_for_the_salary_facet():
    EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="h" * 64,
        doc_type="job", status="ok",
        attrs={"compensation": {"basic_salary": 10750, "salary_state": "listed",
                                "pension_applies": True, "completeness": "basic_only"}},
    )
    out = apply_enrichment(_draft())
    assert out.attrs["estimated_net_min"] == pytest.approx(9997.50)
```

- [ ] **Step 2: Write the failing tests**

`tests/search/test_query_facets.py`:

```python
import pytest

from search.filters import parse_filters
from search.models import SearchDocument
from search.query import search_page


@pytest.fixture
def corpus(db):
    def mk(**kw):
        base = dict(source="ibay", doc_type="shopping", url="https://x",
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
```

`tests/search/test_interleave.py`:

```python
from search.interleave import interleave
from search.query import SearchResult


def _r(i, doc_type):
    return SearchResult(id=i, source="ibay", source_key=str(i), doc_type=doc_type,
                        url="https://x", title="t", summary="s", card={},
                        score=1.0 / i, matched_lang="en")


def test_no_more_than_three_consecutive_results_of_one_type():
    """Spec 8: 16k shopping listings must not bury 306 iulaan."""
    results = [_r(i, "shopping") for i in range(1, 21)]
    results += [_r(i, "job") for i in range(21, 25)]
    out = interleave(results, cap=3)
    run, prev = 0, None
    for r in out:
        run = run + 1 if r.doc_type == prev else 1
        prev = r.doc_type
        assert run <= 3


def test_interleaving_preserves_every_result():
    results = [_r(i, "shopping") for i in range(1, 11)] + [_r(11, "job")]
    assert len(interleave(results)) == 11
    assert {r.id for r in interleave(results)} == {r.id for r in results}


def test_relative_order_within_a_type_is_preserved():
    results = [_r(i, "shopping") for i in range(1, 8)] + [_r(8, "job")]
    out = [r.id for r in interleave(results, cap=3) if r.doc_type == "shopping"]
    assert out == sorted(out)


def test_a_single_type_result_set_is_returned_unchanged():
    results = [_r(i, "news") for i in range(1, 6)]
    assert [r.id for r in interleave(results, cap=3)] == [1, 2, 3, 4, 5]
```

- [ ] **Step 3: Run to confirm failure**

Run: `pytest tests/search/test_query_facets.py tests/search/test_interleave.py -v`
Expected: FAIL — `ImportError: cannot import name 'search_page'`.

- [ ] **Step 4: Write the interleaver**

`search/interleave.py`:

```python
"""The All-tab type cap. Spec 8.

`All` interleaves types with a cap of three consecutive results from one type,
so 16,000 shopping listings cannot bury 306 iulaan. Relative order within a
type is preserved -- this reorders across types only, it never re-ranks.
"""

from __future__ import annotations

from collections import defaultdict, deque


def interleave(results: list, cap: int = 3) -> list:
    if not results:
        return []

    queues: dict[str, deque] = defaultdict(deque)
    order: list[str] = []
    for r in results:
        if r.doc_type not in queues:
            order.append(r.doc_type)
        queues[r.doc_type].append(r)

    if len(order) == 1:
        return list(results)

    out: list = []
    run_type, run_len = None, 0

    while any(queues[t] for t in order):
        # Prefer the highest-scoring available head that would not break the
        # cap; fall back to any head when only the capped type remains.
        best = None
        for t in order:
            if not queues[t]:
                continue
            if t == run_type and run_len >= cap:
                continue
            head = queues[t][0]
            if best is None or head.score > queues[best][0].score:
                best = t
        if best is None:
            best = next(t for t in order if queues[t])

        pick = queues[best].popleft()
        run_len = run_len + 1 if best == run_type else 1
        run_type = best
        out.append(pick)

    return out
```

- [ ] **Step 5: Extend the query module**

In `search/query.py`, add the imports:

```python
from dataclasses import field

from search.facets import FACETS, FacetDef
from search.filters import Filter, filter_sql, _expr, _array_expr
from search.interleave import interleave
```

Add the page dataclass and the new SQL. The candidate CTE is unchanged in shape — it grows a `{filters}` placeholder that `filter_sql` fills with an already-parameterized fragment.

```python
@dataclass(slots=True)
class SearchPage:
    results: list[SearchResult]
    total: int
    facets: list[dict]
    plan: QueryPlan


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
       r_en, r_dv, r_latin, score,
       count(*) OVER () AS total
FROM scored
ORDER BY {order_by}
LIMIT %(limit)s OFFSET %(offset)s
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
```

Refactor `search()` so both it and `search_page()` share one parameter builder:

```python
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
```

and add:

```python
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
    fsql, fparams = filter_sql(filters)

    params = _base_params(plan, doc_type, candidate_limit)
    params.update(fparams)
    params["limit"] = per_page
    params["offset"] = max(0, (page - 1) * per_page)

    sql = _PAGE_SQL.format(
        filters=fsql,
        score_expr=_SCORE_EXPR,
        order_by=_SORTS.get(sort, _SORTS["relevance"]),
    )

    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    total = rows[0][-1] if rows else 0
    results = [_to_result(row, plan) for row in rows]
    if doc_type is None:
        results = interleave(results)

    facets = compute_facets(doc_type, filters, params, fsql)
    return SearchPage(results=results, total=int(total), facets=facets, plan=plan)
```

Extract the row-to-`SearchResult` conversion already in `search()` into `_to_result(row, plan)` and have both callers use it. The existing `search()` keeps working unchanged for callers that want the simple list.

- [ ] **Step 6: Write the facet aggregation**

Append to `search/query.py`:

```python
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
            if d.widget == "checkbox":
                entry = _enum_facet(cur, cte, params, d)
            elif d.widget == "range":
                entry = _range_facet(cur, cte, params, d)
            else:
                entry = _toggle_facet(cur, cte, params, d)
            if entry is not None:
                out.append(entry)
    return out


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
```

- [ ] **Step 7: Run the tests**

Run: `pytest tests/search/ -v`
Expected: PASS, including the P1 and P2 suites — `search()` must still behave identically.

- [ ] **Step 8: Commit**

```bash
jj commit -m "P5 task 3: faceted, filtered, paginated search"
```

---

### Task 4: `GET /api/v1/search`

**Files:**
- Create: `api/routers/search.py`
- Modify: `api/urls.py`
- Test: `tests/api/test_search.py`

**Interfaces:**
- Consumes: `search.query.search_page`, `search.filters.parse_filters`.
- Produces: `GET /api/v1/search`, `resolve_display(result, lang) -> tuple[str, str, bool]`, `annotate_time(card, doc_type) -> dict`.

- [ ] **Step 1: Write the failing test**

`tests/api/test_search.py`:

```python
import datetime as dt

import pytest
from django.utils import timezone

from search.models import SearchDocument


@pytest.fixture
def docs(db, sources):
    SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        title_en="iPhone 13", summary_en="A used iPhone.", price=9500,
        attrs={"brand": "Apple"}, card={"source": "ibay", "title": "iPhone 13"},
        thumbnails=["https://x/1.jpg"],
    )
    SearchDocument.objects.create(
        source="gazette", source_key="IUL-1", doc_type="job",
        url="https://gazette.gov.mv/iulaan/1",
        title_dv="އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ", summary_dv="ވަޒީފާގެ ފުރުޞަތު",
        title_en="", summary_en="",
        attrs={"job_category": "Admin"},
        card={"source": "gazette", "role": "Administrative Officer",
              "deadline": (timezone.now() + dt.timedelta(days=2)).date().isoformat()},
    )
    from django.core.management import call_command
    call_command("reindex_vectors")


@pytest.mark.django_db
def test_search_returns_the_documented_envelope(api, docs):
    body = api.get("/api/v1/search?q=iphone").json()
    assert set(body) >= {"query", "total", "page", "per_page", "results",
                         "facets", "suggestions", "query_id"}
    assert body["query"]["raw"] == "iphone"
    assert body["query"]["detected_lang"]
    assert body["query"]["response_lang"]


@pytest.mark.django_db
def test_a_result_carries_source_key_not_an_icon_path(api, docs):
    r = api.get("/api/v1/search?q=iphone").json()["results"][0]
    assert r["source"] == "ibay"
    assert r["card"]["source"] == "ibay"
    assert "icon" not in r["card"]


@pytest.mark.django_db
def test_title_falls_back_across_languages_and_says_so(api, docs):
    """Spec 9: title and summary resolve server-side to the response language,
    falling back with a `translated: true` flag. The frontend never chooses."""
    body = api.get("/api/v1/search?q=officer&lang=en").json()
    r = body["results"][0]
    assert r["title"]                    # not empty despite title_en being blank
    assert r["translated"] is True


@pytest.mark.django_db
def test_a_document_with_a_native_title_is_not_flagged_translated(api, docs):
    r = api.get("/api/v1/search?q=iphone&lang=en").json()["results"][0]
    assert r["translated"] is False


@pytest.mark.django_db
def test_deadline_state_is_computed_per_request_and_not_stored(api, docs):
    """Spec 8: a gazette card is written once. `deadline_state` must be derived
    from the raw date at response time or a closed vacancy advertises itself as
    open forever."""
    r = next(x for x in api.get("/api/v1/search?q=officer").json()["results"]
             if x["doc_type"] == "job")
    assert r["card"]["deadline_state"] == "closing_soon"
    stored = SearchDocument.objects.get(source_key="IUL-1")
    assert "deadline_state" not in stored.card


@pytest.mark.django_db
def test_a_past_deadline_renders_closed(api, docs):
    d = SearchDocument.objects.get(source_key="IUL-1")
    d.card["deadline"] = "2020-01-01"
    d.save()
    r = next(x for x in api.get("/api/v1/search?q=officer").json()["results"]
             if x["doc_type"] == "job")
    assert r["card"]["deadline_state"] == "closed"


@pytest.mark.django_db
def test_filters_are_accepted_as_repeated_query_params(api, docs):
    body = api.get("/api/v1/search?q=iphone&type=shopping&f=brand:Apple").json()
    assert body["total"] == 1


@pytest.mark.django_db
def test_an_unknown_filter_key_is_a_400_not_a_500(api, docs):
    r = api.get("/api/v1/search?q=iphone&type=shopping&f=nonsense:1")
    assert r.status_code == 400
    assert "unknown filter" in r.json()["detail"]


@pytest.mark.django_db
def test_per_page_is_clamped(api, docs):
    body = api.get("/api/v1/search?q=iphone&per_page=5000").json()
    assert body["per_page"] <= 100


@pytest.mark.django_db
def test_an_empty_query_returns_an_empty_envelope_not_a_400(api, docs):
    body = api.get("/api/v1/search?q=").json()
    assert body["total"] == 0 and body["results"] == []


@pytest.mark.django_db
def test_images_tab_flattens_thumbnails(api, docs):
    body = api.get("/api/v1/search?q=iphone&type=images").json()
    assert body["results"][0]["card"]["images"] == ["https://x/1.jpg"]


@pytest.mark.django_db
def test_the_response_carries_a_query_id_for_click_logging(api, docs):
    body = api.get("/api/v1/search?q=iphone").json()
    assert isinstance(body["query_id"], int)


@pytest.mark.django_db
def test_a_dhivehi_query_gets_a_dhivehi_response_language(api, docs):
    body = api.get("/api/v1/search?q=ވަޒީފާ").json()
    assert body["query"]["response_lang"] == "dv"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/api/test_search.py -v`
Expected: FAIL — 404.

- [ ] **Step 3: Write the router**

`api/routers/search.py`:

```python
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
    if lang == "dv":
        primary_t, fallback_t = result.title, ""
    else:
        primary_t, fallback_t = result.title, ""
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
            "detected_lang": result_page.plan.detected_lang,
            "response_lang": response_lang,
            "expanded_terms": (result_page.plan.terms_en
                               + result_page.plan.terms_dv
                               + result_page.plan.terms_latin),
        },
        "query_id": query_id,
        "total": result_page.total,
        "page": page,
        "per_page": per_page,
        "results": results,
        "facets": result_page.facets,
        "suggestions": [],
    }
```

`search_page` must carry `thumbnails` onto the result for the Images tab. Add `thumbnails: list = field(default_factory=list)` to `SearchResult` in `search/query.py` and populate it in `_to_result` from the column the page SQL now selects.

Register in `api/urls.py`:

```python
from api.routers import meta, search

api.add_router("", search.router, tags=["search"])
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/api/test_search.py -v`
Expected: FAIL on `log_query` — that is task 6. Write a temporary stub returning `None` in `api/logging.py`, get the rest green, then task 6 replaces it.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P5 task 4: GET /search"
```

---

### Task 5: Suggest, and the term table behind it

**Files:**
- Create: `search/suggest.py`, `search/management/commands/rebuild_suggest_terms.py`, `api/routers/suggest.py`
- Modify: `search/models.py` (add `SuggestTerm`), `api/urls.py`
- Test: `tests/search/test_suggest.py`, `tests/api/test_suggest.py`

**Interfaces:**
- Consumes: `SearchDocument`, `search.lang`.
- Produces: `SuggestTerm` model, `suggest(q, limit=8) -> list[dict]`, `rebuild_terms() -> int`, `GET /api/v1/suggest`.

Trigram over a term table rather than over documents: a `LIKE '%x%'` across 71,445 titles is a sequential scan on every keystroke, and the term table is a few thousand rows with a GIN trigram index on one column.

- [ ] **Step 1: Write the failing test**

`tests/search/test_suggest.py`:

```python
import pytest

from search.models import SearchDocument, SuggestTerm
from search.suggest import rebuild_terms, suggest


@pytest.fixture
def indexed(db):
    for i, (t_en, t_dv, dtype) in enumerate([
        ("iPhone 13 Pro Max", "", "shopping"),
        ("iPhone 12", "", "shopping"),
        ("Samsung Galaxy", "", "shopping"),
        ("", "ވަޒީފާގެ ފުރުޞަތު", "job"),
        ("", "ވަޒީފާގެ ފުރުޞަތު", "job"),
    ], start=1):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type=dtype, url="https://x",
                                      title_en=t_en, title_dv=t_dv)
    rebuild_terms()


@pytest.mark.django_db
def test_rebuild_extracts_terms_with_frequencies(indexed):
    iphone = SuggestTerm.objects.get(term="iphone")
    assert iphone.frequency == 2
    assert iphone.script == "latin"


@pytest.mark.django_db
def test_thaana_terms_are_recorded_with_their_script(indexed):
    assert SuggestTerm.objects.filter(script="thaana").exists()


@pytest.mark.django_db
def test_suggest_prefix_match_ranks_by_frequency(indexed):
    out = [s["term"] for s in suggest("ipho")]
    assert out[0] == "iphone"


@pytest.mark.django_db
def test_suggest_survives_a_typo_via_trigram(indexed):
    assert "iphone" in [s["term"] for s in suggest("ihpone")]


@pytest.mark.django_db
def test_suggest_works_in_thaana(indexed):
    assert suggest("ވަޒީފާ")


@pytest.mark.django_db
def test_suggest_returns_the_doc_type_a_term_is_most_common_in(indexed):
    """So the frontend can render 'iphone -- in Shopping' with the tab icon."""
    assert suggest("ipho")[0]["doc_type"] == "shopping"


@pytest.mark.django_db
def test_a_single_character_query_returns_nothing():
    """Trigram similarity on one character matches everything."""
    assert suggest("i") == []


@pytest.mark.django_db
def test_rebuild_is_idempotent(indexed):
    before = SuggestTerm.objects.count()
    rebuild_terms()
    assert SuggestTerm.objects.count() == before
```

`tests/api/test_suggest.py`:

```python
import pytest


@pytest.mark.django_db
def test_suggest_endpoint(api, db):
    from search.models import SuggestTerm
    SuggestTerm.objects.create(term="iphone", frequency=9, script="latin",
                               doc_type="shopping")
    body = api.get("/api/v1/suggest?q=ipho").json()
    assert body["suggestions"][0]["term"] == "iphone"


@pytest.mark.django_db
def test_suggest_with_no_query_is_empty_not_an_error(api, db):
    assert api.get("/api/v1/suggest?q=").json()["suggestions"] == []
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_suggest.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Add the model**

In `search/models.py`:

```python
class SuggestTerm(models.Model):
    """Autocomplete vocabulary. Spec 9.

    Derived and disposable, like SearchDocument: rebuilt from titles by
    `rebuild_suggest_terms`. Trigram-searched here rather than over the
    documents themselves because a substring match across 71,445 titles is a
    sequential scan on every keystroke.
    """

    term = models.CharField(max_length=64, unique=True)
    frequency = models.IntegerField(default=0)
    script = models.CharField(max_length=8)     # latin | thaana
    doc_type = models.CharField(max_length=32, blank=True)

    class Meta:
        indexes = [models.Index(fields=["-frequency"], name="suggest_freq")]

    def __str__(self):
        return self.term
```

Migration: `python manage.py makemigrations search`, then hand-add the trigram index to the generated migration:

```python
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class Migration(migrations.Migration):
    operations = [
        # ... the generated CreateModel for SuggestTerm stays first ...
        migrations.AddIndex(
            model_name="suggestterm",
            index=GinIndex(fields=["term"], name="suggest_term_trgm",
                           opclasses=["gin_trgm_ops"]),
        ),
    ]
```

`GinIndex` with `opclasses` requires `fields` and `opclasses` to be the same length; if Django rejects it, fall back to `migrations.RunSQL("CREATE INDEX suggest_term_trgm ON search_suggestterm USING gin (term gin_trgm_ops)", reverse_sql="DROP INDEX suggest_term_trgm")`.

- [ ] **Step 4: Write the suggest module**

`search/suggest.py`:

```python
"""Autocomplete. Spec 9.

Prefix matches first (that is what a user typing expects), then trigram
neighbours (that is what rescues a typo), both ranked by corpus frequency.
"""

from __future__ import annotations

import re
from collections import Counter

from django.db import connection, transaction

from search.models import SearchDocument, SuggestTerm

MIN_QUERY_LEN = 2
MIN_TERM_LEN = 3
MAX_TERMS = 20_000
_TOKEN = re.compile(r"[\wހ-޿]+", re.UNICODE)


def _script(term: str) -> str:
    return "thaana" if any("ހ" <= c <= "޿" for c in term) else "latin"


def rebuild_terms() -> int:
    """Rebuild the whole table from current titles.

    Streams with .iterator() -- this reads every row in the corpus and must not
    materialize it (spec 12.4).
    """
    freq: Counter[str] = Counter()
    types: dict[str, Counter[str]] = {}

    qs = (SearchDocument.objects.using("direct")
          .filter(is_active=True)
          .only("title_en", "title_dv", "title_latin", "doc_type"))
    for doc in qs.iterator(chunk_size=500):
        seen = set()
        for title in (doc.title_en, doc.title_dv, doc.title_latin):
            for tok in _TOKEN.findall((title or "").lower()):
                if len(tok) < MIN_TERM_LEN or tok.isdigit():
                    continue
                seen.add(tok)
        for tok in seen:
            freq[tok] += 1
            types.setdefault(tok, Counter())[doc.doc_type] += 1

    rows = [
        SuggestTerm(term=t, frequency=n, script=_script(t),
                    doc_type=types[t].most_common(1)[0][0])
        for t, n in freq.most_common(MAX_TERMS)
    ]
    with transaction.atomic(using="direct"):
        SuggestTerm.objects.using("direct").all().delete()
        SuggestTerm.objects.using("direct").bulk_create(rows, batch_size=1000)
    return len(rows)


_SQL = """
SELECT term, frequency, script, doc_type,
       CASE WHEN term LIKE %(prefix)s THEN 1 ELSE 0 END AS is_prefix,
       similarity(term, %(q)s) AS sim
FROM search_suggestterm
WHERE term LIKE %(prefix)s OR term %% %(q)s
ORDER BY is_prefix DESC, sim DESC, frequency DESC, term
LIMIT %(limit)s
"""


def suggest(q: str, limit: int = 8) -> list[dict]:
    q = (q or "").strip().lower()
    if len(q) < MIN_QUERY_LEN:
        return []
    with connection.cursor() as cur:
        cur.execute(_SQL, {"q": q, "prefix": f"{q}%", "limit": limit})
        return [
            {"term": t, "frequency": f, "script": s, "doc_type": d}
            for t, f, s, d, _p, _sim in cur.fetchall()
        ]
```

`search/management/commands/rebuild_suggest_terms.py`:

```python
from django.core.management.base import BaseCommand

from search.suggest import rebuild_terms


class Command(BaseCommand):
    help = "Rebuild the autocomplete term table from current document titles."

    def handle(self, *args, **opts):
        n = rebuild_terms()
        self.stdout.write(self.style.SUCCESS(f"{n} terms"))
```

`api/routers/suggest.py`:

```python
from ninja import Router

from api.schemas import SuggestOut
from search.suggest import suggest as suggest_terms

router = Router()


@router.get("/suggest", response=SuggestOut)
def suggest(request, q: str = "", limit: int = 8):
    return {"suggestions": suggest_terms(q, limit=max(1, min(limit, 20)))}
```

Register it in `api/urls.py`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/search/test_suggest.py tests/api/test_suggest.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P5 task 5: suggest and the term table"
```

---

### Task 6: Query and click logging

**Files:**
- Create: `api/logging.py`, `search/management/commands/create_log_partitions.py`, `search/management/commands/prune_logs.py`, `api/routers/events.py`
- Modify: `search/models.py`, `search/migrations/` (raw SQL partitioned tables), `api/urls.py`, `beynunehcheh/settings.py`
- Test: `tests/api/test_logging.py`, `tests/search/test_log_partitions.py`

**Interfaces:**
- Consumes: `search.models.SearchDocument`.
- Produces: `QueryLog`, `ClickLog`, `log_query(request, ...) -> int | None`, `log_click(query_id, document_id, position)`, `session_hash(request) -> str`, `POST /api/v1/events/click`, `create_log_partitions`, `prune_logs`.

Shipping the API without this would mean discarding the exact history that makes ranking work possible, and every day it runs unlogged is history that cannot be recovered.

- [ ] **Step 1: Write the failing test**

`tests/api/test_logging.py`:

```python
import datetime as dt

import pytest
from django.test import override_settings

from search.models import ClickLog, QueryLog, SearchDocument


@pytest.fixture
def doc(db):
    return SearchDocument.objects.create(source="ibay", source_key="1",
                                         doc_type="shopping", url="https://x",
                                         title_en="iPhone 13")


@pytest.mark.django_db
def test_a_search_writes_a_query_log(api, doc):
    from django.core.management import call_command
    call_command("reindex_vectors")
    api.get("/api/v1/search?q=iphone&type=shopping")
    log = QueryLog.objects.get()
    assert log.q_raw == "iphone"
    assert log.doc_type == "shopping"
    assert log.result_count >= 0
    assert log.latency_ms >= 0
    assert log.session_hash


@pytest.mark.django_db
def test_the_log_records_the_filters_that_were_applied(api, doc):
    from django.core.management import call_command
    call_command("reindex_vectors")
    api.get("/api/v1/search?q=iphone&type=shopping&f=condition:New")
    assert QueryLog.objects.get().filters == ["condition:New"]


@pytest.mark.django_db
def test_a_zero_result_query_is_logged(api, doc):
    """The immediate payoff, before any ranking work: zero-result queries
    become a measurable list. Spec 16.3."""
    api.get("/api/v1/search?q=zzzznothingmatchesthis")
    log = QueryLog.objects.get()
    assert log.result_count == 0


@pytest.mark.django_db
def test_no_raw_ip_or_user_agent_is_stored(api, doc):
    """There are no accounts and there must be no durable per-person search
    history. Spec 16.3."""
    api.get("/api/v1/search?q=iphone", HTTP_USER_AGENT="Mozilla/5.0 Secret",
            REMOTE_ADDR="203.0.113.9")
    log = QueryLog.objects.get()
    values = " ".join(str(v) for v in log.__dict__.values())
    assert "203.0.113.9" not in values
    assert "Mozilla" not in values


@pytest.mark.django_db
def test_the_session_salt_rotates_daily(api, doc, monkeypatch):
    from api import logging as apilog

    class _Req:
        META = {"REMOTE_ADDR": "203.0.113.9", "HTTP_USER_AGENT": "UA"}

    monkeypatch.setattr(apilog, "_today", lambda: dt.date(2026, 8, 18))
    a = apilog.session_hash(_Req())
    monkeypatch.setattr(apilog, "_today", lambda: dt.date(2026, 8, 19))
    b = apilog.session_hash(_Req())
    assert a != b


@pytest.mark.django_db
def test_a_logging_failure_never_fails_the_search(api, doc, monkeypatch):
    """Spec 16.3: logging must not add latency to or fail a search response."""
    from api import logging as apilog
    monkeypatch.setattr(apilog, "_write_query_log",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))
    r = api.get("/api/v1/search?q=iphone")
    assert r.status_code == 200


@pytest.mark.django_db
def test_click_endpoint_records_the_position(api, doc):
    from django.core.management import call_command
    call_command("reindex_vectors")
    qid = api.get("/api/v1/search?q=iphone").json()["query_id"]
    r = api.post("/api/v1/events/click",
                 data={"query_id": qid, "document_id": doc.id, "position": 3},
                 content_type="application/json")
    assert r.status_code == 202
    click = ClickLog.objects.get()
    assert click.position == 3
    assert click.document_id == doc.id


@pytest.mark.django_db
def test_a_click_on_an_unknown_query_is_accepted_and_dropped(api, doc):
    """A stale tab posting a click from yesterday's query must not 500."""
    r = api.post("/api/v1/events/click",
                 data={"query_id": 999999, "document_id": doc.id, "position": 1},
                 content_type="application/json")
    assert r.status_code == 202
    assert ClickLog.objects.count() == 0


@pytest.mark.django_db
def test_a_click_with_a_negative_position_is_rejected(api, doc):
    r = api.post("/api/v1/events/click",
                 data={"query_id": 1, "document_id": doc.id, "position": -1},
                 content_type="application/json")
    assert r.status_code in (400, 422)
```

`tests/search/test_log_partitions.py`:

```python
import pytest
from django.core.management import call_command
from django.db import connection


def _partitions(table):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = %s ORDER BY 1", [table])
        return [r[0] for r in cur.fetchall()]


@pytest.mark.django_db
def test_query_log_is_partitioned_by_month():
    """Spec 16.3: the fastest-growing table in the system must not share
    partition space or vacuum behaviour with SearchDocument."""
    assert _partitions("search_querylog")


@pytest.mark.django_db
def test_create_log_partitions_is_idempotent():
    call_command("create_log_partitions", "--months", "3")
    first = _partitions("search_querylog")
    call_command("create_log_partitions", "--months", "3")
    assert _partitions("search_querylog") == first


@pytest.mark.django_db
def test_create_log_partitions_creates_the_requested_horizon():
    call_command("create_log_partitions", "--months", "6")
    assert len(_partitions("search_querylog")) >= 6


@pytest.mark.django_db
def test_brin_index_exists_on_created_at():
    with connection.cursor() as cur:
        cur.execute("SELECT indexdef FROM pg_indexes "
                    "WHERE tablename LIKE 'search_querylog%%'")
        defs = " ".join(r[0] for r in cur.fetchall())
    assert "brin" in defs.lower()
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/api/test_logging.py -v`
Expected: FAIL — `ImportError: cannot import name 'QueryLog'`.

- [ ] **Step 3: Add the models**

In `search/models.py`:

```python
class QueryLog(models.Model):
    """Append-only search history. Spec 16.3.

    Month-partitioned with BRIN on created_at: this is the fastest-growing
    table in the system and it must not share vacuum behaviour with
    SearchDocument. Created by raw SQL, like SearchDocument -- Django tracks
    only the state.

    No user identity. session_hash is salted with a daily-rotating salt, which
    supports same-session analysis without building a durable per-person
    search history. Query text is the most sensitive data this system holds
    and a Dhivehi search log is a small-population, easily de-anonymised set.
    """

    q_raw = models.CharField(max_length=256)
    q_normalized = models.CharField(max_length=256, blank=True)
    detected_lang = models.CharField(max_length=8, blank=True)
    response_lang = models.CharField(max_length=8, blank=True)
    doc_type = models.CharField(max_length=32, blank=True)
    filters = models.JSONField(default=list, blank=True)
    result_count = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    session_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "search_querylog"
        managed = True


class ClickLog(models.Model):
    query = models.ForeignKey(QueryLog, on_delete=models.CASCADE,
                              related_name="clicks", db_constraint=False)
    # SearchDocument is partitioned, so a real FK constraint is not available.
    document = models.ForeignKey("search.SearchDocument", on_delete=models.DO_NOTHING,
                                 db_constraint=False, related_name="clicks")
    # Rank at click time. Impossible to reconstruct later; without it there is
    # no MRR, no nDCG and no usable ranking feature -- just a list of documents
    # someone once opened. Spec 16.3.
    position = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "search_clicklog"
        managed = True
```

- [ ] **Step 4: Write the partitioned-table migration**

`makemigrations search`, then replace the generated `CreateModel` for both with `SeparateDatabaseAndState`, exactly as P1 task 5 did for `SearchDocument`:

```python
CREATE_QUERYLOG = """
CREATE TABLE search_querylog (
    id             bigserial,
    q_raw          varchar(256) NOT NULL,
    q_normalized   varchar(256) NOT NULL DEFAULT '',
    detected_lang  varchar(8)   NOT NULL DEFAULT '',
    response_lang  varchar(8)   NOT NULL DEFAULT '',
    doc_type       varchar(32)  NOT NULL DEFAULT '',
    filters        jsonb        NOT NULL DEFAULT '[]'::jsonb,
    result_count   integer      NOT NULL DEFAULT 0,
    latency_ms     integer      NOT NULL DEFAULT 0,
    session_hash   varchar(64)  NOT NULL,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE search_clicklog (
    id          bigserial,
    query_id    bigint      NOT NULL,
    document_id bigint      NOT NULL,
    position    integer     NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- A default partition means an insert never fails because nobody ran the
-- monthly command. Rows land here and get moved when the real partition is
-- created; losing a click because of a missed cron is not acceptable.
CREATE TABLE search_querylog_default PARTITION OF search_querylog DEFAULT;
CREATE TABLE search_clicklog_default PARTITION OF search_clicklog DEFAULT;

-- BRIN, not btree: these tables are append-only and physically ordered by
-- created_at, which is exactly the access pattern BRIN is for, at roughly
-- 1/1000th the size.
CREATE INDEX querylog_created_brin ON search_querylog USING brin (created_at);
CREATE INDEX clicklog_created_brin ON search_clicklog USING brin (created_at);
CREATE INDEX querylog_zero_results ON search_querylog (created_at)
    WHERE result_count = 0;
CREATE INDEX clicklog_query ON search_clicklog (query_id);
"""
```

with `reverse_sql` dropping both tables. Then in the same migration, `RunPython` calling the partition creator for the current month plus three ahead.

- [ ] **Step 5: Write the partition command**

`search/management/commands/create_log_partitions.py`:

```python
"""Create month partitions ahead of time.

Run monthly. The DEFAULT partition means a missed run does not lose data, but
rows in the default partition cannot be dropped by the retention policy
without a rewrite, so do not rely on it.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import connection

TABLES = ("search_querylog", "search_clicklog")


def month_bounds(d: dt.date) -> tuple[dt.date, dt.date]:
    start = d.replace(day=1)
    end = (start + dt.timedelta(days=32)).replace(day=1)
    return start, end


def create_partitions(months: int = 3, today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    made: list[str] = []
    with connection.cursor() as cur:
        cursor_date = today.replace(day=1)
        for _ in range(months):
            start, end = month_bounds(cursor_date)
            suffix = start.strftime("%Y%m")
            for table in TABLES:
                name = f"{table}_{suffix}"
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "
                    f"FOR VALUES FROM (%s) TO (%s)", [start, end]
                )
                made.append(name)
            cursor_date = end
    return made


class Command(BaseCommand):
    help = "Create month partitions for the log tables."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=3)

    def handle(self, *args, **opts):
        made = create_partitions(opts["months"])
        self.stdout.write(self.style.SUCCESS(f"{len(made)} partitions ensured"))
```

`search/management/commands/prune_logs.py`:

```python
"""Drop log partitions older than the retention window.

Raw rows expire; aggregates do not. Query text is the most sensitive data this
system holds (spec 16.3), and dropping a partition is instant and leaves no
dead tuples, unlike a DELETE over millions of rows.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import connection

TABLES = ("search_querylog", "search_clicklog")


class Command(BaseCommand):
    help = "Drop log partitions older than --days."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        cutoff = (dt.date.today() - dt.timedelta(days=opts["days"])).replace(day=1)
        cutoff_suffix = cutoff.strftime("%Y%m")

        with connection.cursor() as cur:
            for table in TABLES:
                cur.execute(
                    "SELECT c.relname FROM pg_inherits i "
                    "JOIN pg_class c ON c.oid = i.inhrelid "
                    "JOIN pg_class p ON p.oid = i.inhparent "
                    "WHERE p.relname = %s", [table])
                for (name,) in cur.fetchall():
                    suffix = name.rsplit("_", 1)[-1]
                    if not suffix.isdigit() or suffix >= cutoff_suffix:
                        continue
                    self.stdout.write(f"dropping {name}")
                    if not opts["dry_run"]:
                        cur.execute(f"DROP TABLE {name}")
```

- [ ] **Step 6: Write the logger**

`api/logging.py`:

```python
"""Fire-and-forget logging. Spec 16.3.

Never on the hot path: the write goes to a small thread pool and every
exception is swallowed. A search response must not slow down for, or fail
because of, analytics.

The query id is needed in the response so a click can reference it, so the
QueryLog row is written synchronously and only the ClickLog write is deferred.
That single INSERT is measured in the P5 load check; if it shows up in p95,
move it to the pool and return a client-generated UUID instead.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import connection as db_connection

from search.models import ClickLog, QueryLog

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="searchlog")


def _today() -> dt.date:
    return dt.date.today()


def session_hash(request) -> str:
    """Salted, daily-rotating. Supports same-session analysis without building
    a durable per-person history. Neither the IP nor the user agent is stored.
    """
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() \
        or request.META.get("REMOTE_ADDR", "")
    ua = request.META.get("HTTP_USER_AGENT", "")
    material = f"{settings.SEARCH_LOG_SALT}:{_today().isoformat()}:{ip}:{ua}"
    return hashlib.sha256(material.encode()).hexdigest()


def _write_query_log(**kwargs) -> int:
    return QueryLog.objects.create(**kwargs).id


def log_query(request, *, raw, plan, doc_type, filters, result_count,
              latency_ms) -> int | None:
    if not settings.SEARCH_LOGGING_ENABLED:
        return None
    try:
        return _write_query_log(
            q_raw=(raw or "")[:256],
            q_normalized=(getattr(plan, "normalized", "") or "")[:256],
            detected_lang=getattr(plan, "detected_lang", ""),
            response_lang=getattr(plan, "response_lang", ""),
            doc_type=doc_type or "",
            filters=list(filters or []),
            result_count=result_count,
            latency_ms=latency_ms,
            session_hash=session_hash(request),
        )
    except Exception:
        logger.exception("query logging failed")
        return None


def _write_click(query_id: int, document_id: int, position: int) -> None:
    try:
        if not QueryLog.objects.filter(id=query_id).exists():
            return
        ClickLog.objects.create(query_id=query_id, document_id=document_id,
                                position=position)
    except Exception:
        logger.exception("click logging failed")
    finally:
        # Each pool thread owns its connection and must not leak it.
        db_connection.close()


def log_click(query_id: int, document_id: int, position: int) -> None:
    if not settings.SEARCH_LOGGING_ENABLED:
        return
    try:
        _POOL.submit(_write_click, query_id, document_id, position)
    except Exception:
        logger.exception("click dispatch failed")
```

In tests the pool makes assertions racy. Add to `settings.py`:

```python
SEARCH_LOGGING_ENABLED = os.getenv("SEARCH_LOGGING_ENABLED", "1") == "1"
SEARCH_LOG_SALT = os.getenv("SEARCH_LOG_SALT", DJANGO_SECRET_KEY)
# Synchronous in tests: a thread pool plus pytest-django's transactional
# rollback is a race, not a test.
SEARCH_LOGGING_SYNC = False
```

and in `log_click`, branch on `settings.SEARCH_LOGGING_SYNC` to call `_write_click` directly. Set `SEARCH_LOGGING_SYNC = True` in the pytest settings module.

- [ ] **Step 7: Write the events router**

`api/routers/events.py`:

```python
from ninja import Router
from ninja.errors import HttpError

from api.logging import log_click
from api.schemas import AcceptedOut, ClickIn

router = Router()


@router.post("/events/click", response={202: AcceptedOut})
def click(request, payload: ClickIn):
    if payload.position < 0:
        raise HttpError(400, "position must be >= 0")
    log_click(payload.query_id, payload.document_id, payload.position)
    return 202, {"status": "accepted"}
```

Register it, and replace the temporary `log_query` stub from task 4.

- [ ] **Step 8: Run the tests**

Run: `pytest tests/api/test_logging.py tests/search/test_log_partitions.py -v`
Expected: PASS.

- [ ] **Step 9: Add the ops note**

Append to `docs/superpowers/plans/README.md` under a new "Scheduled jobs" heading:

```markdown
## Scheduled jobs

| Command | Cadence | Why |
|---|---|---|
| `create_log_partitions --months 3` | monthly | Log tables are RANGE-partitioned; a missed run lands rows in DEFAULT, where retention cannot drop them cheaply. |
| `prune_logs --days 90` | monthly | Raw query text expires (spec 16.3). |
| `rebuild_suggest_terms` | after each full reindex | The term table is derived from titles. |
```

- [ ] **Step 10: Commit**

```bash
jj commit -m "P5 task 6: query and click logging, partitioned"
```

---

### Task 7: Document detail and the report endpoint

**Files:**
- Create: `api/routers/documents.py`, `api/ratelimit.py`
- Modify: `search/models.py` (add `DocumentReport`), `search/admin.py`, `api/urls.py`
- Test: `tests/api/test_documents.py`, `tests/api/test_report.py`

**Interfaces:**
- Consumes: `SearchDocument`, `enrich.models.EnrichedRecord`, `gazette.models.Attachment`.
- Produces: `DocumentReport` model, `GET /api/v1/documents/{id}`, `POST /api/v1/documents/{id}/report`, `check_rate(key, limit, window) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/api/test_documents.py`:

```python
import pytest

from search.models import SearchDocument


@pytest.fixture
def docs(db, sources):
    SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        title_en="iPhone 13", summary_en="A used iPhone.",
        attrs={"brand": "Apple", "specs": [{"key_raw": "storage",
                                            "value_num": 128, "unit": "GB"}]},
        card={"source": "ibay", "title": "iPhone 13"},
        thumbnails=["https://x/1.jpg", "https://x/2.jpg"],
    )
    SearchDocument.objects.create(
        source="gazette", source_key="IUL-1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1", title_en="Tender",
        card={"source": "gazette"},
    )


@pytest.mark.django_db
def test_detail_returns_attrs_thumbnails_and_source(api, docs):
    d = SearchDocument.objects.get(source_key="1")
    body = api.get(f"/api/v1/documents/{d.id}").json()
    assert body["source"] == "ibay"
    assert body["attrs"]["brand"] == "Apple"
    assert body["thumbnails"] == ["https://x/1.jpg", "https://x/2.jpg"]
    assert body["url"] == "https://x/1"


@pytest.mark.django_db
def test_detail_404s_for_news(api, docs):
    """Spec 8.5: /documents/{id} serves shopping, jobs and property only.
    News links out; building an internal reader for content we do not own is
    work that helps nobody."""
    d = SearchDocument.objects.get(source_key="IUL-1")
    assert api.get(f"/api/v1/documents/{d.id}").status_code == 404


@pytest.mark.django_db
def test_detail_404s_for_a_missing_id(api, docs):
    assert api.get("/api/v1/documents/999999").status_code == 404


@pytest.mark.django_db
def test_detail_carries_the_full_spec_table_including_non_facetable_keys(api, docs):
    d = SearchDocument.objects.get(source_key="1")
    body = api.get(f"/api/v1/documents/{d.id}").json()
    assert body["specs"] == [{"key_raw": "storage", "value_num": 128,
                              "value_text": "", "unit": "GB"}]


@pytest.mark.django_db
def test_detail_computes_deadline_state_rather_than_reading_it(api, docs):
    d = SearchDocument.objects.get(source_key="1")
    d.doc_type = "job"
    d.card = {"source": "ibay", "deadline": "2020-01-01"}
    d.save()
    body = api.get(f"/api/v1/documents/{d.id}").json()
    assert body["card"]["deadline_state"] == "closed"
```

`tests/api/test_report.py`:

```python
import pytest

from search.models import DocumentReport, SearchDocument


@pytest.fixture
def doc(db):
    return SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                         doc_type="news", url="https://x")


def _post(api, doc_id, reason="stale", note="", ip="203.0.113.1"):
    return api.post(f"/api/v1/documents/{doc_id}/report",
                    data={"reason": reason, "note": note},
                    content_type="application/json", REMOTE_ADDR=ip)


@pytest.mark.django_db
def test_a_report_is_accepted_and_queued(api, doc):
    assert _post(api, doc.id).status_code == 202
    r = DocumentReport.objects.get()
    assert r.reason == "stale"
    assert r.status == "open"
    assert r.reporter_ip_hash and "203.0.113.1" not in r.reporter_ip_hash


@pytest.mark.django_db
def test_a_report_never_marks_the_document_stale(api, doc):
    """The endpoint is public and reprocessing costs real money per document,
    so auto-reprocessing would be a billable denial-of-wallet vector. Reports
    are inert data; an admin action re-queues. Spec 5.7."""
    _post(api, doc.id)
    doc.refresh_from_db()
    assert doc.stale_marked_at is None


@pytest.mark.django_db
def test_a_duplicate_report_returns_202_and_creates_nothing(api, doc):
    """Always 202, new or duplicate: telling a caller which documents they
    have already reported leaks nothing useful and invites probing. Spec 9."""
    _post(api, doc.id)
    assert _post(api, doc.id).status_code == 202
    assert DocumentReport.objects.count() == 1


@pytest.mark.django_db
def test_a_different_reason_from_the_same_reporter_is_a_new_report(api, doc):
    _post(api, doc.id, reason="stale")
    _post(api, doc.id, reason="dead_link")
    assert DocumentReport.objects.count() == 2


@pytest.mark.django_db
def test_reports_are_rate_limited_per_ip(api, doc, settings):
    settings.REPORT_RATE_LIMIT = 3
    for i in range(3):
        d = SearchDocument.objects.create(source="ibay", source_key=f"r{i}",
                                          doc_type="shopping", url="https://x")
        assert _post(api, d.id).status_code == 202
    d = SearchDocument.objects.create(source="ibay", source_key="over",
                                      doc_type="shopping", url="https://x")
    r = _post(api, d.id)
    assert r.status_code == 202                 # still 202, deliberately
    assert not DocumentReport.objects.filter(document_id=d.id).exists()


@pytest.mark.django_db
def test_a_report_on_a_missing_document_is_202_and_creates_nothing(api, doc):
    assert _post(api, 999999).status_code == 202
    assert DocumentReport.objects.count() == 0


@pytest.mark.django_db
def test_an_invalid_reason_is_rejected(api, doc):
    r = api.post(f"/api/v1/documents/{doc.id}/report",
                 data={"reason": "i just dont like it"},
                 content_type="application/json")
    assert r.status_code == 422


@pytest.mark.django_db
def test_the_note_is_truncated_not_rejected(api, doc):
    _post(api, doc.id, note="x" * 10_000)
    assert len(DocumentReport.objects.get().note) <= 2000
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/api/test_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'DocumentReport'`.

- [ ] **Step 3: Add the model**

In `search/models.py`:

```python
class DocumentReport(models.Model):
    """User-reported staleness. Spec 5.7.

    Inert data by design. A report must never trigger reprocessing on its own:
    the endpoint is public and transcription plus enrichment cost real money
    per document, so auto-reprocessing would let anyone loop the endpoint and
    spend the API budget. The admin queue sorts by report count so genuinely
    broken records surface first, and a human action is what re-queues.
    """

    REASONS = [("stale", "stale"), ("wrong_details", "wrong details"),
               ("dead_link", "dead link"), ("spam", "spam"), ("other", "other")]
    STATUSES = [("open", "open"), ("actioned", "actioned"), ("rejected", "rejected")]

    # SearchDocument is partitioned; a real FK constraint is unavailable.
    document = models.ForeignKey("search.SearchDocument", on_delete=models.DO_NOTHING,
                                 db_constraint=False, related_name="reports")
    reason = models.CharField(max_length=24, choices=REASONS)
    note = models.TextField(blank=True)
    reporter_ip_hash = models.CharField(max_length=64)   # rate limiting only
    status = models.CharField(max_length=16, choices=STATUSES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "reason", "reporter_ip_hash"],
                name="uniq_report_per_reporter_reason",
            )
        ]
        indexes = [models.Index(fields=["status", "-created_at"],
                                name="report_status_created")]
```

Migration: `python manage.py makemigrations search && python manage.py migrate`.

- [ ] **Step 4: Write the rate limiter**

`api/ratelimit.py`:

```python
"""Rate limiting without new infrastructure.

Counted against the reports table itself rather than a cache backend: the
production stack has three gunicorn workers and LocMemCache is per-process, so
an in-memory limiter would grant 3x the intended budget. Adding Redis for one
counter is not worth an extra service; this is one indexed COUNT over a small
table.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.utils import timezone


def report_quota_exceeded(ip_hash: str) -> bool:
    from search.models import DocumentReport

    window = timezone.now() - dt.timedelta(seconds=settings.REPORT_RATE_WINDOW)
    used = DocumentReport.objects.filter(
        reporter_ip_hash=ip_hash, created_at__gte=window
    ).count()
    return used >= settings.REPORT_RATE_LIMIT
```

Settings:

```python
REPORT_RATE_LIMIT = int(os.getenv("REPORT_RATE_LIMIT", "20"))
REPORT_RATE_WINDOW = int(os.getenv("REPORT_RATE_WINDOW", "3600"))
```

- [ ] **Step 5: Write the router**

`api/routers/documents.py`:

```python
"""Detail and report. Spec 8.5, 9, 5.7."""

from __future__ import annotations

from django.db import IntegrityError
from ninja import Router
from ninja.errors import HttpError

from api.logging import session_hash
from api.ratelimit import report_quota_exceeded
from api.routers.search import annotate_time
from api.schemas import AcceptedOut, ReportIn
from search.models import DocumentReport, SearchDocument

router = Router()

# News has no detail page: a news result links straight to the source article.
DETAIL_TYPES = {"shopping", "job", "property"}
MAX_NOTE = 2000


@router.get("/documents/{int:doc_id}")
def detail(request, doc_id: int):
    doc = SearchDocument.objects.filter(id=doc_id).first()
    if doc is None or doc.doc_type not in DETAIL_TYPES:
        raise HttpError(404, "not found")

    specs = [
        {"key_raw": s.get("key_raw", ""), "value_num": s.get("value_num"),
         "value_text": s.get("value_text", ""), "unit": s.get("unit", "")}
        for s in (doc.attrs.get("specs") or [])
    ]

    return {
        "id": doc.id,
        "source": doc.source,
        "source_key": doc.source_key,
        "doc_type": doc.doc_type,
        "url": doc.url,
        "title_en": doc.title_en,
        "title_dv": doc.title_dv,
        "summary_en": doc.summary_en,
        "summary_dv": doc.summary_dv,
        "price": float(doc.price) if doc.price is not None else None,
        "currency": doc.currency,
        "location": doc.location,
        "island": doc.island,
        "atoll": doc.atoll,
        "published_at": doc.published_at,
        "expires_at": doc.expires_at,
        "attrs": doc.attrs,
        "specs": specs,
        "card": annotate_time(doc.card, doc.doc_type),
        "thumbnails": doc.thumbnails,
    }


@router.post("/documents/{int:doc_id}/report", response={202: AcceptedOut})
def report(request, doc_id: int, payload: ReportIn):
    """Always 202. Never reprocesses. Spec 5.7, 9."""
    ip_hash = session_hash(request)

    if report_quota_exceeded(ip_hash):
        return 202, {"status": "accepted"}
    if not SearchDocument.objects.filter(id=doc_id).exists():
        return 202, {"status": "accepted"}

    try:
        DocumentReport.objects.create(
            document_id=doc_id,
            reason=payload.reason,
            note=(payload.note or "")[:MAX_NOTE],
            reporter_ip_hash=ip_hash,
        )
    except IntegrityError:
        pass                     # duplicate; the caller learns nothing either way

    return 202, {"status": "accepted"}
```

- [ ] **Step 6: Add the admin queue**

In `search/admin.py`:

```python
from django.contrib import admin
from django.db.models import Count
from django.utils import timezone

from search.models import DocumentReport


@admin.register(DocumentReport)
class DocumentReportAdmin(admin.ModelAdmin):
    list_display = ("document_id", "reason", "status", "created_at")
    list_filter = ("status", "reason")
    actions = ("mark_stale_and_action", "reject")

    def get_queryset(self, request):
        # Sorted by report count so genuinely broken records surface first.
        qs = super().get_queryset(request)
        return qs.annotate(
            sibling_count=Count("document__reports")
        ).order_by("status", "-sibling_count", "-created_at")

    @admin.action(description="Mark document stale and action report (SPENDS MONEY)")
    def mark_stale_and_action(self, request, queryset):
        from search.models import SearchDocument
        ids = list(queryset.values_list("document_id", flat=True))
        SearchDocument.objects.filter(id__in=ids).update(
            stale_marked_at=timezone.now()
        )
        queryset.update(status="actioned")
        self.message_user(
            request,
            f"{len(ids)} documents marked stale. Run extract_attachments --stale, "
            f"enrich_documents --stale, then reindex --stale.",
        )

    @admin.action(description="Reject")
    def reject(self, request, queryset):
        queryset.update(status="rejected")
```

The action name says it spends money on purpose. This is the only public-facing path to a billable operation and the confirmation should read like one.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/api/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
jj commit -m "P5 task 7: document detail, report endpoint, admin queue"
```

---

### Task 8: Measure and record

**Files:**
- Create: `docs/superpowers/measurements/2026-08-p5-api.md`
- Test: `tests/api/test_latency.py`

**Interfaces:**
- Produces: a p50/p95 table and a facet-cost breakdown that P6 and P7 read. Spec 16.4's Meilisearch re-entry condition is decided by these numbers.

- [ ] **Step 1: Write the latency test**

`tests/api/test_latency.py`:

```python
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
```

Mark it slow and exclude it from the default run: add `markers = slow: long-running` to `pytest.ini` and run it with `pytest -m slow`.

- [ ] **Step 2: Run it and record the numbers**

Run: `pytest tests/api/test_latency.py -m slow -v -s`

- [ ] **Step 3: Measure the facet cost separately**

The result page and the facet block are separate statements, so measure them separately — if facets dominate, P7's dynamic discovery has a budget problem before it is written.

```python
# scratch, not committed
from search.query import search_page, compute_facets
# time search_page with facets, then a variant that skips compute_facets
```

- [ ] **Step 4: Write the measurements file**

`docs/superpowers/measurements/2026-08-p5-api.md`:

```markdown
# P5 API, measured

Date: <fill>
Corpus size at measurement: <fill> documents

## Latency

| Endpoint | p50 | p95 | Notes |
|---|---|---|---|
| GET /search (all, no facets) | | | |
| GET /search (shopping, 7 facets) | | | |
| GET /search (job, 8 facets) | | | |
| GET /search (property, 14 facets) | | | property has the most facets |
| GET /suggest | | | |
| GET /documents/{id} | | | |
| POST /documents/{id}/report | | | includes the rate-limit COUNT |

Budget: p95 under 400ms. Spec 16.4's Meilisearch re-entry condition is this
table missing target, or facet discovery proving too slow at scale.

## Facet cost

| doc_type | facets returned | statements | facet ms | % of request |
|---|---|---|---|---|

If facets are more than half the request, the N-statements-over-one-CTE shape
in `compute_facets` is the thing to change, not the CTE.

## Logging overhead

| | p95 with SEARCH_LOGGING_ENABLED=1 | =0 | delta |
|---|---|---|---|

The QueryLog INSERT is synchronous because the response carries `query_id`.
If the delta is material, switch to a client-generated id and defer the write.

## Zero-result queries, first week

    SELECT q_raw, count(*) FROM search_querylog
    WHERE result_count = 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 50;

The highest-signal input for curating QueryAlias rows and finding gaps in the
transliteration table (spec 16.3). Feed the top 50 into P8's eval set.

## Decisions this changes

- [ ] Does p95 clear 400ms with the property facet set? If not, cap facet count.
- [ ] Does the report rate-limit COUNT show up at all? If yes, add a partial index.
- [ ] Meilisearch re-entry (16.4): triggered or not?
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "P5 task 8: latency measured and recorded"
```

---

## Self-Review

**Spec coverage.** 7 → task 3 (candidate CTE reuse, sorts, facets over the same set). 8 tabs → task 1's `TABS` and task 4's tab mapping; the three-consecutive cap → task 3's `interleave`. 8.1-8.4 facet sets → task 2's registry, each asserted against the spec's list. 8.5 source attribution → `_SOURCE` on every type plus the icon-path test. 9 → tasks 1, 4, 5, 7. 12.3 → task 8's flat-latency assertion. 16.3 → task 6 in full, including `position`, the daily salt, BRIN, month partitions and expiry. 5.7's report rules → task 7.

**Known gaps, deliberate.** Zero-result progressive relaxation (spec 7's last bullet) is P8 — it wants the zero-result query list that task 8 starts collecting, and writing the relaxation ladder before seeing which queries actually fail would be guessing. Dynamic shopping facets are P7 and append to the same ordered list. `QueryAlias` curation is P8.

**Type consistency checked.** `search_page` returns `SearchPage(results, total, facets, plan)` and task 4 destructures exactly those. `parse_filters(raw, doc_type)` and `filter_sql(filters)` are called with the same argument order in task 3 and task 4. `annotate_time(card, doc_type)` is imported by `documents.py` from `search.py` — one implementation, so a job card gets the same treatment on the detail page as in the result list. `session_hash(request)` is used for both the log and the report hash, deliberately: the same daily-rotating salt, so a reporter cannot be correlated across days either.

**The one thing to watch.** Task 3 adds `thumbnails` and `price` to the page SQL's select list. `_to_result` is shared with `search()`, so the column count changed for both callers — if `search()` starts raising a tuple-unpacking error, that is why, and the fix is to unpack by index rather than by position-sensitive destructuring.
