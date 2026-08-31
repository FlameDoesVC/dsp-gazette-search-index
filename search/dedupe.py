"""Repost collapsing. Keeps the most recent listing of each group.

Sellers repost the same advertisement daily to stay near the top of a
marketplace source's own ordering; 8,089 of 20,445 rows are duplicate titles,
one appearing 202 times.

The key is (seller, normalized title, price) and deliberately NOT
`content_hash`: measured, 202 identical-titled rows carry 17 distinct content
hashes because descriptions vary slightly, so a body-inclusive hash collapses
almost nothing.

Gazette is excluded. Two councils may publish identically-titled notices, and a
published government notice is not a repost.
"""

from __future__ import annotations

import hashlib
import re

from search.lang.normalize import normalize_text

EXCLUDED_SOURCES = {"gazette", "archive"}

# Strips punctuation and runs of non-alphanumerics so "MN-2 ROOM FOR
# DAILY/HOURLY RENT." and "MN-2 Room for Daily / Hourly Rent" are one group.
# normalize_text casefolds but keeps punctuation; the plan assumed otherwise.
_NORM = re.compile(r"[^a-z0-9ހ-޿]+")


def _title_key(title: str) -> str:
    return _NORM.sub(" ", (title or "").casefold()).strip()


def dedupe_key(*, source: str, seller: str, title: str, price) -> str:
    if source in EXCLUDED_SOURCES:
        return ""
    basis = "|".join([
        source,
        (seller or "").strip().lower(),
        _title_key(title) or normalize_text(title or ""),
        f"{float(price):.2f}" if price is not None else "",
    ])
    return hashlib.sha256(basis.encode()).hexdigest()
