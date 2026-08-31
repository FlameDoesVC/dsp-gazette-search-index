"""Canonical keys and translatable labels for closed attribute vocabularies.

Twenty strings across the whole corpus. They are translated once in a gettext
catalog rather than per document, for two reasons: cost is the obvious one,
and consistency is the real one -- a per-document translator emitting
"Full-time" and "Full time" produces two Dhivehi spellings of one concept.

Canonicalise first, then look up. Same principle as SpecKey.value_aliases
(spec 4.4).
"""

from __future__ import annotations

import re

from django.utils import translation
from django.utils.translation import gettext_lazy as _

_NON_ALNUM = re.compile(r"[^a-z0-9ހ-޿]+")


def canonical(value: str) -> str:
    """'Full-time', 'Full time', 'FULL TIME' -> 'full_time'.

    Thaana is preserved (it has no case), so a Dhivehi value like
    `ބީލަން` canonicalises to itself and can be a catalog key.
    """
    return _NON_ALNUM.sub("_", (value or "").strip().lower()).strip("_")


# key -> translatable label. Add a row when enrichment produces a new value;
# the admin promotion queue in P7 is the model for spotting them.
POSITION_TYPE = {
    "permanent": _("Permanent"),
    "full_time": _("Full-time"),
    "part_time": _("Part-time"),
    "contract": _("Contract"),
    "temporary": _("Temporary"),
}

JOB_CATEGORY = {
    "medical": _("Medical"),
    "logistics": _("Logistics"),
    "teaching": _("Teaching"),
    "administration": _("Administration"),
    "engineering": _("Engineering"),
}

CONDITION = {
    "new": _("New"),
    "used": _("Used"),
    "refurbished": _("Refurbished"),
}

LISTING_KIND = {
    "rent": _("For rent"),
    "sale": _("For sale"),
    "wanted": _("Wanted"),
}

# Gazette announcement types are a fixed vocabulary (the IulaanType table), so
# their labels live in the catalog, not in machine translation. The keys are
# the Dhivehi canonical forms; English variants are canonicalised into them
# before lookup (P9 task 7 step 3).
ANNOUNCEMENT_TYPE = {
    "ވަޒީފާގެ ފުރުޞަތު": _("Job Opportunity"),
    "ކުއްޔަށް ދިނުން": _("For Rent"),
    "ކުއްޔަށް ހިފުން": _("Wanted to Rent"),
    "ބީލަން": _("Tender"),
    "ނީލަން": _("Auction"),
    "މަސައްކަތް": _("Works"),
    "ތަމްރީނު": _("Training"),
    "ގަންނަން ބޭނުންވާ ތަކެތި": _("Items Wanted"),
    "ޢާންމު މަޢުލޫމާތު": _("Public Information"),
    "ދެންނެވުން": _("Notice"),
}

# English variants of IulaanType names collapse into the Dhivehi canonical, so
# the announcement_type facet has one bucket per concept instead of two (the
# language-duplicate rows: 'ވަޒީފާގެ ފުރުޞަތު' / 'Job Opportunity'). P9 task 7
# step 1: canonicalise in the facet layer, never merge rows.
ANNOUNCEMENT_TYPE_CANONICAL = {
    "Job Opportunity": "ވަޒީފާގެ ފުރުޞަތު",
    "For Rent": "ކުއްޔަށް ދިނުން",
    "Letting": "ކުއްޔަށް ދިނުން",
    "Need to Rent": "ކުއްޔަށް ހިފުން",
    "Wanted to Rent": "ކުއްޔަށް ހިފުން",
    "Public Information": "ޢާންމު މަޢުލޫމާތު",
    "Auction": "ނީލަން",
    "Tender": "ބީލަން",
    "Bids": "ބީލަން",
    "Work": "މަސައްކަތް",
    "Works": "މަސައްކަތް",
    "Items wanted": "ގަންނަން ބޭނުންވާ ތަކެތި",
    "Items to buy": "ގަންނަން ބޭނުންވާ ތަކެތި",
    "Training": "ތަމްރީނު",
}


def canonical_announcement_type(name: str) -> str:
    """Collapse English/other variants into the Dhivehi canonical form."""
    name = (name or "").strip()
    return ANNOUNCEMENT_TYPE_CANONICAL.get(name, name)

VOCABULARIES = {
    "position_type": POSITION_TYPE,
    "job_category": JOB_CATEGORY,
    "condition": CONDITION,
    "listing_kind": LISTING_KIND,
    "announcement_type": ANNOUNCEMENT_TYPE,
}


def label(field: str, value: str) -> str:
    """Localised label, or the raw value when the vocabulary has no entry.

    Falling back to the raw value keeps an unrecognised term visible rather
    than blanking the card -- an unknown value is a prompt to extend the
    catalog, not an error.
    """
    table = VOCABULARIES.get(field)
    if not table:
        return value
    return str(table.get(canonical(value), value))


# Which closed-vocab fields each doc_type's card carries. Drives
# `annotate_labels` below -- add a row here when a new card field joins
# VOCABULARIES.
FIELDS_BY_DOC_TYPE = {
    "job": ("position_type", "job_category"),
    "shopping": ("condition",),
    "property": ("listing_kind",),
    "news": ("announcement_type",),
}


def bilingual_label(field: str, value: str) -> tuple[str, str]:
    """(english, dhivehi) for one closed-vocab value, independent of whatever
    locale happens to be active. Mirrors title_en/title_dv: both sides are
    always sent, and the frontend's own per-element Bidi rendering picks."""
    with translation.override("en"):
        en = label(field, value)
    with translation.override("dv"):
        dv = label(field, value)
    return en, dv


def annotate_labels(doc_type: str, card: dict) -> dict:
    """Add `<field>_label_en` / `<field>_label_dv` for every closed-vocab
    field this doc_type's card carries.

    Resolved here, at request time, rather than baked into `card` at
    enrichment time: enrichment runs once under whatever locale happens to be
    active at build time (never a real language choice), so a label frozen
    then would be wrong for every request in the other language forever. The
    catalog itself only needs compiling once (`compilemessages`, part of
    run_pipeline) -- this just looks it up per request, the same way
    `annotate_time` computes deadline_state per request instead of freezing
    it at index time.
    """
    fields = FIELDS_BY_DOC_TYPE.get(doc_type, ())
    if not fields:
        return card
    out = dict(card)
    for field_name in fields:
        value = card.get(field_name)
        if value:
            en, dv = bilingual_label(field_name, value)
            out[f"{field_name}_label_en"] = en
            out[f"{field_name}_label_dv"] = dv
    return out


def annotate_free_text(doc_type: str, card: dict) -> dict:
    """Add the Dhivehi side of a job card's recurring free-text fields --
    role, employer, qualifications, required_documents, allowance names,
    apply labels -- from whatever `translate_card_vocab` has already put in
    the cache.

    These are not a closed vocabulary, so there is no catalog to compile;
    they are looked up from TranslationCache (one batched query per card,
    never a provider call -- a request is not the place to pay for a
    translation, only to read one that's already there). A string this
    command has not translated yet simply has no `_dv` sibling here, and the
    frontend falls back to the English value it already renders today.
    """
    if doc_type != "job":
        return card

    from core.translate import cached_translations

    qualifications = card.get("qualifications") or []
    required_documents = card.get("required_documents") or []
    allowances = (card.get("compensation") or {}).get("allowances") or []
    apply_methods = card.get("apply_methods") or []

    texts = set(qualifications) | set(required_documents)
    texts |= {a.get("label_raw") for a in allowances if a.get("label_raw")}
    texts |= {m.get("label_en") for m in apply_methods
             if m.get("label_en") and not m.get("label_dv")}
    role = card.get("role")
    if role:
        texts.add(role)
    employer = card.get("employer")
    if employer:
        texts.add(employer)

    translations = cached_translations([t for t in texts if t])
    if not translations:
        return card

    out = dict(card)
    if role in translations:
        out["role_dv"] = translations[role]
    if employer in translations:
        out["employer_dv"] = translations[employer]
    if qualifications:
        out["qualifications_dv"] = [translations.get(q, "") for q in qualifications]
    if required_documents:
        out["required_documents_dv"] = [
            translations.get(d, "") for d in required_documents
        ]
    if allowances:
        comp = dict(card["compensation"])
        comp["allowances"] = [
            {**a, "label_dv": translations.get(a.get("label_raw", ""), "")}
            for a in allowances
        ]
        out["compensation"] = comp
    if apply_methods:
        out["apply_methods"] = [
            {**m, "label_dv": m.get("label_dv") or translations.get(m.get("label_en", ""), "")}
            for m in apply_methods
        ]
    return out
