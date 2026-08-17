"""Scanned-PDF transcription with Claude Haiku 4.5. Spec 5.6.1.

Native PDF input: the file goes up as a `document` content block and Claude
renders the pages. No rasterization, no temp images, no RAM spike -- and the
model sees the page as laid out, which matters because the content we want most
is a salary table.

Runs through the Batch API. Extraction is a background command with no latency
requirement, so paying list price would be pointless.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from django.conf import settings

from search.extract.local import TEXT_CAP, ExtractionResult, _clean

logger = logging.getLogger(__name__)

_PROMPT = """Transcribe this document verbatim.

Rules:
- Output only the transcribed text. No preamble, no commentary, no summary.
- Do not translate. Dhivehi (Thaana) stays in Thaana; English stays in English.
- Preserve numbers, dates and reference codes exactly as written.
- Preserve table structure using " | " between cells, one row per line.
- If a region is illegible, write [illegible] rather than guessing.
"""


@dataclass(slots=True)
class TranscriptionItem:
    custom_id: str
    content: bytes
    page_range: tuple[int, int | None] | None = None


def chunk_ranges(page_count: int | None) -> list[tuple[int, int | None]]:
    """Split a document into per-request page ranges.

    Haiku 4.5 caps output at 64,000 tokens; a page transcribes to roughly 1,500,
    so ~20 pages is the safe ceiling per request.
    """
    if not page_count:
        return [(1, None)]
    per_chunk = getattr(settings, "TRANSCRIBE_PAGES_PER_CHUNK", 20)
    ranges: list[tuple[int, int | None]] = []
    start = 1
    while start <= page_count:
        end = min(start + per_chunk - 1, page_count)
        ranges.append((start, end))
        start = end + 1
    return ranges


def build_request(content: bytes, *, page_range=None) -> dict:
    """Message params for one transcription request."""
    encoded = base64.standard_b64encode(content).decode("ascii")
    instruction = _PROMPT
    if page_range and page_range[1]:
        instruction += (
            f"\nTranscribe pages {page_range[0]} to {page_range[1]} only.\n"
        )
    return {
        "model": getattr(settings, "TRANSCRIBE_MODEL", "claude-haiku-4-5"),
        "max_tokens": 32_000,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": instruction},
                ],
            }
        ],
    }


def parse_response(text: str) -> ExtractionResult:
    cleaned = _clean(text or "")
    if not cleaned:
        return ExtractionResult(
            method="transcribed", status="failed", error="empty response"
        )
    return ExtractionResult(
        text=cleaned[:TEXT_CAP], method="transcribed", status="ok",
        transcribed=True,
    )


def transcribe_batch(items: list[TranscriptionItem]) -> dict[str, ExtractionResult]:
    """Submit every item as one batch, wait for it, return results by custom_id.

    Batch results arrive in arbitrary order, so they are keyed by `custom_id`
    and never by position.
    """
    if not items:
        return {}

    import anthropic
    from anthropic.types.message_create_params import (
        MessageCreateParamsNonStreaming,
    )
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic(
        api_key=getattr(settings, "CLAUDE_API_KEY", None)
        or None,
    )
    batch = client.messages.batches.create(
        requests=[
            Request(
                custom_id=item.custom_id,
                params=MessageCreateParamsNonStreaming(
                    **build_request(item.content, page_range=item.page_range)
                ),
            )
            for item in items
        ]
    )
    logger.info("submitted batch %s with %d requests", batch.id, len(items))

    import time

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        time.sleep(30)

    out: dict[str, ExtractionResult] = {}
    for entry in client.messages.batches.results(batch.id):
        if entry.result.type != "succeeded":
            out[entry.custom_id] = ExtractionResult(
                method="transcribed",
                status="failed",
                error=str(entry.result.type),
            )
            continue
        text = "".join(
            block.text
            for block in entry.result.message.content
            if block.type == "text"
        )
        out[entry.custom_id] = parse_response(text)
    return out
