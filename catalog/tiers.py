"""The origin classifier. Spec section 8.

The model tags every spec `from_listings` or `from_knowledge`, and this module
checks the tag instead of trusting it. A failed `from_listings` claim is demoted
to `inferred`, never dropped -- which is the difference between this stage and
stage 1, where an unsupported value is deleted (enrich/validate.py).

The primitives are imported from enrich/validate.py rather than reimplemented,
so "grounded" means exactly the same thing in both stages.
"""

from __future__ import annotations

from enrich.preextract import Candidates
from enrich.validate import (MIN_GROUNDED_LEN, STRING_OVERLAP_FLOOR,
                             normalize_for_match, token_overlap)


# Keys whose values the model is expected to NORMALIZE rather than copy, so an
# exact-substring test is the wrong question. Measured across two providers on
# real service entities: both emit 'Door lock repair' for a listing reading
# 'We fix door locks, smart locks, door frame, door closer'. That is faithful
# summarising, not fabrication, and demoting it would put a "may not be
# accurate" caveat on all 1,747 service entities while they are in fact
# accurate. enrich/validate.py carries the same exemption list for the same
# reason (_UNGROUNDED_STRING_FIELDS).
NORMALIZED_KEYS = {"service_offered", "coverage", "rate_basis", "call_out",
                   "shop_visit", "availability", "brand"}
# The floor for those keys. Lower than STRING_OVERLAP_FLOOR because a paraphrase
# shares content words but not phrasing; still high enough that an invented
# service the listings never mention fails.
NORMALIZED_OVERLAP_FLOOR = 0.5


def _fold_plural(text: str) -> str:
    """Crude singular/plural fold before comparing normalized values.

    Necessary, not cosmetic. Sellers write plurals and models write singulars:
    'We fix door locks, ... fans and heaters' against 'Door lock repair',
    'Fan repair', 'Heater repair'. Without folding those score 0.333, 0.0 and
    0.0 and every one is demoted, which defeats the exemption above entirely.
    With folding they score 0.667, 0.5 and 0.5, while 'Marine engine overhaul',
    'Aircon gas refill' and 'Stainless steel fabrication' -- services these
    listings never mention -- stay at 0.0. All nine measured cases classify
    correctly at the 0.5 floor.

    Trailing 's' only, on tokens over three characters, skipping '-ss'. A real
    stemmer would be a dependency and a worse fit: this is comparing two short
    label strings, not indexing prose.
    """
    out = []
    for token in normalize_for_match(text).split():
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.append(token)
    return " ".join(out)


def _folded_overlap(value: str, union_text: str) -> float:
    a, b = set(_fold_plural(value).split()), set(_fold_plural(union_text).split())
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def classify_origin(*, claimed: str, value_num, value_text: str,
                    union_text: str, candidates: Candidates,
                    key_raw: str = "") -> str:
    if claimed != "from_listings":
        return "inferred"

    if key_raw in NORMALIZED_KEYS and value_text:
        return ("grounded"
                if _folded_overlap(value_text, union_text) >= NORMALIZED_OVERLAP_FLOOR
                else "inferred")

    if value_num is not None:
        formatted = (str(int(value_num)) if float(value_num).is_integer()
                     else str(value_num))
        return ("grounded" if formatted in candidates.all_numeric_strings()
                else "inferred")

    value = (value_text or "").strip()
    if len(value) < MIN_GROUNDED_LEN:
        return "inferred"
    haystack = normalize_for_match(union_text)
    if normalize_for_match(value) in haystack:
        return "grounded"
    if token_overlap(value, union_text) >= STRING_OVERLAP_FLOOR:
        return "grounded"
    return "inferred"
