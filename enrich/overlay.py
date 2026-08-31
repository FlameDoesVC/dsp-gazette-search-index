"""Fold EnrichedRecord into a DocumentDraft. Spec 3.3, 5.2.

Called by search.indexing between the adapter and the upsert. Three rules:

- A record whose content_hash does not match the draft describes text that no
  longer exists, and applying it would attach last month's extraction to this
  month's listing. Ignored.
- `failed` records are ignored; `needs_review` records are applied. A conflict
  on one field is not a reason to discard the other nine.
- This function must not touch stale_marked_at. reindex clears it, and it must
  still be set when reindex runs.
"""

from __future__ import annotations

import logging

from enrich.cards import build_card
from enrich.models import EnrichedRecord
from enrich.schemas import ATTRS_FOR_TYPE
from search.adapters.base import DocumentDraft

logger = logging.getLogger(__name__)

_USABLE = ("ok", "needs_review")


# Keys the SOURCE owns outright. Enrichment may never write these, whatever it
# says, because they are not judgements about the text -- they are facts about
# where the listing sits in the source's own catalogue, which no reading of the
# text can establish.
#
# category_path is the case that proved it. Of 250 records where the model
# filled it, 250 differed from the adapter and not one matched: it invents a
# taxonomy of its own ('Electronics/Audio Equipment', 'Education/Online
# Learning', 'Websites'). That is not merely untidy. in_scope() requires
# path[0] to be 'For Sale' or 'Services', so an invented root drops the document
# out of entity resolution altogether, and _mapped_key() and _is_service() read
# the same field.
#
# Filtering only EMPTY answers was not enough: a wrong answer is worse than a
# blank one, and this field cannot have a right one.
_ADAPTER_OWNED = frozenset({"category_path", "specs_raw"})


def _unanswered(value) -> bool:
    """True when the model left the field alone.

    Deliberately not `not value`: `negotiable: False` and a numeric 0 are
    answers, and treating them as absent would silently drop the only two
    values a boolean facet can take.
    """
    return value is None or value == "" or value == [] or value == {}


def apply_enrichment(draft: DocumentDraft) -> DocumentDraft:
    record = (
        EnrichedRecord.objects
        .filter(source=draft.source, source_key=draft.source_key)
        .only("doc_type", "status", "content_hash", "canonical_title_en",
              "canonical_title_dv", "summary_en", "summary_dv", "attrs", "keywords")
        .first()
    )
    if record is None or record.status not in _USABLE:
        return draft
    if draft.content_hash and record.content_hash != draft.content_hash:
        logger.debug("enrichment hash mismatch for %s:%s", draft.source, draft.source_key)
        return draft

    draft.doc_type = record.doc_type or draft.doc_type

    if record.canonical_title_en:
        draft.title_en = record.canonical_title_en
    if record.canonical_title_dv:
        draft.title_dv = record.canonical_title_dv
    if record.summary_en:
        draft.summary_en = record.summary_en
    if record.summary_dv:
        draft.summary_dv = record.summary_dv

    model_cls = ATTRS_FOR_TYPE.get(draft.doc_type, ATTRS_FOR_TYPE["news"])
    try:
        attrs_model = model_cls(**(record.attrs or {}))
    except Exception:                      # already validated at write time
        logger.warning("unparseable stored attrs for %s:%s",
                       draft.source, draft.source_key)
        return draft

    # Only the keys the model actually FILLED. model_dump() returns every field
    # in the schema including untouched defaults, so merging it wholesale let a
    # default overwrite real adapter data -- `category_path: []` blanked a
    # source's own breadcrumb on 7,553 documents, which took in_scope(),
    # _mapped_key() and _is_service() out at once and halved entity
    # resolution: 22,869 links down to 11,098, missed from 33.4% to 67.7%.
    #
    # This is rule 3 of the prompt applied on our side rather than trusted to
    # the model: scraped fields win, the model may fill a blank and never
    # overwrite one. False and 0 are real answers and must survive -- only
    # None, "", [] and {} count as "did not answer".
    enriched = {k: v for k, v in attrs_model.model_dump().items()
                if k not in _ADAPTER_OWNED and not _unanswered(v)}
    draft.attrs = {**draft.attrs, **enriched}

    # The only figure comparable across ads that itemize differently, so it is
    # what the salary facet and the salary sort read. Spec 4.3.2, 7.
    if draft.doc_type == "job":
        from enrich.compensation import estimate_net
        est = estimate_net(attrs_model.compensation)
        draft.attrs["estimated_net_min"] = (
            round(est.value, 2) if est else attrs_model.compensation.basic_salary
        )

    base = dict(draft.card)
    base.setdefault("source", draft.source)
    base.setdefault("title", draft.title_en or draft.title_dv)
    base.setdefault("summary", draft.summary_en or draft.summary_dv)
    base.setdefault("external_url", draft.url)
    base.setdefault("price", draft.price)
    base.setdefault("currency", draft.currency)
    base.setdefault("location", draft.location)
    base.setdefault("published_at",
                    draft.published_at.isoformat() if draft.published_at else None)
    # A regex over the source text, not another model call: an address is
    # either there verbatim or it isn't, and _job_card only reaches for this
    # when the model's own apply_methods came back with no email at all.
    base.setdefault("body_text", f"{draft.text_en}\n{draft.text_dv}")
    draft.card = build_card(draft.doc_type, attrs_model, base=base)

    # Aliases and synonyms are search surface, not display surface, so they go
    # into the vectors and stay out of the card.
    if record.keywords:
        latin = [k for k in record.keywords if not _is_thaana(k)]
        thaana = [k for k in record.keywords if _is_thaana(k)]
        if latin:
            draft.text_en = f"{draft.text_en}\n{' '.join(latin)}".strip()
        if thaana:
            draft.text_dv = f"{draft.text_dv}\n{' '.join(thaana)}".strip()

    return draft


def _is_thaana(s: str) -> bool:
    return any("ހ" <= c <= "޿" for c in s)
