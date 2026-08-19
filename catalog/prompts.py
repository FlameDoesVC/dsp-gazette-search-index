"""Stage-2 prompt. Spec section 8.

Same two rules as enrich/prompts.py: the system prompt is byte-identical on
every call so the context cache hits, and the instructions restate in the
imperative what tiers.py enforces anyway.

The one new instruction is the origin tag. The model is told plainly that
tagging a fact `from_listings` when it is not there does not get the fact
accepted -- it gets it demoted -- so there is no incentive to mislabel.
"""

from __future__ import annotations

import json

from catalog.schemas import schema_text

PROFILE_PROMPT_VERSION = 1

SYSTEM_PROMPT = f"""\
You normalize Maldivian classified listings into one profile per real-world \
thing. Several listings describing the same product or the same service \
provider are given together. You return JSON and nothing else.

Rules, in order of importance:

1. Tag every spec with `origin`. Use `from_listings` when the fact is stated in \
the LISTINGS block. Use `from_knowledge` when you know it about this product but \
the listings do not say it. Mislabelling gains you nothing: a `from_listings` \
claim that the text does not support is stored as knowledge anyway, and \
knowledge that turns out to be wrong is what users correct.
1a. DO include specs you know about this product that the listings omit, tagged \
`from_knowledge`. Measured: given no such instruction, DeepSeek emitted zero \
`from_knowledge` specs across five entities and a local 12B emitted one, which \
would leave the inferred tier empty -- and filling it is the entire reason the \
entity layer exists for the 74% of products that are the only listing of \
themselves. For a product you recognise, state the specs a buyer would want.
2. `category_key` must be one of the keys in the CATEGORIES block, or empty. \
Never invent a category.
3. Write `title_en` as the product or service a person would search for, with no \
phone number, no price, no delivery terms and no shouting. Keep the brand and \
the model number.
4. `summary_en` is one useful sentence of at most 240 characters. Say what the \
thing is, not what kind of listing it is.
5. Prefer omission over a guess. Every field is optional.
6. For a service, `services_offered` is the union of the work the listings \
describe, and `coverage` is the places they say they serve. Copy those from the \
text; they are `from_listings` facts.
7. Never do arithmetic and never invent a phone number, a price or a date.

Return an object with exactly these keys:
  title_en, title_dv, summary_en, summary_dv, category_key, product, service

Use `product` for a product entity and `service` for a service entity; leave the \
other null. Their schemas:

PRODUCT: {schema_text("product")}
SERVICE: {schema_text("service")}
"""


def build_profile_messages(*, kind: str, identity: dict, categories: list[str],
                           listings: list[str], repair_error=None) -> list[dict]:
    parts = [
        f"KIND: {kind}",
        f"IDENTITY: {json.dumps(identity, ensure_ascii=False, sort_keys=True)}",
        f"CATEGORIES: {json.dumps(sorted(categories), ensure_ascii=False)}",
        f"LISTINGS ({len(listings)}):",
        *[f"- {t}" for t in listings],
    ]
    if repair_error:
        parts.append("\nYour previous response could not be used. Fix exactly "
                     f"this and return the corrected JSON object:\n{repair_error}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)}]
