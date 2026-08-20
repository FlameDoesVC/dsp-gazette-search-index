"""Prompt construction. Spec 5.2, 5.3.

Two rules govern everything here:

1. The system prompt is byte-identical on every call, and DeepSeek's context
   cache makes it cost $0.007/M instead of $0.22/M -- but only if the prefix
   never varies. Nothing per-document may leak into it.

   Measured 2026-08-20: 11,163 characters, about 3,100 tokens. It was ~800 when
   this was written and the schema block has grown since. It is worth knowing
   how lopsided that makes a call: the median iBay shopping user message is 574
   characters, so roughly 93% of the input tokens are the cached prefix and the
   cache is doing nearly all the work of keeping this affordable.
2. The instructions repeat, in the imperative, the two rules the grounding
   validator enforces anyway: select numbers from the candidate list, and do
   no arithmetic. Telling the model reduces the number of records that have to
   be repaired; the validator is what makes it true.
"""

from __future__ import annotations

import json

from enrich.preextract import Candidates, candidates_block
from enrich.schemas import ATTRS_FOR_TYPE, schema_text

# Bump when the instructions or the schemas change in a way that would produce
# different output. Spec 4.2: a bump re-enriches iBay, and deliberately does
# NOT backfill gazette (spec 5.7).
PROMPT_VERSION = 2

_ALL_SCHEMAS = json.dumps(
    {t: json.loads(schema_text(t)) for t in sorted(ATTRS_FOR_TYPE)},
    sort_keys=True,
    ensure_ascii=False,
)

SYSTEM_PROMPT = f"""\
You extract structured data from Maldivian classified listings and government \
gazette notices. You return JSON and nothing else.

Rules, in order of importance:

1. NEVER write a number that does not appear in the CANDIDATES block of the \
user message. Phone numbers, salaries, prices, voltages and dates have already \
been extracted from the source text for you. Your job is to choose which \
candidate belongs in which field and to label it. If the right number is not \
in CANDIDATES, leave the field null.
2. NEVER perform arithmetic. Do not total allowances, do not compute take-home \
pay, do not convert currencies, do not average a range. Report line items \
exactly as stated. Arithmetic is done elsewhere.
3. NEVER overwrite a value in the SCRAPED block. Those are ground truth. You \
may fill a field the SCRAPED block leaves empty; you may not contradict it.
4. Prefer null over a guess. Every field is optional. A null field is correct \
behavior. An invented field is a defect.
5. Copy strings from the source rather than paraphrasing them. Every string you \
emit must be traceable to the input text.
6. `salary_state` is `negotiable` ONLY when the source actually says the salary \
is negotiable or open to discussion. A listing that simply does not mention pay \
is `unlisted`.
7. Classify `doc_type` as one of job, property, shopping, news. A PRIOR is given \
in the user message. Override it only if you are confident; report your \
confidence honestly in `doc_type_confidence` between 0 and 1. If nothing else \
fits, use news.
8. Write `summary_en` and `summary_dv` as one useful sentence of at most 240 \
characters each. For a news document the summary is the entire product, so make \
it say what actually happened, not what kind of document it is. Leave the \
Dhivehi fields empty if the source has no Dhivehi.
9. `required_documents` lists what an applicant must attach -- ID copy, \
accredited certificates, CV, reference letters, police report. One short \
string each, copied from the source. It is not the same as `qualifications`, \
which describes the person; this describes the paperwork.

Return an object with exactly these keys:
  doc_type, doc_type_confidence, canonical_title_en, canonical_title_dv,
  summary_en, summary_dv, keywords, attrs

`attrs` must match the JSON schema for the doc_type you chose:

{_ALL_SCHEMAS}
"""


def build_messages(
    *,
    source: str,
    doc_type_prior: str,
    title: str,
    body: str,
    candidates: Candidates,
    scraped: dict,
    repair_error: str | None = None,
) -> list[dict]:
    parts = [
        f"SOURCE: {source}",
        f"PRIOR: {doc_type_prior}",
        f"SCRAPED: {json.dumps(scraped, ensure_ascii=False, sort_keys=True)}",
        f"CANDIDATES: {candidates_block(candidates)}",
        f"TITLE: {title}",
        "BODY:",
        body,
    ]
    if repair_error:
        parts.append(
            "\nYour previous response could not be used. Fix exactly this and "
            f"return the corrected JSON object:\n{repair_error}"
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]
