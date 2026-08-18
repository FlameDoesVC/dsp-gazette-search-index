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

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def canonical(value: str) -> str:
    """'Full-time', 'Full time', 'FULL TIME' -> 'full_time'."""
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
}

VOCABULARIES = {
    "position_type": POSITION_TYPE,
    "job_category": JOB_CATEGORY,
    "condition": CONDITION,
    "listing_kind": LISTING_KIND,
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
