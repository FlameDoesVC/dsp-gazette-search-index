"""Detail and report. Spec 8.5, 9, 5.7."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from ninja import Router
from ninja.errors import HttpError

from api.logging import session_hash
from api.ratelimit import report_quota_exceeded
from api.routers.search import annotate_time
from api.schemas import AcceptedOut, ReportIn
from search.models import DocumentReport, SearchDocument
from search.vocab import annotate_free_text, annotate_labels

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
         "value_text": s.get("value_text", ""), "unit": s.get("unit", ""),
         "provenance": s.get("provenance", "")}
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
        "card": annotate_free_text(
            doc.doc_type, annotate_labels(doc.doc_type, annotate_time(doc.card, doc.doc_type))),
        "thumbnails": doc.thumbnails,
        "entity_id": doc.attrs.get("entity_id"),
        "profile_tier": doc.attrs.get("profile_tier", ""),
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
        # A nested atomic isolates the duplicate so the IntegrityError cannot
        # poison the request's transaction.
        with transaction.atomic():
            DocumentReport.objects.create(
                document_id=doc_id,
                reason=payload.reason,
                note=(payload.note or "")[:MAX_NOTE],
                reporter_ip_hash=ip_hash,
            )
    except IntegrityError:
        pass                     # duplicate; the caller learns nothing either way

    return 202, {"status": "accepted"}
