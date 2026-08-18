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
