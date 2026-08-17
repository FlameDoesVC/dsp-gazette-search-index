"""Dhivehi and English date parsing for gazette metadata.

Month names are matched by **prefix**, not by exact string. Dhivehi has
competing orthographic conventions for the borrowed month names -- the corpus
contains `ސެޕްޓެންބަރު` while other sources write `ސެޕްޓެމްބަރު` or
`ސެޕްޓެމްބަރ`, differing in nasal `ން` versus `މް` and a trailing `ު`. An exact
table gets one variant right and silently drops the rest, which is precisely
what happened: the previous table matched neither spelling the corpus uses.

Each stem below is unambiguous across all twelve months, so prefix matching
cannot collide.
"""

from __future__ import annotations

import datetime as dt
import re

from django.utils import timezone

# stem -> month. Ordered longest-first at match time so `މާރ` cannot shadow a
# longer stem. No two stems are prefixes of one another.
DV_MONTH_STEMS = {
    "ޖެނު": 1,
    "ފެބް": 2,
    "މާރ": 3,
    "އޭޕް": 4, "އެޕް": 4,
    "މެއި": 5, "މޭ": 5,
    "ޖޫން": 6,
    "ޖުލަ": 7,
    "އޮގަ": 8, "އޯގަ": 8,
    "ސެޕް": 9,
    "އޮކް": 10,
    "ނޮވ": 11,
    "ޑިސ": 12,
}

EN_MONTH_STEMS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# day, month-word, 4-digit year, optional HH:MM
_DMY = re.compile(
    r"(\d{1,2})\s+([^\s\d]+)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?"
)
_TIME = re.compile(r"(\d{1,2}):(\d{2})")

MIN_YEAR = 1990
MAX_YEAR = 2100


def parse_dv_month(token: str) -> int | None:
    token = (token or "").strip()
    if not token:
        return None
    for stem, month in sorted(DV_MONTH_STEMS.items(), key=lambda kv: -len(kv[0])):
        if token.startswith(stem):
            return month
    low = token.lower()
    for stem, month in EN_MONTH_STEMS.items():
        if low.startswith(stem):
            return month
    return None


def parse_dv_datetime(s: str, *, time_str: str = "") -> dt.datetime | None:
    """Parse `23 އޮގަސްޓް 2026 13:00` and friends into an aware datetime.

    A date with no time at all becomes 23:59 local: a deadline of "17 August"
    has not passed at 09:00 on the 17th, and defaulting to midnight would
    close every such vacancy a day early. An explicit `00:00` is preserved.
    """
    if not s:
        return None
    match = _DMY.search(s)
    if not match:
        return None

    day_s, month_token, year_s, hh, mm = match.groups()
    month = parse_dv_month(month_token)
    if month is None:
        return None

    day, year = int(day_s), int(year_s)
    if not (MIN_YEAR <= year <= MAX_YEAR):
        return None

    if hh is None and time_str:
        t = _TIME.search(time_str)
        if t:
            hh, mm = t.groups()

    if hh is None:
        hour, minute = 23, 59
    else:
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None

    try:
        naive = dt.datetime(year, month, day, hour, minute)
    except ValueError:          # 31 February and similar
        return None
    return timezone.make_aware(naive)
