"""Card payloads for entity-backed documents. Spec section 11.

Nothing time-dependent goes in here, same rule as enrich/cards.py: the card is
written at index time and read for months.
"""

from __future__ import annotations


def _values(fields, key_raw: str) -> list[str]:
    return [f.value_text for f in fields
            if f.key_raw == key_raw and f.value_text]


def build_service_card(entity, fields, base: dict) -> dict:
    """A service is not a product: no spec chips, no condition, no brand.
    What a caller wants is who does what, where, and the number to call."""
    return {
        **base,
        "kind": "service",
        "title": entity.title_en or base.get("title", ""),
        "summary": entity.summary_en or base.get("summary", ""),
        "services_offered": _values(fields, "service_offered")[:6],
        "coverage": _values(fields, "coverage")[:6],
        "rate_basis": next(iter(_values(fields, "rate_basis")), ""),
        "call_out": next(iter(_values(fields, "call_out")), ""),
        "listing_count": entity.listing_count,
    }


def spec_chips(fields, limit: int = 3) -> list[str]:
    out: list[str] = []
    for f in fields:
        if f.value_num is None or not f.unit:
            continue
        n = int(f.value_num) if float(f.value_num).is_integer() else f.value_num
        out.append(f"{n}{f.unit}")
        if len(out) >= limit:
            break
    return out
