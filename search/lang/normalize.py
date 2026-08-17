"""Text normalization. Pure functions, no I/O. Spec 6.2."""

from __future__ import annotations

import re
import unicodedata

# Thaana block. Consonants occupy U+0780-U+07A5; fili (vowel marks and sukun)
# occupy U+07A6-U+07B0. The corpus contains exactly 49 distinct codepoints:
# 38 consonants and all 11 fili.
THAANA_RANGE = (0x0780, 0x07BF)
FILI = frozenset(chr(c) for c in range(0x07A6, 0x07B1))

_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None
)
_ARABIC_INDIC = {chr(0x0660 + i): str(i) for i in range(10)}
_EXT_ARABIC_INDIC = {chr(0x06F0 + i): str(i) for i in range(10)}
_DIGITS = str.maketrans({**_ARABIC_INDIC, **_EXT_ARABIC_INDIC})

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def is_thaana_char(ch: str) -> bool:
    return THAANA_RANGE[0] <= ord(ch) <= THAANA_RANGE[1]


def contains_thaana(s: str) -> bool:
    return any(is_thaana_char(c) for c in s or "")


def strip_html(s: str) -> str:
    """Return visible text. Gazette bodies are Word-exported HTML tables and
    indexing `td`/`valign`/`strong` would poison the vocabulary (spec 6.2)."""
    if not s or "<" not in s:
        return (s or "").strip()
    try:
        from lxml import html as lxml_html

        text = lxml_html.fromstring(s).text_content()
    except Exception:
        text = _TAG.sub(" ", s)
    return _WS.sub(" ", text).strip()


def strip_fili(s: str) -> str:
    """Remove vowel marks and sukun, leaving the consonant skeleton.

    Highest-impact recall trick for Thaana, because users type fili
    inconsistently. Indexed at weight C, never alone -- see spec 6.2.
    """
    return "".join(c for c in (s or "") if c not in FILI)


def normalize_text(s: str) -> str:
    """Script-agnostic normalization: NFC, drop zero-width, ASCII digits,
    casefold Latin, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_ZERO_WIDTH).translate(_DIGITS)
    s = s.casefold()
    return _WS.sub(" ", s).strip()


def normalize_dv(s: str, *, drop_fili: bool = False) -> str:
    """Normalize Thaana text. `drop_fili` selects the skeleton form."""
    out = normalize_text(strip_html(s))
    return strip_fili(out) if drop_fili else out
