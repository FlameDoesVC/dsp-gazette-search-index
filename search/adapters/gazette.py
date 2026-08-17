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
from search.extract.tables import parse_label_value_pairs
from search.lang import translit_dv_to_latin

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
            payload={
                "iulaan": iulaan,
                "attachments": list(
                    iulaan.attachment_files.filter(status="ok")
                    .exclude(role="application_form")
                    .exclude(text="")
                ),
            },
        )

    def to_document(self, raw: RawDocument) -> DocumentDraft | None:
        i: Iulaan = raw.payload["iulaan"]

        type_name = i.iulaan_type.name if i.iulaan_type else ""
        doc_type = IULAAN_TYPE_DOC_TYPE.get(type_name, _DEFAULT_DOC_TYPE)

        body_dv = strip_html(i.body)
        body_en = _WS.sub(" ", i.translated_body or "").strip()
        office_en = (i.office.translated_name or i.office.name) if i.office else ""
        office_dv = i.office.name if i.office else ""

        attachments = raw.payload.get("attachments", [])
        attachment_text = "\n".join(a.text for a in attachments)
        transcribed = any(a.transcribed for a in attachments)

        # Table structure the source already provides (spec 5.2). Parsed here
        # so P4's extraction receives labelled pairs, not markup.
        pairs = parse_label_value_pairs(i.body)
        pair_text = "\n".join(f"{label}: {value}" for label, value in pairs)

        text_dv = " ".join(
            part for part in
            (i.title, office_dv, type_name, body_dv, pair_text, attachment_text)
            if part
        ).strip()
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
            title_latin=translit_dv_to_latin(i.title or ""),
            text_latin=translit_dv_to_latin(text_dv),
            attrs={
                "office": office_en,
                "office_dv": office_dv,
                "announcement_type": type_name,
                "additional_info": i.additional_info or {},
                "attachment_count": len(i.attachments or {}),
                "table_pairs": pairs,
                "transcribed": transcribed,
            },
            card={
                "source": self.key,
                "title": i.translated_title or i.title,
                "office": office_en,
                "announcement_type": type_name,
                "external_url": i.url,
                "attachment_count": len(i.attachments or {}),
                "detail_source": "attachment" if attachment_text else "listing",
                "transcribed": transcribed,
            },
            quality=_quality(body_dv, i, attachments, transcribed),
            content_hash=hashlib.sha256(
                "".join(
                    [i.title or "", i.body or ""]
                    + [a.content_sha or a.text[:64] for a in attachments]
                ).encode()
            ).hexdigest(),
        )


def _quality(body_dv: str, iulaan: Iulaan, attachments=(), transcribed=False) -> float:
    score = 0.0
    score += 0.3 if len(body_dv) >= 500 else 0.1
    score += 0.2 if iulaan.translated_title else 0.0
    score += 0.15 if iulaan.office_id else 0.0
    score += 0.2 if attachments else 0.0
    score += 0.15 if iulaan.attachments else 0.0
    # Transcribed text is lower-trust than a clean text layer (spec 5.6.1).
    if transcribed:
        score *= 0.8
    return round(min(score, 1.0), 3)
