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
# 'Pro+' is a different phone from 'Pro'. The glyph is dropped by tokenizing, so
# 'REDMI NOTE 15 PRO+' and 'REDMI NOTE 15 PRO' both produced NOTE-15-PRO and
# landed in one entity. Only a '+' bound to a letter is a qualifier: in
# '8+256GB' it separates RAM from storage, and in 'Case + Screen Protector' it
# is a conjunction.
_PLUS_SUFFIX = re.compile(r"(?<=[A-Za-z])\+")

# A model token carries a digit: RL-S07100C, A15, 128GB, 200W.
_MODEL_TOKEN = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-/\.]{1,23}$")
_BARE_YEAR = re.compile(r"^20\d{2}$")

# A product line followed by its number: 'iPhone 11', 'JBL 470', 'Galaxy A15'.
#
# model_tokens drops digit-only tokens because a bare number is usually a
# quantity, and that was right for '3 IN 1' and wrong for 'iPhone 11'. Measured
# on 13,356 detail-scraped For Sale listings: 8,939 (66.9%) had no discriminating
# identity, and 2,588 of those were this case alone -- 'Apple iPhone 11 Cover
# Case' has obvious identity and the rule threw it away.
#
# The trailing qualifiers are not optional. Without them 'iPhone 11 Pro Max' and
# 'iPhone 11' collapse into one entity, which is the merge the discriminating
# rule exists to prevent.
_QUALIFIER = r"(?:pro|max|plus|ultra|mini|lite|air|se)"
# The trailing lookahead rejects a number that belongs to the word AFTER it:
# 'Hisun 2-Burner Gas Stove' produced HISUN-2 without it, treating a burner count
# as a model number. A real designator is followed by a space or the end of the
# title, never by a hyphenated noun.
_LINE_NUMBER = re.compile(
    rf"\b([A-Za-z]{{3,}})\s+(\d{{1,4}})((?:\s+{_QUALIFIER})*)(?![-\w])", re.I)
# Words that take a number without naming a product: 'SET 2', 'PACK 4'.
_NOT_A_LINE = {"set", "pack", "pcs", "piece", "pieces", "box", "lot", "size",
               "qty", "unit", "units", "watt", "volt", "amp", "inch", "gen",
               "ply", "pair", "pairs", "row", "rows", "seater", "burner",
               "door", "ton", "kilo", "litre", "liter"}


def compound_tokens(text: str) -> list[str]:
    """Product-line designators of the form WORD-NUMBER[-QUALIFIER].

    Returned uppercased and hyphen-joined so they are one token to the key, and
    exempt from the document-frequency stopword filter (see
    discriminating_tokens): a compound is specific by construction, where a bare
    'PS5' is not. 'iPhone 11' shared by 200 case listings is a product family,
    and the mapped category in the entity key is what separates a case from a
    phone.
    """
    out: list[str] = []
    for match in _LINE_NUMBER.finditer(clean_title(text)):
        word, number, qualifiers = match.groups()
        if word.lower() in _NOT_A_LINE or _BARE_YEAR.match(number):
            continue
        parts = [word.upper(), number]
        for qualifier in qualifiers.split():
            # 'Note 14 Pro+ Plus' writes the same qualifier twice, and after
            # _PLUS_SUFFIX so does 'Pro+ Plus'. A repeat is emphasis, not a
            # further variant.
            if parts[-1] != qualifier.upper():
                parts.append(qualifier.upper())
        token = "-".join(parts)
        if token not in out:
            out.append(token)
    return out


# What KIND of thing the listing is, when the title names it.
#
# The entity key is brand plus designator plus mapped category, and inside a
# coarse accessory leaf that is not enough: a silicone case and a tempered glass
# protector for the same phone share all three. Measured over the 13,356 For
# Sale listings, 51 of 1,291 multi-listing product entities (4.0%) mixed kinds
# this way, 36 of them in 'Cases, Protection & Skins' alone, and it was four of
# the five precision failures on the golden set.
#
# The vocabulary is accessory nouns only, and that is what makes it safe to
# apply everywhere rather than gating it on the category. Measured coverage per
# leaf: 'Cases, Protection & Skins' 89%, 'Charger' 92%, 'Battery' 99%, 'Screen
# Protection' 82% -- and 'Mobile Phones' 2%, 'Games' 1%, 'Tablets' 3%. So the
# token fires almost always where kinds actually collide, and almost never where
# a partial hit would split a good entity. A phone listing that does say
# 'battery' or 'case' is an accessory listing filed under phones.
_KINDS = {
    "case": r"\b(case|cases|cover|covers|casing|pouch|sleeve)\b",
    "protector": r"\b(tempered\s+glass|screen\s+protector|protector|protection)\b",
    "charger": r"\b(charger|chargers|adapter|adaptor|adepter|dock)\b",
    "cable": r"\b(cable|cables|cord|wire)\b",
    "audio": r"\b(earphone|earphones|earbud|earbuds|headphone|headphones|"
             r"headset|airpod|airpods|speaker|speakers)\b",
    "battery": r"\b(battery|batteries|powerbank|power\s+bank)\b",
    "holder": r"\b(holder|stand|mount|grip)\b",
    "memory": r"\b(memory\s+card|sd\s+card|flash\s+drive|pendrive|"
              r"pen\s+drive|ssd|hdd)\b",
    "hub": r"\b(hub|splitter|converter|extension)\b",
}
_KINDS = {name: re.compile(pattern, re.I) for name, pattern in _KINDS.items()}


def kind_token(text: str) -> str:
    """The accessory kinds the title names, sorted, or "" when it names none.

    Sorted and joined rather than first-match-wins, because a bundle really is
    a third thing: 76 listings read 'Cover Case + Tempered Glass Screen
    Protector', and that is neither a case nor a protector.
    """
    found = sorted(name for name, rx in _KINDS.items() if rx.search(text or ""))
    return "-".join(found)


def clean_title(text: str) -> str:
    out = strip_phones(text or "")
    out = _PLUS_SUFFIX.sub(" PLUS", out)
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
    seen.update(compound_tokens(text))
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


def product_key(brand: str, tokens: list[str], category_key: str,
                kind: str = "") -> str:
    """category_key is the MAPPED canonical key only, empty when unmapped.
    Never the classified one -- that arrives from a model call, and a key that
    depends on a model call is not reproducible (spec section 7.1).

    `kind` is kind_token()'s output and separates a case from a protector inside
    one coarse accessory leaf. Empty for anything whose title names no accessory
    kind, which is most non-accessories, so those keys are unchanged.
    """
    return _key("product", (brand or "").strip().lower(),
                "|".join(sorted(t.upper() for t in tokens)),
                (category_key or "").strip().lower(),
                (kind or "").strip().lower())


def service_key(provider_key: str, service_type: str) -> str:
    return _key("service", (provider_key or "").strip().lower(),
                (service_type or "").strip().lower())


# A real model designator carries letters AND digits: SQ905, T200, SK-319,
# QUEST-2, A15. A bare unit value does not identify anything -- '256GB' and '2A'
# are specs that thousands of listings share.
_HAS_LETTER_AND_DIGIT = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)")
_BARE_UNIT = re.compile(
    r"^\d+(?:\.\d+)?(?:W|V|A|GB|TB|MB|MAH|KG|ML|CM|MM|L|INCH|K)$", re.I)


def strong_tokens(tokens: list[str]) -> list[str]:
    """The subset of `tokens` that actually designates a model."""
    return [t for t in tokens
            if _HAS_LETTER_AND_DIGIT.match(t) and not _BARE_UNIT.match(t)]


def identity_confidence(brand: str, tokens: list[str]) -> float:
    """How much the identity can be trusted, in [0, 1].

    This gates whether inferred specs reach DocumentSpec (spec section 9), so
    the grading is measured rather than assumed. Of the 2,745 For Sale listings
    that match no known brand, 87.9% still carry a strong model designator
    (`SQ905`, `SK-319`, `QUEST-2`) and only 12.1% offer nothing but a bare unit.

    A model designator therefore outranks a brand: `SQ905` is close to a unique
    key, while `Samsung` with no model is thousands of different products. A
    both-or-nothing rule scored the 87.9% at 0.5 and put them below the 0.7
    floor, which would have left facet coverage almost exactly where the entity
    layer found it.
    """
    strong = strong_tokens(tokens)
    if brand and strong:
        return 0.9
    if strong:
        return 0.8
    if brand and tokens:
        return 0.7
    if brand:
        return 0.6
    return 0.4          # bare units only: weakest identity that still resolves


# --------------------------------------------------------------------------
# Discriminating power. A token that appears in hundreds of listings names a
# platform, a capacity or a marketing claim; it cannot identify one product.
# --------------------------------------------------------------------------

_STOPWORD_CACHE: set[str] | None = None


def identity_stopwords(*, refresh: bool = False) -> set[str]:
    """Model tokens too common to identify anything, derived from the corpus.

    Measured over the 7,105 For Sale listings: PS5 appears in 426 of them, PS4
    in 266, 5G in 214, 256GB in 163, IP66 in 66. A real model designator
    behaves the opposite way -- WH-1000XM5 in 2, SQ905 in 1, G06 in 1. The
    document-frequency distribution is p50=1, p75=3, p95=13, so the default
    threshold of 15 removes every platform and capacity word while leaving
    about 97% of distinct tokens usable.

    Derived, not curated, for the same reason the taxonomy is: a hand-written
    blocklist would need an entry for every new console and storage size.
    """
    global _STOPWORD_CACHE
    if _STOPWORD_CACHE is not None and not refresh:
        return _STOPWORD_CACHE

    from collections import Counter

    from django.conf import settings

    from search.models import SearchDocument

    threshold = getattr(settings, "CATALOG_IDENTITY_STOPWORD_DF", 15)
    df: Counter = Counter()
    qs = (SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
          .filter(source="ibay", doc_type="shopping")
          .only("title_en", "attrs"))
    for doc in qs.iterator(chunk_size=500):
        path = doc.attrs.get("category_path") or []
        if not path or str(path[0]) != "For Sale":
            continue
        for token in set(model_tokens(doc.title_en)):
            df[token] += 1
    _STOPWORD_CACHE = {t for t, n in df.items() if n >= threshold}
    return _STOPWORD_CACHE


def clear_stopword_cache() -> None:
    global _STOPWORD_CACHE
    _STOPWORD_CACHE = None


_COMPOUND = re.compile(r"^[A-Z]{3,}-\d{1,4}(?:-[A-Z]+)*$")


def discriminating_tokens(tokens: list[str], stopwords: set[str]) -> list[str]:
    """Strong tokens that are also rare enough to mean something.

    A compound designator is exempt from the frequency filter. It is specific by
    construction -- 'IPHONE-11-PRO' names one product line where a bare 'PS5'
    names a platform -- so frequency says nothing useful about it, and applying
    the filter would discard the 2,588 listings this rule exists to recover.
    """
    return [t for t in strong_tokens(tokens)
            if _COMPOUND.match(t) or t.upper() not in stopwords]
