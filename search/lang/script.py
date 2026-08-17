"""Per-token script detection. Spec 6.1.

Order matters: keyboard space is checked before phonetic Latin because its
test is exact (a decode either succeeds or fails), while the phonetic test is
a heuristic. Running the heuristic first would let it claim tokens the exact
test could have resolved.
"""

from __future__ import annotations

import re

from search.lang.keymap import looks_like_keys
from search.lang.normalize import contains_thaana, normalize_text

THAANA = "dv-Thaa"
KEYS = "dv-Keys"
LATIN_DV = "dv-Latn"
ENGLISH = "en"

# Markers drawn from real corpus titles: "Halaalukuvefa hunna", "kuyyah
# dhinun", "firihen kudhin bahattaden", "iPhone 13 vikkan".
_MARKER_WORDS = frozenset("""
beynun beynunvaa vikkan vikkanee vikkaa gannan hoadhan kuyyah kuyyah's
hunna huri hifun dhinun dhookuran libey libeyne nulibey
firihen anhen kudhin bahattan bahattaden baithibbaa thibbaa
vazeefaa vazeefa masakkaiy mauloomaathu dhennevun
laari rufiyaa mihaaru miadhu adhi noon
ge ah aa akah eh ekey thakah kah
""".split())

_DIGRAPHS = ("aa", "ee", "oo", "dh", "th", "lh", "gn", "sh", "ey", "oa")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _phonetic_score(token: str) -> float:
    if token in _MARKER_WORDS:
        return 1.0
    hits = sum(token.count(d) for d in _DIGRAPHS)
    if not hits:
        return 0.0
    # Normalize by length so short tokens are not over-rewarded.
    return min(1.0, (hits * 2.0) / max(len(token), 1))


def detect_script(token: str) -> str:
    token = normalize_text(token)
    if not token:
        return ENGLISH
    if contains_thaana(token):
        return THAANA
    if token.isdigit():
        return ENGLISH
    if looks_like_keys(token):
        return KEYS
    if _phonetic_score(token) >= 0.5:
        return LATIN_DV
    return ENGLISH


def detect_query_script(q: str) -> tuple[str, list[tuple[str, str]]]:
    """Return `(dominant_label, [(token, label), ...])`."""
    tokens = _TOKEN.findall(normalize_text(q))
    if not tokens:
        return ENGLISH, []
    labelled = [(t, detect_script(t)) for t in tokens]

    counts: dict[str, int] = {}
    for _token, label in labelled:
        counts[label] = counts.get(label, 0) + 1
    # Any Thaana at all dominates: it is unambiguous evidence.
    if counts.get(THAANA):
        return THAANA, labelled
    dominant = max(counts, key=lambda k: (counts[k], k != ENGLISH))
    return dominant, labelled
