"""Value normalization. Spec 4.4."""

from __future__ import annotations

from enrich.preextract import split_multivalue
from search.models import SpecKey

MAX_VALUE_LEN = 128

_TRUE = {"yes", "true", "1", "available", "included", "有"}
_FALSE = {"no", "false", "0", "none", "not available", "n/a"}


def normalize_value(key: SpecKey, raw: str) -> list[str]:
    """Split, alias-collapse, and drop anything unusable.

    Splitting first and aliasing second matters: 'Apple (iPhone), Nokia' must
    become two values, both of which then pass through the alias table.
    """
    if not raw or not raw.strip():
        return []

    parts = split_multivalue(raw) or [raw.strip()]
    out: list[str] = []
    for p in parts:
        v = key.resolve_value(p)
        if not v or len(v) > MAX_VALUE_LEN:
            # Truncating a brand produces a wrong brand, so drop it instead.
            continue
        if v not in out:
            out.append(v)
    return out


def parse_bool(raw: str) -> bool | None:
    v = (raw or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None
