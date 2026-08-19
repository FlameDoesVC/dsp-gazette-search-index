"""Deterministic identity extraction. Spec section 7.

No model call anywhere in this module. Everything here has to be reproducible,
because the entity key is computed from it and a key that moves between runs
splits one entity into several on every pass.
"""

from __future__ import annotations

import hashlib
import re

from search.contacts import strip_phones

# Marketing vocabulary measured in the corpus. These words appear in titles as
# selling copy, never as identity, so they are removed before tokenizing.
_MARKETING = re.compile(
    r"\b(free|delivery|delivary|call|whatsapp|viber|tel|telephone|contact|"
    r"sms|order|now|available|stock|instock|best|price|offer|sale|discount|"
    r"cheap|new|brand\s+new|used|original|genuine|quality|shop|visit|cash|"
    r"bml|transfer|urgent|limited|hot|deal)\b", re.I)
_SEPARATORS = re.compile(r"[|:;,\.\(\)\[\]♦♥*#]+")
_WS = re.compile(r"\s+")

# A model token carries a digit: RL-S07100C, A15, 128GB, 200W.
_MODEL_TOKEN = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-/\.]{1,23}$")
_BARE_YEAR = re.compile(r"^20\d{2}$")


def clean_title(text: str) -> str:
    out = strip_phones(text or "")
    out = _MARKETING.sub(" ", out)
    out = _SEPARATORS.sub(" ", out)
    return _WS.sub(" ", out).strip(" -_")


def model_tokens(text: str, limit: int = 4) -> list[str]:
    """Sorted, uppercased, deduplicated. Sorted because a reposted listing with
    the words rearranged must land on the same entity."""
    seen: set[str] = set()
    for word in clean_title(text).split():
        token = word.strip("-/.").upper()
        if not _MODEL_TOKEN.match(token) or _BARE_YEAR.match(token):
            continue
        if token.isdigit():          # a bare quantity is not a model
            continue
        seen.add(token)
    return sorted(seen)[:limit]


def brand_vocabulary() -> dict[str, str]:
    """Lowercased alias -> canonical brand name."""
    from catalog.models import Brand

    vocab: dict[str, str] = {}
    for brand in Brand.objects.filter(is_active=True).only("name", "aliases"):
        vocab[brand.name.lower()] = brand.name
        for alias in brand.aliases or []:
            vocab[str(alias).lower()] = brand.name
    return vocab


def match_brand(text: str, vocabulary: dict[str, str]) -> str:
    """Longest alias wins, so 'Green Lion' beats 'Lion'. Empty when unknown:
    an honest miss, never a guess from the first token."""
    haystack = f" {clean_title(text).lower()} "
    best = ""
    for alias in vocabulary:
        if f" {alias} " in haystack and len(alias) > len(best):
            best = alias
    return vocabulary[best] if best else ""


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def product_key(brand: str, tokens: list[str], category_key: str) -> str:
    """category_key is the MAPPED canonical key only, empty when unmapped.
    Never the classified one -- that arrives from a model call, and a key that
    depends on a model call is not reproducible (spec section 7.1)."""
    return _key("product", (brand or "").strip().lower(),
                "|".join(sorted(t.upper() for t in tokens)),
                (category_key or "").strip().lower())


def service_key(provider_key: str, service_type: str) -> str:
    return _key("service", (provider_key or "").strip().lower(),
                (service_type or "").strip().lower())
