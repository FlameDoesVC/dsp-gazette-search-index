"""Phonetic Thaana/Latin transliteration. Spec 6.3.

Unlike the keyboard mapping in `keymap`, this is many-to-one in both
directions, so the Latin-to-Thaana path returns a bounded *set* of candidate
spellings rather than one answer.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from pathlib import Path

from search.lang.normalize import normalize_text

_DATA = Path(__file__).parent / "data" / "translit.tsv"

MAX_VARIANTS = 24
_MAX_LATIN_TOKEN = 24   # refuse to expand absurdly long tokens


def _load() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = defaultdict(list)
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        codepoint = parts[0]
        primary = parts[1] if len(parts) > 1 else ""
        alternates = (
            [a for a in parts[2].split(",") if a] if len(parts) > 2 else []
        )
        ch = chr(int(codepoint, 16))
        forward[ch] = [primary] + alternates
        for latin in [primary] + alternates:
            if latin and ch not in reverse[latin]:
                reverse[latin].append(ch)
    return forward, dict(reverse)


DV_TO_LATIN, LATIN_TO_DV = _load()

# Longest-first so `sh` is matched before `s` when scanning Latin input.
_LATIN_KEYS = sorted(LATIN_TO_DV, key=len, reverse=True)


def translit_dv_to_latin(s: str) -> str:
    """Deterministic Thaana to Latin using the primary reading of each
    character. Non-Thaana characters pass through."""
    if not s:
        return ""
    return "".join(
        DV_TO_LATIN[ch][0] if ch in DV_TO_LATIN else ch for ch in s
    )


def _segment(token: str) -> list[list[str]] | None:
    """Greedy longest-match segmentation of a Latin token into per-segment
    Thaana candidate lists."""
    out: list[list[str]] = []
    i = 0
    while i < len(token):
        for key in _LATIN_KEYS:
            if token.startswith(key, i):
                out.append(LATIN_TO_DV[key])
                i += len(key)
                break
        else:
            return None
    return out


def translit_latin_to_dv_variants(s: str) -> list[str]:
    """Candidate Thaana spellings for a Latin token, capped at MAX_VARIANTS.

    Consonant-only output: fili are not reconstructed, because the reader
    cannot know which vowel was intended. That is fine -- the skeleton half of
    `vector_dv` (weight C) is exactly what this matches against (spec 6.2).
    """
    token = normalize_text(s)
    if not token or len(token) > _MAX_LATIN_TOKEN:
        return []
    segments = _segment(token)
    if not segments:
        return []
    total = 1
    for seg in segments:
        total *= len(seg)
        if total > MAX_VARIANTS:
            # Too ambiguous to expand; fall back to primary readings only.
            return ["".join(seg[0] for seg in segments)]
    return ["".join(combo) for combo in itertools.product(*segments)]


def translit_latin_variants(s: str) -> list[str]:
    """Latin spellings of a Thaana string, primary first."""
    if not s:
        return []
    per_char = [DV_TO_LATIN.get(ch, [ch]) for ch in s]
    total = 1
    for options in per_char:
        total *= len(options)
        if total > MAX_VARIANTS:
            return [translit_dv_to_latin(s)]
    return ["".join(combo) for combo in itertools.product(*per_char)]
