import pytest

from catalog.tiers import classify_origin
from enrich.preextract import extract_candidates

UNION = ("Samsung Galaxy A15 128GB blue. 6.5 inch display. "
         "Free delivery Male' Hulhumale'.")
CAND = extract_candidates(UNION)


def test_a_string_present_in_the_listings_is_grounded():
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="blue", union_text=UNION,
                           candidates=CAND) == "grounded"


def test_a_number_present_in_the_candidate_set_is_grounded():
    assert classify_origin(claimed="from_listings", value_num=128,
                           value_text="", union_text=UNION,
                           candidates=CAND) == "grounded"


def test_a_claim_the_text_does_not_support_is_demoted_not_dropped():
    """The behaviour the whole coverage argument rests on: the validator
    classifies instead of deleting."""
    assert classify_origin(claimed="from_listings", value_num=5000,
                           value_text="", union_text=UNION,
                           candidates=CAND) == "inferred"


def test_a_knowledge_claim_is_inferred_even_when_the_text_agrees():
    """Honesty is rewarded but not upgraded: the model said it came from
    knowledge, so it is inferred."""
    assert classify_origin(claimed="from_knowledge", value_num=128,
                           value_text="", union_text=UNION,
                           candidates=CAND) == "inferred"


def test_a_very_short_string_cannot_be_grounded_by_substring_luck():
    """Two characters match almost anything; enrich/validate.py sets the same
    floor for the same reason."""
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="A1", union_text=UNION,
                           candidates=CAND) == "inferred"


def test_token_overlap_grounds_a_reordered_phrase():
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="Galaxy A15 Samsung", union_text=UNION,
                           candidates=CAND) == "grounded"


# The real listing text, plurals intact. It was briefly singularised to make the
# assertion below pass, which hid the actual defect: sellers write plurals and
# models write singulars, so the exemption needs plural folding, not a friendlier
# fixture.
SERVICE_UNION = ("We fix door locks, smart locks, door, door frame, door "
                 "closer, fans and heaters. Male' and Hulhumale'. 9663178")
SERVICE_CAND = extract_candidates(SERVICE_UNION)


def test_a_paraphrased_service_stays_grounded():
    """Measured across two providers: both emit 'Door lock repair' for 'We fix
    door locks'. That is summarising, not fabricating, and demoting it would
    caveat all 1,747 service entities while they are accurate."""
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="Door lock repair",
                           union_text=SERVICE_UNION, candidates=SERVICE_CAND,
                           key_raw="service_offered") == "grounded"


def test_an_invented_service_is_still_demoted():
    """The lower floor must not become no floor."""
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="Marine engine overhaul",
                           union_text=SERVICE_UNION, candidates=SERVICE_CAND,
                           key_raw="service_offered") == "inferred"


def test_a_product_spec_keeps_the_strict_floor():
    """The exemption is per key, not global: a spec value is copied, not
    paraphrased, so battery_mah keeps the numeric candidate-set test."""
    assert classify_origin(claimed="from_listings", value_num=5000,
                           value_text="", union_text=SERVICE_UNION,
                           candidates=SERVICE_CAND,
                           key_raw="battery_mah") == "inferred"


@pytest.mark.parametrize("value", [
    "Door lock repair",        # 'locks' in the listing, 'lock' in the output
    "Smart lock repair",
    "Door closer repair",
    "Fan repair",              # 'fans' -> 'fan'; scores 0.0 unfolded
    "Heater repair",           # 'heaters' -> 'heater'; scores 0.0 unfolded
    "Door frame repair",
])
def test_every_paraphrase_both_providers_produced_stays_grounded(value):
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text=value, union_text=SERVICE_UNION,
                           candidates=SERVICE_CAND,
                           key_raw="service_offered") == "grounded"


@pytest.mark.parametrize("value", [
    "Marine engine overhaul",
    "Aircon gas refill",
    "Stainless steel fabrication",
])
def test_a_service_the_listings_never_mention_is_demoted(value):
    """The lower floor must not become no floor."""
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text=value, union_text=SERVICE_UNION,
                           candidates=SERVICE_CAND,
                           key_raw="service_offered") == "inferred"


def test_plural_folding_does_not_collapse_short_or_double_s_words():
    from catalog.tiers import _fold_plural
    assert _fold_plural("gas glass is as") == "gas glass is as"
    assert _fold_plural("locks heaters") == "lock heater"
