"""Request and response models. Spec 9.

django-ninja generates the OpenAPI document from these and the frontend
generates its TypeScript from that, so a name here is a name in the browser.
Rename deliberately.
"""

from __future__ import annotations

from typing import Any, Literal

from ninja import Schema
from pydantic import ConfigDict, Field


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
    from_: float = Field(alias="from")
    to: float
    count: int

    # `from` is a Python keyword; the alias is the wire name the frontend
    # sees. populate_by_name keeps construction usable with `from_`.
    model_config = ConfigDict(populate_by_name=True)


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
    has_inferred: bool = False


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
    profile_tier: str = ""


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
