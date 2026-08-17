"""Query expansion. Spec 6.5.

Cheapest-first, short-circuiting: normalize, then the exact keyboard decode,
then phonetic transliteration, then curated aliases. The translation call is
deliberately absent here -- it belongs in P4 once the enrichment client exists,
and the three lexical paths cover most queries without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from search.lang.keymap import decode_keys
from search.lang.normalize import normalize_text, strip_fili
from search.lang.script import ENGLISH, KEYS, LATIN_DV, THAANA, detect_query_script
from search.lang.translit import (
    translit_dv_to_latin,
    translit_latin_to_dv_variants,
)

_PHRASE = re.compile(r'"([^"]+)"')
# Word characters minus underscore, plus the Thaana fili range (U+07A6-U+07B0).
# Fili are combining marks (Mn) that `\w` does not match; without them a Thaana
# word splits into bare consonants at every vowel mark.
_TOKEN = re.compile(r"(?:[^\W_]|[\u07A6-\u07B0])+", re.UNICODE)

MAX_TERMS_PER_SIDE = 32


@dataclass(slots=True)
class QueryPlan:
    raw: str
    lang: str = ENGLISH
    response_lang: str = "en"
    terms_en: list[str] = field(default_factory=list)
    terms_dv: list[str] = field(default_factory=list)
    terms_latin: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)


def _dedupe(items: list[str], cap: int = MAX_TERMS_PER_SIDE) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
        if len(out) >= cap:
            break
    return out


def _alias_expansions(tokens: list[str]) -> list[str]:
    from search.models import QueryAlias

    rows = QueryAlias.objects.filter(term__in=tokens, is_active=True)
    out: list[str] = []
    for row in rows:
        out.extend(row.expands_to or [])
    return out


def build_query_plan(q: str, *, use_aliases: bool = True) -> QueryPlan:
    raw = q or ""
    phrases = [p.strip() for p in _PHRASE.findall(raw) if p.strip()]
    without_phrases = _PHRASE.sub(" ", raw)

    lang, labelled = detect_query_script(without_phrases)
    plan = QueryPlan(raw=raw, lang=lang, phrases=phrases)
    if not labelled:
        return plan

    plan.response_lang = "en" if lang == ENGLISH else "dv"

    # Keyboard space is case-sensitive (lowercase `a` is abafili, uppercase `A`
    # is aabaafili), so the decode must run on the original-case token, never
    # the casefolded one. `labelled` is casefolded; tokenize the raw query to
    # recover case. The token count matches because normalization only
    # casefolds and collapses whitespace.
    raw_tokens = _TOKEN.findall(without_phrases)

    en: list[str] = []
    dv: list[str] = []
    latin: list[str] = []

    for (token, label), raw_token in zip(labelled, raw_tokens):
        if label == THAANA:
            dv.append(token)
            dv.append(strip_fili(token))
            latin.append(translit_dv_to_latin(token))
        elif label == KEYS:
            decoded = decode_keys(raw_token)
            if decoded:
                dv.append(decoded)
                dv.append(strip_fili(decoded))
                latin.append(translit_dv_to_latin(decoded))
        elif label == LATIN_DV:
            latin.append(token)
            en.append(token)   # phonetic words can live in English text too
            dv.extend(translit_latin_to_dv_variants(token))
        else:
            en.append(token)
            latin.append(token)

    if use_aliases:
        all_tokens = [t for t, _ in labelled]
        for expansion in _alias_expansions(all_tokens):
            norm = normalize_text(expansion)
            if not norm:
                continue
            for sub, sub_label in detect_query_script(norm)[1]:
                if sub_label == THAANA:
                    dv.append(sub)
                    dv.append(strip_fili(sub))
                else:
                    en.append(sub)
                    latin.append(sub)

    plan.terms_en = _dedupe(en)
    plan.terms_dv = _dedupe(dv)
    plan.terms_latin = _dedupe(latin)
    return plan
