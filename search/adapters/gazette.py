"""Gazette adapter. P1 maps scraped fields only.

Bodies are raw HTML (spec 5.6), so markup is stripped before it can reach a
tsvector. The salary-table parsing that exploits the table structure arrives
in P3 alongside attachments.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterator

from lxml import html as lxml_html

from gazette.models import Iulaan
from search.adapters.base import DocumentDraft, RawDocument

# Spec 5.3 classification priors. Anything absent from this table becomes
# news -- there is deliberately no `unknown` bucket.
IULAAN_TYPE_DOC_TYPE = {
    "ވަޒީފާގެ ފުރުޞަތު": "job",
    "Job Opportunity": "job",
    "ކުއްޔަށް ދިނުން": "property",
    "ކުއްޔަށް ހިފުން": "property",
}
_DEFAULT_DOC_TYPE = "news"

_WS = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    """Return visible text only. Gazette bodies are Word-exported HTML tables;
    indexing `td`/`valign`/`strong` as lexemes would poison the vocabulary."""
    if not raw or not raw.strip():
        return ""
    try:
        text = lxml_html.fromstring(raw).text_content()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    return _WS.sub(" ", text).strip()


def _summarize(text: str, limit: int = 240) -> str:
    text = _WS.sub(" ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


class GazetteAdapter:
    key = "gazette"

    def iter_source_keys(self, **filters: Any) -> Iterator[str]:
        qs = Iulaan.objects.all()
        if doc_ids := filters.get("ids"):
            qs = qs.filter(id__in=doc_ids)
        for pk in qs.values_list("id", flat=True).iterator(chunk_size=500):
            yield str(pk)

    def fetch_raw(self, source_key: str) -> RawDocument | None:
        try:
            iulaan = Iulaan.objects.select_related("office", "iulaan_type").get(
                id=source_key
            )
        except Iulaan.DoesNotExist:
            return None
        return RawDocument(
            source=self.key,
            source_key=source_key,
            payload={"iulaan": iulaan},
        )

    def to_document(self, raw: RawDocument) -> DocumentDraft | None:
        i: Iulaan = raw.payload["iulaan"]

        type_name = i.iulaan_type.name if i.iulaan_type else ""
        doc_type = IULAAN_TYPE_DOC_TYPE.get(type_name, _DEFAULT_DOC_TYPE)

        body_dv = strip_html(i.body)
        body_en = _WS.sub(" ", i.translated_body or "").strip()
        office_en = (i.office.translated_name or i.office.name) if i.office else ""
        office_dv = i.office.name if i.office else ""

        text_dv = f"{i.title} {office_dv} {type_name} {body_dv}".strip()
        text_en = f"{i.translated_title} {office_en} {body_en}".strip()

        return DocumentDraft(
            source=self.key,
            source_key=str(i.id),
            doc_type=doc_type,
            url=i.url,
            title_dv=i.title or "",
            title_en=i.translated_title or "",
            summary_dv=_summarize(body_dv),
            summary_en=_summarize(body_en),
            text_dv=text_dv,
            text_en=text_en,
            attrs={
                "office": office_en,
                "office_dv": office_dv,
                "announcement_type": type_name,
                "additional_info": i.additional_info or {},
                "attachment_count": len(i.attachments or {}),
            },
            card={
                "source": self.key,
                "title": i.translated_title or i.title,
                "office": office_en,
                "announcement_type": type_name,
                "external_url": i.url,
                "attachment_count": len(i.attachments or {}),
            },
            quality=_quality(body_dv, i),
            content_hash=hashlib.sha256(
                f"{i.title}{i.body}".encode()
            ).hexdigest(),
        )


def _quality(body_dv: str, iulaan: Iulaan) -> float:
    score = 0.0
    score += 0.4 if len(body_dv) >= 500 else 0.1
    score += 0.2 if iulaan.translated_title else 0.0
    score += 0.2 if iulaan.office_id else 0.0
    score += 0.2 if iulaan.attachments else 0.0
    return round(score, 3)
