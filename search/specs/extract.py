"""The deterministic unit-pattern extractor. Spec 4.4.

Numeric specs often live in the title, not in a field: the real listing
`KICO METAL POWER SUPPLY 24V-5A-120W / 7884445` carries its entire spec sheet
as a compact string. So this runs over title and description before the model
does, and the model's job is only to assign semantic keys to what it missed.
Cheaper, and it cannot hallucinate a voltage.

The vocabulary comes from SpecKey, so adding a unit is an admin row rather than
a deploy. That is the P4-to-P7 change: enrich/preextract.py's UNIT_VOCAB
constant is replaced by unit_vocabulary().
"""

from __future__ import annotations

import re

from search.models import SpecKey

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Units not tied to a registered key. Captured as key_raw so they surface in
# the promotion queue instead of being silently discarded.
_UNREGISTERED = ["mAh", "kWh", "GHz", "MHz", "sqft", "inch", "kg", "ml", "cm", "mm"]


def unit_vocabulary() -> list[str]:
    """Every unit token, longest first so 'mAh' wins over 'A'."""
    tokens: set[str] = set(_UNREGISTERED)
    for k in SpecKey.objects.exclude(unit="").only("unit", "unit_aliases"):
        tokens.add(k.unit)
        tokens.update(a for a in k.unit_aliases if a)
    return sorted(tokens, key=lambda t: (-len(t), t.lower()))


def _key_index() -> dict[str, SpecKey]:
    """unit token (lowercased) -> the SpecKey that owns it."""
    index: dict[str, SpecKey] = {}
    for k in SpecKey.objects.exclude(unit="").only(
        "id", "key", "unit", "unit_aliases", "datatype"
    ):
        index.setdefault(k.unit.lower(), k)
        for a in k.unit_aliases:
            index.setdefault(a.lower(), k)
    return index


def _pattern(vocab: list[str]) -> re.Pattern:
    alt = "|".join(re.escape(u) for u in vocab)
    # Leading guard rejects '...445GB' inside a longer digit run; trailing
    # guard rejects 'Vodafone' matching the 'V' unit.
    return re.compile(rf"(?<![A-Za-z\d])({_NUM})\s*({alt})(?![A-Za-z])", re.I)


def extract_units(text: str) -> list[dict]:
    """Returns [{key, key_raw, value, unit}] with key None when unregistered."""
    if not text:
        return []
    vocab = unit_vocabulary()
    if not vocab:
        return []

    index = _key_index()
    seen: set[tuple[str, float]] = set()
    out: list[dict] = []

    for m in _pattern(vocab).finditer(text):
        raw_num, raw_unit = m.group(1), m.group(2)
        value = float(raw_num.replace(",", ""))
        spec_key = index.get(raw_unit.lower())
        key_name = spec_key.key if spec_key else raw_unit.lower()

        if (key_name, value) in seen:
            continue
        seen.add((key_name, value))

        out.append({
            "key": spec_key.key if spec_key else None,
            "key_id": spec_key.id if spec_key else None,
            "key_raw": key_name,
            "value": value,
            "unit": spec_key.unit if spec_key else raw_unit,
        })
    return out
