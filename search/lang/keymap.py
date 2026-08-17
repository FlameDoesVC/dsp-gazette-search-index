"""Thaana keyboard-layout transliteration. Spec 6.4.

This is NOT phonetic transliteration -- it is the Latin key sequence that
produces Thaana under the standard layout, a strict 1:1 bijection. `migotawq`
is `މިގޮތައް`. Many Maldivians type this way when no Thaana keyboard is
installed.

Query input only. Never store keyboard space: it collides with the phonetic
Latin Dhivehi in `text_latin`, and no language model has learned it.
"""

from __future__ import annotations

import re
from pathlib import Path

from search.lang.normalize import FILI, is_thaana_char

_DATA = Path(__file__).parent / "data" / "keymap.tsv"


def _load() -> dict[str, str]:
    table: dict[str, str] = {}
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, codepoint, _name = line.split("\t")
        table[key] = chr(int(codepoint, 16))
    return table


KEY_TO_THAANA: dict[str, str] = _load()
THAANA_TO_KEY: dict[str, str] = {v: k for k, v in KEY_TO_THAANA.items()}

# Every Thaana codepoint observed in the corpus (38 consonants + 11 fili).
CORPUS_CODEPOINTS: frozenset[str] = frozenset(
    [chr(c) for c in range(0x0780, 0x07A6)] + sorted(FILI)
)

# Characters allowed to pass through a decode untouched.
_PASSTHROUGH = re.compile(r"[\s0-9\-/.,()]")


def decode_keys(s: str) -> str | None:
    """Decode keyboard space to Thaana, or return None if `s` is not
    keyboard space. Failure is clean: either every character maps or none of
    it does."""
    if not s:
        return None
    out: list[str] = []
    mapped = 0
    for ch in s:
        if ch in KEY_TO_THAANA:
            out.append(KEY_TO_THAANA[ch])
            mapped += 1
        elif _PASSTHROUGH.match(ch):
            out.append(ch)
        else:
            return None
    if mapped == 0:
        return None
    return "".join(out)


def encode_keys(s: str) -> str:
    """Encode Thaana to keyboard space. Used to generate test fixtures and
    ASCII-safe slugs, never to build an index."""
    return "".join(THAANA_TO_KEY.get(ch, ch) for ch in s or "")


def _is_well_formed_thaana(s: str) -> bool:
    """Thaana is fully vocalized: **every consonant carries exactly one fili**,
    either a vowel mark or sukun. That orthographic rule is what makes keyboard
    detection decisive rather than statistical.

    Verified against the corpus and against adversarial input. It accepts every
    genuine keyboard-space string and rejects every English word and every
    phonetic Latin-Dhivehi word tested:

        migotawq   -> މިގޮތައް   accept    washing    -> އަސހިނގ    reject
        vazIfA     -> ވަޒީފާ     accept    machine    -> މަޗހިނެ    reject
        kuwqyaSq   -> ކުއްޔަށް   accept    kuyyah     -> ކުޔޔަހ     reject
        hakata     -> ހަކަތަ     accept    bahattaden -> ބަހަތަތަދެނ reject

    A looser rule -- "a fili must follow a consonant" -- accepts all five of
    those right-hand cases and silently mis-decodes ordinary English into
    Thaana. Do not weaken this function.
    """
    i = 0
    saw_consonant = False
    while i < len(s):
        ch = s[i]
        if not is_thaana_char(ch):
            i += 1
            continue
        if ch in FILI:
            return False           # a fili with no consonant carrying it
        if i + 1 >= len(s) or s[i + 1] not in FILI:
            return False           # a bare consonant
        saw_consonant = True
        i += 2
    return saw_consonant


def looks_like_keys(s: str) -> bool:
    """Detection is decisive rather than heuristic: attempt the decode and
    check the result is well-formed Thaana. No wordlist, no threshold."""
    decoded = decode_keys(s)
    if decoded is None:
        return False
    return _is_well_formed_thaana(decoded)
