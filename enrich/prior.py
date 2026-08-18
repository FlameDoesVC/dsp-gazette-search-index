"""Deterministic classification prior. Spec 5.3.

doc_type comes from the same model call as extraction, but the call is given a
prior derived from data the source already labelled. The model may override it
only at confidence >= 0.8; otherwise the prior wins.

This same table is the complete fallback when no provider is reachable, which
is why it lives here and not inside the prompt builder.
"""

from __future__ import annotations

DOC_TYPES = ("job", "property", "shopping", "news")

# news is the default sink: anything that does not classify confidently lands
# here. There is deliberately no 'unknown' type and no quarantine queue.
DEFAULT_DOC_TYPE = "news"

CONFIDENCE_FLOOR = 0.8

IULAAN_TYPE_MAP = {
    "ވަޒީފާގެ ފުރުޞަތު": "job",
    "Job Opportunity": "job",
    "ކުއްޔަށް ދިނުން": "property",       # letting
    "For Rent": "property",
    "Letting": "property",
    "ކުއްޔަށް ހިފުން": "property",       # seeking to rent
    "Need to Rent": "property",
    "Wanted to Rent": "property",
    "ޢާންމު މަޢުލޫމާތު": "news",
    "Public Information": "news",
    "ދެންނެވުން": "news",
    "ބީލަން": "news",                    # bids -- a future `tender` type (3.2)
    "Tender": "news",
    "Bids": "news",
    "ނީލަން": "news",                    # auctions
    "Auction": "news",
    "މަސައްކަތް": "news",                # works
    "Work": "news",
    "Works": "news",
    "ތަމްރީނު": "news",                  # training
    "Training": "news",
    "ގަންނަން ބޭނުންވާ ތަކެތި": "news",   # items wanted
    "Items wanted": "news",
    "Items to buy": "news",
    "މުބާރާތް": "news",                  # competitions
}

IBAY_CATEGORY_MAP = {
    "Jobs": "job",
    "Housing & Real Estate": "property",
    "Announcements & Events": "news",
    "For Sale": "shopping",
    "Services": "shopping",
    "Wanted": "shopping",
    "Free Stuff": "shopping",
    "Business Opportunities": "shopping",
}


def prior_for(source: str, *, iulaan_type: str = "", categories=()) -> str:
    if source == "gazette":
        return IULAAN_TYPE_MAP.get((iulaan_type or "").strip(), DEFAULT_DOC_TYPE)
    if source == "ibay":
        for name in categories:
            hit = IBAY_CATEGORY_MAP.get((name or "").strip())
            if hit:
                return hit
        return DEFAULT_DOC_TYPE
    return DEFAULT_DOC_TYPE


def apply_confidence_gate(
    prior: str, model_type: str, confidence: float
) -> tuple[str, bool]:
    """Returns (chosen_type, was_overridden).

    The gate exists because the data is genuinely mixed: iBay listings like
    'Cleaning work daily worker' sit under shopping-ish categories and are
    really jobs. It is set at 0.8 so an override needs the model to be sure,
    not merely to have an opinion.
    """
    if model_type not in DOC_TYPES:
        return prior, False
    if model_type == prior:
        return prior, False
    if confidence >= CONFIDENCE_FLOOR:
        return model_type, True
    return prior, False
