"""Source adapter contract. Spec 3.1.

Every adapter implements BOTH directions. `fetch_raw` is the half that makes
reprocessing possible: without it, adding a document type later degrades from
a re-run into a re-scrape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass(slots=True)
class RawDocument:
    """Whatever the source app holds for one entity, unprocessed."""

    source: str
    source_key: str
    payload: dict[str, Any]


@dataclass(slots=True)
class DocumentDraft:
    """A source-agnostic description of one searchable entity.

    Note there is no body-text field beyond `text_en`, which is consumed to
    build a tsvector and then discarded -- SearchDocument never stores it
    (spec 12.1).
    """

    source: str
    source_key: str
    doc_type: str
    url: str

    title_en: str = ""
    title_dv: str = ""
    title_latin: str = ""
    summary_en: str = ""
    summary_dv: str = ""

    # Consumed by the indexer to build vectors, never persisted.
    text_en: str = ""
    text_dv: str = ""
    text_latin: str = ""

    price: Decimal | None = None
    currency: str = "MVR"
    location: str = ""
    island: str = ""
    atoll: str = ""
    published_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True

    attrs: dict[str, Any] = field(default_factory=dict)
    card: dict[str, Any] = field(default_factory=dict)
    thumbnails: list[str] = field(default_factory=list)
    quality: float = 0.0
    content_hash: str = ""


@runtime_checkable
class SourceAdapter(Protocol):
    key: str

    def iter_source_keys(self, **filters: Any) -> Iterator[str]: ...

    def fetch_raw(self, source_key: str) -> RawDocument | None: ...

    def to_document(self, raw: RawDocument) -> DocumentDraft | None: ...


_REGISTRY: dict[str, SourceAdapter] = {}


def register(adapter: SourceAdapter) -> SourceAdapter:
    if adapter.key in _REGISTRY:
        raise ValueError(f"adapter already registered for source {adapter.key!r}")
    _REGISTRY[adapter.key] = adapter
    return adapter


def get_adapter(key: str) -> SourceAdapter:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no adapter registered for source {key!r}; "
            f"known: {sorted(_REGISTRY)}"
        ) from None


def all_adapters() -> list[SourceAdapter]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
