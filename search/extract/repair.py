"""Fili restoration over faithful OCR, with a verifiable gate. Spec 5.6.

Vision reads the consonants but drops ~25% of the fili. A 60M `t5-small`
fine-tuned on Dhivehi ASR error correction restores them to 0.98 (corpus
baseline 0.99) in 0.8s per page on CPU -- no GPU, so translation keeps its
slots.

The gate is the point. A repaired word is accepted only where its consonant
skeleton is unchanged, so the model can re-vowel but cannot substitute. That
turns "the model probably did not invent this" into "the model provably could
not have", which is the difference between this rung and the LLM rung it
replaces.
"""

from __future__ import annotations

import difflib
import functools
import logging
import re

from django.conf import settings

from search.lang.normalize import strip_fili

logger = logging.getLogger(__name__)

_WORD = re.compile(r"([ހ-޿]+)")
MAX_INPUT_TOKENS = 256


@functools.lru_cache(maxsize=1)
def _model():
    """Loaded once per process. ~240 MB, CPU."""
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    name = settings.OCR_REPAIR_MODEL
    return AutoTokenizer.from_pretrained(name), \
        AutoModelForSeq2SeqLM.from_pretrained(name).eval()


def repair_text(text: str, *, batch_size: int = 16) -> str:
    """Re-vowel line by line. Ungated -- callers must pass the result through
    `skeleton_gate` before trusting it."""
    if not text.strip():
        return text
    import torch

    tok, model = _model()
    lines = text.splitlines()
    out: list[str] = []
    with torch.no_grad():
        for i in range(0, len(lines), batch_size):
            chunk = ["fix: " + line for line in lines[i:i + batch_size]]
            enc = tok(chunk, return_tensors="pt", padding=True,
                      truncation=True, max_length=MAX_INPUT_TOKENS)
            gen = model.generate(**enc, max_length=MAX_INPUT_TOKENS, num_beams=1)
            out += tok.batch_decode(gen, skip_special_tokens=True)
    return "\n".join(out)


def skeleton_gate(source: str, repaired: str) -> tuple[str, float]:
    """Keep repaired words whose consonant skeleton matches the OCR.

    Returns (gated_text, accepted_fraction). Alignment is difflib over the
    skeleton sequence rather than a positional zip: a single inserted or
    dropped word would otherwise shift everything and discard a good page.

    Non-Thaana runs -- Latin, digits, reference numbers -- come from `source`
    untouched, so nothing outside the Thaana runs can drift.
    """
    src_words = _WORD.findall(source)
    if not src_words:
        return source, 1.0
    rep_words = _WORD.findall(repaired)

    src_sk = [strip_fili(w) for w in src_words]
    rep_sk = [strip_fili(w) for w in rep_words]

    out = list(src_words)
    kept = 0
    matcher = difflib.SequenceMatcher(a=src_sk, b=rep_sk, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out[i1:i2] = rep_words[j1:j2]
            kept += i2 - i1

    it = iter(out)
    return _WORD.sub(lambda _m: next(it), source), kept / len(src_words)
