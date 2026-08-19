"""Deterministic contact extraction. Spec section 12.

Maldivian numbers are seven digits: mobile starts 7 or 9, landline 3 or 6. The
+960 prefix is optional and the number is frequently welded to the end of a
title with no separator, hence explicit boundary guards rather than \b, which
would happily match the tail of a longer run of digits.

This module owns the pattern. `enrich/preextract.py` imports it so the candidate
list the model sees and the provider key the entity layer computes can never
disagree about what a phone number is.
"""

from __future__ import annotations

import re

PHONE_RE = re.compile(
    r"(?<![\d])(?:\+?960[\s\-]?)?([79]\d{6}|[36]\d{6})(?![\d])")

_LEFTOVER_SPACE = re.compile(r"[ \t]{2,}")


def primary_phone(*texts: str) -> str:
    """The number to call, or "".

    Ordered: the caller passes title before description, because a seller who
    puts one number in the title and another in the body means the first.
    """
    for text in texts:
        if not text:
            continue
        m = PHONE_RE.search(text)
        if m:
            return m.group(1)
    return ""


def all_phones(*texts: str) -> list[str]:
    out: list[str] = []
    for text in texts:
        for m in PHONE_RE.finditer(text or ""):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def strip_phones(text: str) -> str:
    """Remove phone numbers from a string meant for display.

    Display only. The number stays in title_en and therefore in the search
    vector, because searching a corpus where one advertiser holds 1,680 listings
    by their phone number is a legitimate query.
    """
    out = PHONE_RE.sub("", text or "")
    out = _LEFTOVER_SPACE.sub(" ", out)
    # '&' is in the strip set because sellers write 'FREE DELIVERY | &7776828',
    # which otherwise renders as a title ending in '| &'. '.' is deliberately
    # NOT: 'Aircon Repair & Service.' ends in a sentence, not in debris.
    return out.strip(" \t-,|/:&").strip()
