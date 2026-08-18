"""Scanned-PDF transcription. Spec 5.6, rung 3 (superseded).

Rung 3 replaced a Claude-native-PDF rung that fabricated on real scans (0%
anchor overlap). The pipeline is now: rasterize locally, Google Cloud Vision
OCR, fili repair by a 60M T5 on CPU, a consonant-skeleton gate, and an anchor
check against the iulaan's own title and office. Every paid Vision call goes
through the content-addressed cache so a re-run never re-bills.

See docs/superpowers/measurements/2026-08-18-p3-attachments.md for the
backend evaluation that picked this pipeline.
"""

from __future__ import annotations

import logging

from django.conf import settings

from search.extract.cache import cached_call
from search.extract.local import TEXT_CAP, ExtractionResult, _clean

logger = logging.getLogger(__name__)


def transcribe_pdf(pdf: bytes, *, title: str = "", office: str = "",
                   page_count: int | None = None) -> ExtractionResult:
    """Rung 3: Vision OCR, fili repair, skeleton gate, anchor check."""
    from search.extract import ocr, repair

    try:
        pages = ocr.rasterize(pdf, first=1, last=page_count)
    except Exception as exc:
        return ExtractionResult(method="transcribed", status="failed",
                                error=f"rasterize: {exc}"[:500])

    raw_pages, gated_pages, accepted = [], [], []
    for png in pages:
        try:
            raw, _ = cached_call(
                "vision", "DOCUMENT_TEXT_DETECTION", png, "dv",
                lambda png=png: ocr.vision_ocr(png),
            )
        except Exception as exc:
            return ExtractionResult(method="transcribed", status="failed",
                                    error=f"vision: {exc}"[:500])
        raw_pages.append(raw)
        fixed = repair.repair_text(raw)
        gated, kept = repair.skeleton_gate(raw, fixed)
        gated_pages.append(gated)
        accepted.append(kept)

    text = _clean("\n".join(gated_pages))
    if not text:
        return ExtractionResult(method="transcribed", status="failed",
                                error="empty transcription")

    # Grounding: does this describe the document it is attached to? Measured
    # 0% for a fabricated page against 87% for a good one. Cheap insurance
    # against an upstream model change silently regressing into invention.
    score = ocr.anchor_overlap(text, title=title, office=office)
    if title or office:
        if score < settings.OCR_ANCHOR_MIN:
            return ExtractionResult(
                method="transcribed", status="failed",
                error=f"anchor overlap {score:.2f} below "
                      f"{settings.OCR_ANCHOR_MIN}; transcription does not "
                      f"match the iulaan metadata",
            )

    mean_kept = sum(accepted) / len(accepted) if accepted else 0.0
    logger.info("transcribed %d pages, anchor %.2f, gate accepted %.0f%%",
                len(pages), score, 100 * mean_kept)
    return ExtractionResult(text=text[:TEXT_CAP], method="transcribed",
                            status="ok", transcribed=True)
