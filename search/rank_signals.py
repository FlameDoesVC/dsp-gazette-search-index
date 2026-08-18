"""Ranking signal helpers. Spec 7, P9 task 6.

`stuffing_penalty` feeds the existing `quality` term rather than adding a new
ranking weight: repetition inflates `ts_rank_cd` position counts, so a stuffed
title wins lexically while saying less. Measured over 19,570 titles, repetition
ratio mean 0.030 and median 0.000 -- a small minority, but it is the minority
that wins.
"""

from __future__ import annotations

import re

# The 0.15 floor matches the measured ">0.15 repetition: 1,180 listings"
# boundary; ordinary titles are untouched.
_STUFFING_FLOOR = 0.15
_TOKEN = re.compile(r"[a-z0-9ހ-޿]+", re.UNICODE)


def stuffing_penalty(title: str) -> float:
    """1 - unique_tokens/total_tokens, floored at 0 below the stuffing floor.

    0.50 for "AC Gas Leakage AC- Water Leakage. Maintenance. Water. Leakage.
    Gas." (7 tokens, 3 unique), 0.0 for "iPhone 13".
    """
    tokens = _TOKEN.findall((title or "").casefold())
    if len(tokens) < 4:
        return 0.0
    ratio = 1 - len(set(tokens)) / len(tokens)
    return ratio if ratio > _STUFFING_FLOOR else 0.0
