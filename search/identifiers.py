"""Identifier extraction and matching. Spec sections 4 and 5.

No model call, and none is wanted. An identifier is a token that translation does
not touch, so the tokens appearing verbatim in both the Thaana and the translated
text are almost exactly the identifier set -- measured on iulaan 408123, the
intersection is four tokens and all four are identifiers, with no noise.

That also makes fabrication structurally impossible rather than merely validated:
a candidate exists only if it is in both texts, so there is no path by which an
invented number reaches the index.
"""

from __future__ import annotations

import re

KINDS = [
    ("project", "project"),
    ("announcement", "announcement"),
    ("bid_committee", "bid committee"),
    ("job", "job opportunity"),
    ("license", "license or authority"),
    ("reference", "reference"),
    ("invoice", "invoice"),
    ("contract", "contract"),
    ("other", "other"),
]

# The leading alternative absorbs a parenthesized type prefix such as "(IUL)"
# so it cannot glue onto the number after it and change that number's key. The
# ":" in the token run admits scheme URLs so _URLISH can reject them whole.
_TOKEN = re.compile(r"\([A-Za-z]+\)|[A-Za-z0-9][A-Za-z0-9()/.\-:]*")
_TRIM = ".,);:'\""
_MIN_LEN = 7

# A URL contains slashes and digits and is not a reference number.
_URLISH = re.compile(r"(?:www\.|https?:)", re.I)
# Maldivian numbers are seven digits starting 7 or 9 (mobile) or 3 or 6
# (landline), and advertisers write pairs joined by a slash. Measured:
# '7924894/3315555' was one of only four candidates the intersection missed, and
# it should never have been a candidate.
_PHONE = r"(?:\+?960[\s-]?)?(?:[79]\d{6}|[36]\d{6})"
_PHONE_PAIR = re.compile(rf"^{_PHONE}(?:\s*/\s*{_PHONE})+$")

# Labels, most specific first; classify_kind takes the rightmost match because
# the label nearest the identifier is the one that names it. 'announcement
# number' matches inside 'in response to announcement number' without a
# separate rule.
_LABEL_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"bid\s+committee|meeting\s+number", re.I), "bid_committee"),
    (re.compile(r"project\s+number", re.I), "project"),
    (re.compile(r"job\s+opportunit\w*\s+number", re.I), "job"),
    (re.compile(r"(?:licen[cs]e|authority)\s+number", re.I), "license"),
    (re.compile(r"(?:announcement|iulaan)\s+number", re.I), "announcement"),
    (re.compile(r"invoice\s+number", re.I), "invoice"),
    (re.compile(r"contract\s+number", re.I), "contract"),
    (re.compile(r"\bref(?:erence)?\.?\s*$", re.I), "reference"),
]

# How far back to look for the label. One line of prose; more than this and the
# label belongs to something else.
LABEL_WINDOW = 60


def value_key(raw: str) -> str:
    """Digits in order, then the letter multiset sorted.

    Letters are where the noise lives -- office codes, transpositions, stray
    parentheses -- and digits carry the identity. Sorting the letters absorbs a
    transposition without discarding them, which matters: dropping letters
    entirely collides BC-171/2026/094 with PC-171/2026/094, a bid committee
    meeting and a project.
    """
    upper = (raw or "").upper().strip(_TRIM)
    digits = "-".join(re.findall(r"[0-9]+", upper))
    letters = "".join(sorted(re.findall(r"[A-Z]", upper)))
    return f"{digits}|{letters}"


def _is_candidate(token: str) -> bool:
    if len(token) < _MIN_LEN or "/" not in token:
        return False
    if not any(c.isdigit() for c in token):
        return False
    if _URLISH.search(token):
        return False
    if _PHONE_PAIR.match(token):
        return False
    return True


def candidates(text: str) -> dict[str, str]:
    """`value_key` -> display form, for every identifier-shaped token in `text`.

    Keyed rather than listed because the intersection in `extract` is over keys:
    the two sides of a document routinely spell the same identifier differently.
    First occurrence wins the display slot.
    """
    out: dict[str, str] = {}
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0).strip(_TRIM)
        if _is_candidate(token):
            out.setdefault(value_key(token), token)
    return out


def _positioned(text: str) -> list[tuple[str, int]]:
    out = []
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0).strip(_TRIM)
        if _is_candidate(token):
            out.append((value_key(token), match.start()))
    return out


def classify_kind(preceding_text: str) -> str:
    """The kind of identifier, from the words in front of it.

    Measured: 13 of 31 identifiers in translated bodies state their kind this
    way and 18 do not. `other` is a perfectly good answer -- the link searches
    the number, so kind is display metadata.
    """
    window = re.sub(r"\s+", " ", (preceding_text or ""))[-LABEL_WINDOW:]
    best = "other"
    best_pos = -1
    for pattern, kind in _LABEL_RULES:
        match = pattern.search(window)
        if match and match.start() > best_pos:
            best_pos = match.start()
            best = kind
    return best


def extract(thaana_text: str, translated_text: str) -> list[dict]:
    """Identifiers common to both sides of one document.

    The display form comes from the Thaana side, which is the source of record.
    The translated side supplies two things and nothing else: proof the token
    survived translation, and the English label that gives the kind.
    """
    english = _positioned(translated_text)
    english_keys = {key for key, _pos in english}
    if not english_keys:
        return []

    labels: dict[str, tuple[str, str]] = {}
    for key, pos in english:
        if key in labels and labels[key][0] != "other":
            continue
        before = (translated_text or "")[max(0, pos - LABEL_WINDOW):pos]
        labels[key] = (classify_kind(before),
                       re.sub(r"\s+", " ", before).strip()[-40:])

    rows = []
    for key, display in candidates(thaana_text).items():
        if key not in english_keys:
            continue
        kind, label_raw = labels.get(key, ("other", ""))
        rows.append({"value_raw": display, "value_key": key,
                     "kind": kind, "label_raw": label_raw})
    return rows


def looks_like_identifier(q: str) -> bool:
    """Whether a query should be routed through the identifier index.

    Deliberately narrow. A query that is not identifier-shaped must never touch
    that path, because a regression in ordinary search is a worse outcome than
    this feature not shipping.
    """
    token = (q or "").strip().strip(_TRIM)
    return bool(token) and _is_candidate(token)
