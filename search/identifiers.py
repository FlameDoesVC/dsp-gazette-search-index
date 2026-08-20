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




def extract(thaana_text: str, translated_text: str) -> list[dict]:
    """Identifiers common to both sides of one document.

    The display form comes from the Thaana side, which is the source of record.
    The translated side supplies exactly one thing: proof the token survived
    translation, which is what makes it an identifier rather than prose.

    What kind of number each one is -- project, invoice, bid committee -- is
    deliberately not determined. Correlating the number to the document it was
    found in is the entire feature; naming it added a label vocabulary, a
    proximity window and a class of mislabelling bugs for no gain in what a
    reader can do.
    """
    english_keys = set(candidates(translated_text))
    if not english_keys:
        return []
    return [{"value_raw": display, "value_key": key}
            for key, display in candidates(thaana_text).items()
            if key in english_keys]


def looks_like_identifier(q: str) -> bool:
    """Whether a query should be routed through the identifier index.

    Deliberately narrow. A query that is not identifier-shaped must never touch
    that path, because a regression in ordinary search is a worse outcome than
    this feature not shipping.
    """
    token = (q or "").strip().strip(_TRIM)
    return bool(token) and _is_candidate(token)
