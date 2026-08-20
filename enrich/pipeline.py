"""Orchestration. Spec 5.4, 5.7.

Async with its own semaphore, separate from translation's, so the two
workloads never contend for the GPU or for rate limit headroom.

Idempotent and resumable: per-record try/except, an attempts counter, and a
selection query that skips anything already enriched at the current
content_hash and prompt_version.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Iterator

from asgiref.sync import sync_to_async
from django.conf import settings

from enrich.client import EnrichClient, ProviderError
from enrich.models import EnrichedRecord
from enrich.preextract import Candidates, extract_candidates
from enrich.prior import apply_confidence_gate, prior_for
from enrich.prompts import PROMPT_VERSION, build_messages
from enrich.schemas import EnrichmentOutput
from enrich.validate import ground
from search.adapters import base as adapters
from search.models import SearchDocument

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# The order the cold pass runs in. Jobs and news first: 641 documents, minutes
# and cents, and the typed facets with the most to gain. Shopping last: the
# corpus that costs the most. Spec 5.4.
COLD_PASS_ORDER = ("job", "news", "property", "shopping")


@dataclass(slots=True)
class EnrichInput:
    source: str
    source_key: str
    doc_type_prior: str
    title: str
    body: str
    scraped: dict
    candidates: Candidates
    content_hash: str


def _gazette_scraped(iulaan) -> dict:
    from search.extract.dates import parse_dv_datetime

    info = iulaan.additional_info or {}
    deadline = parse_dv_datetime(info.get("ސުންގަޑި", ""))
    return {
        "office": iulaan.office.name if iulaan.office else "",
        "announcement_type": iulaan.iulaan_type.name if iulaan.iulaan_type else "",
        "reference_no": info.get("ނަންބަރު", ""),
        # Scraped fields win. The model may fill a null, never overwrite.
        "deadline": deadline.date().isoformat() if deadline else "",
    }


def _ibay_scraped(payload) -> dict:
    info = payload["info"]
    p = payload["product"]
    out = {
        "employer": info.get("Employer", ""),
        "position_type": info.get("Position Type", ""),
        "job_category": info.get("Job Category", ""),
        "condition": info.get("Item Condition", ""),
        "brand": info.get("Brand", ""),
        "neighborhood": info.get("Neighborhood", ""),
        "furnishing": info.get("Furnishing", ""),
        "floor": info.get("Floor", ""),
    }
    if p.product_location:
        out["location"] = p.product_location
    return {k: v for k, v in out.items() if v}


def build_input(source: str, source_key: str) -> EnrichInput | None:
    """Assemble everything the model call needs, from the adapter's raw payload.

    Reads through the adapter rather than the ORM directly so a new source
    needs no change here -- the only source-specific parts are the two
    `scraped` mappers and the prior.
    """
    adapter = adapters.get_adapter(source)
    raw = adapter.fetch_raw(source_key)
    if raw is None:
        return None
    draft = adapter.to_document(raw)
    if draft is None:
        return None

    if source == "gazette":
        iulaan = raw.payload["iulaan"]
        prior = prior_for("gazette",
                          iulaan_type=iulaan.iulaan_type.name if iulaan.iulaan_type else "")
        scraped = _gazette_scraped(iulaan)
        title = iulaan.title
    elif source == "ibay":
        prior = prior_for("ibay", categories=raw.payload["categories"])
        scraped = _ibay_scraped(raw.payload)
        title = raw.payload["product"].name
    else:
        prior = prior_for(source)
        scraped = {}
        title = draft.title_en or draft.title_dv

    # draft.text_* is what the adapter fed to the vectors: the body plus, from
    # P3, any attachment text. That is the exact text the model must see, and
    # the exact text the hash must cover.
    body = "\n".join(t for t in (draft.text_en, draft.text_dv) if t)
    body = body[: settings.ENRICH_MAX_INPUT_CHARS]

    return EnrichInput(
        source=source,
        source_key=source_key,
        doc_type_prior=prior,
        title=title,
        body=body,
        scraped=scraped,
        candidates=extract_candidates(f"{title}\n{body}"),
        content_hash=draft.content_hash
        or hashlib.sha256(body.encode()).hexdigest(),
    )


async def enrich_one(inp: EnrichInput, client) -> EnrichedRecord:
    """One document, one record. Never raises: a failure is a stored status."""
    def _messages(schema_for=None, repair_error=None):
        return build_messages(
            source=inp.source,
            doc_type_prior=inp.doc_type_prior,
            title=inp.title,
            body=inp.body,
            candidates=inp.candidates,
            scraped=inp.scraped,
            schema_for=schema_for,
            repair_error=repair_error,
        )

    record, _ = await sync_to_async(EnrichedRecord.objects.get_or_create)(
        source=inp.source, source_key=inp.source_key,
        defaults={"content_hash": inp.content_hash,
                  "doc_type": inp.doc_type_prior},
    )
    record.attempts += 1
    record.content_hash = inp.content_hash
    record.prompt_version = PROMPT_VERSION

    try:
        payload, model_name = await client.run_chain(
            _messages(), rebuild=lambda err: _messages(repair_error=err)
        )
    except ProviderError as exc:
        record.status = "failed"
        record.error = str(exc)[:2000]
        # The prior is still a usable classification, so the document indexes
        # with scraped data and the rule-based type. Indexing never blocks on
        # enrichment (spec 5.2).
        record.doc_type = inp.doc_type_prior
        await sync_to_async(record.save)()
        return record

    out = EnrichmentOutput(**payload) if isinstance(payload, dict) else EnrichmentOutput()
    doc_type, overridden = apply_confidence_gate(
        inp.doc_type_prior, out.doc_type, out.doc_type_confidence
    )

    if overridden:
        # The first call carried the PRIOR's schema, so whatever attrs came back
        # describe the wrong shape. Ask once more with the right schema. Measured
        # on the first 31 iBay shopping documents: the model agreed with the
        # prior 31 times out of 31, so this path is rare and the 61% saved on
        # every other call pays for it many times over.
        try:
            payload, model_name = await client.run_chain(
                _messages(schema_for=doc_type),
                rebuild=lambda err: _messages(schema_for=doc_type,
                                              repair_error=err),
            )
        except ProviderError as exc:
            # The classification still stands; only the extraction is missing.
            record.status = "failed"
            record.error = str(exc)[:2000]
            record.doc_type = doc_type
            await sync_to_async(record.save)()
            return record
        reclassified = (EnrichmentOutput(**payload) if isinstance(payload, dict)
                        else EnrichmentOutput())
        # Keep the type the gate accepted. A second disagreement does not get a
        # third call, or a document the model cannot place would loop.
        reclassified.doc_type = doc_type
        reclassified.doc_type_confidence = out.doc_type_confidence
        out = reclassified

    attrs_model, report = ground(
        out.attrs,
        doc_type=doc_type,
        source_text=f"{inp.title}\n{inp.body}",
        candidates=inp.candidates,
        scraped=inp.scraped,
    )

    record.doc_type = doc_type
    record.doc_type_confidence = out.doc_type_confidence
    record.canonical_title_en = out.canonical_title_en[:512]
    record.canonical_title_dv = out.canonical_title_dv[:512]
    record.summary_en = out.summary_en[:240]
    record.summary_dv = out.summary_dv[:240]
    record.attrs = attrs_model.model_dump()
    record.keywords = out.keywords[:20]
    record.model_name = model_name
    record.validation = report
    record.status = "needs_review" if report["needs_review"] else "ok"
    record.error = ""
    await sync_to_async(record.save)()
    return record


def select_keys(
    *,
    source: str,
    prompt_version: int,
    doc_type: str | None = None,
    only_stale: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> Iterator[tuple[str, str]]:
    """Which documents need the model. Spec 4.2, 5.7.

    Four gates, in order of precedence:
      1. stale_marked_at set  -> always, overriding everything
      2. --force              -> always
      3. content_hash changed -> always
      4. prompt_version bumped-> iBay only. Gazette is write-once (5.7), so a
         prompt improvement reaches only newly-ingested iulaan. Without this
         the next PROMPT_VERSION bump silently re-bills 51,000 documents.
    """
    qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS).filter(source=source)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)
    if only_stale:
        qs = qs.filter(stale_marked_at__isnull=False)

    records = EnrichedRecord.objects.using(settings.STREAM_DB_ALIAS).filter(
        source=source
    ).only("source_key", "content_hash", "prompt_version", "status", "attempts")
    existing = {r.source_key: r for r in records}

    yielded = 0
    for doc in qs.only("source_key", "content_hash", "stale_marked_at").iterator(
        chunk_size=500
    ):
        if limit is not None and yielded >= limit:
            return

        rec = existing.get(doc.source_key)
        wanted = False

        if doc.stale_marked_at is not None or force or rec is None:
            wanted = True
        elif rec.content_hash != (doc.content_hash or ""):
            wanted = True
        elif rec.status == "failed" and rec.attempts < MAX_ATTEMPTS:
            wanted = True
        elif rec.prompt_version < prompt_version and source != "gazette":
            wanted = True

        if wanted:
            yielded += 1
            yield (source, doc.source_key)


async def run_pass(
    keys: list[tuple[str, str]], *, concurrency: int | None = None
) -> dict:
    """Run `keys` through the model with a bounded semaphore."""
    sem = asyncio.Semaphore(concurrency or settings.ENRICH_CONCURRENCY)
    client = EnrichClient()
    counts = {"ok": 0, "needs_review": 0, "failed": 0, "skipped": 0}

    async def _one(source, source_key):
        async with sem:
            inp = await sync_to_async(build_input)(source, source_key)
            if inp is None:
                counts["skipped"] += 1
                return
            rec = await enrich_one(inp, client)
            counts[rec.status] = counts.get(rec.status, 0) + 1

    try:
        await asyncio.gather(*(_one(s, k) for s, k in keys))
    finally:
        usage = dict(client.usage)
        await client.aclose()
    counts["usage"] = usage
    return counts
