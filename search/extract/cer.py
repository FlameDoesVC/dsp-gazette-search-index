"""Character error rate and the transcription quality gate. Spec 5.6.1.

The evaluation corpus is free: PDFs that *do* have a text layer give
near-ground-truth Thaana via pdftotext. Transcribe the same files and compare.
Real Maldivian government documents, zero labelling cost.
"""

from __future__ import annotations

import re

from django.conf import settings

_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS.sub("", s or "")


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over characters, divided by reference length.

    Whitespace is stripped from both sides: layout differences between
    `pdftotext -layout` and a transcription are not errors.
    """
    ref, hyp = _normalize(reference), _normalize(hypothesis)
    if not ref:
        return 1.0
    if ref == hyp:
        return 0.0

    previous = list(range(len(hyp) + 1))
    for i, ref_char in enumerate(ref, start=1):
        current = [i]
        for j, hyp_char in enumerate(hyp, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ref_char != hyp_char),
                )
            )
        previous = current
    return previous[len(hyp)] / len(ref)


def passes_gate(cer: float) -> bool:
    return cer <= getattr(settings, "TRANSCRIBE_MAX_CER", 0.15)
