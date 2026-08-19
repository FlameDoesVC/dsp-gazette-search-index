import pytest

from search.contacts import PHONE_RE, primary_phone, strip_phones


def test_the_title_wins_over_the_description():
    """89.3% of listings carry a phone, usually in both places, and the title
    number is the one the seller wants called."""
    assert primary_phone("Fridge repair 7438649", "call 9663178") == "7438649"


def test_falls_back_to_the_description():
    assert primary_phone("Fridge repair", "call 9663178") == "9663178"


def test_no_phone_is_empty_string_not_none():
    assert primary_phone("Fridge repair", "") == ""


@pytest.mark.parametrize("text,expected", [
    ("Call 7438649", "7438649"),
    ("Tel: 7989696", "7989696"),
    ("+960 7438649", "7438649"),
    ("+9607438649", "7438649"),
    ("9445252 , 9519132 , 9654041", "9445252"),
    ("landline 3325555", "3325555"),
    ("6621234", "6621234"),
])
def test_real_corpus_forms(text, expected):
    assert primary_phone(text) == expected


@pytest.mark.parametrize("text", [
    "Model RL-S07100C",          # not a phone
    "12345678",                  # eight digits
    "5551234",                   # leading 5 is not a Maldivian prefix
    "Price 1450",
])
def test_things_that_are_not_phones(text):
    assert primary_phone(text) == ""


def test_a_longer_digit_run_is_not_a_phone():
    """The '445' tail of a longer run must not match."""
    assert primary_phone("serial 79386491234") == ""


def test_strip_phones_leaves_a_readable_title():
    title = "Refrigerator, Aircon Repair & Service. Home Service Call.. 7438649"
    out = strip_phones(title)
    assert "7438649" not in out
    assert out == "Refrigerator, Aircon Repair & Service. Home Service Call.."


def test_strip_phones_collapses_the_separator_it_leaves_behind():
    """The trailing label punctuation goes too: 'Tel:' with nothing after it
    reads as broken markup, not as a label."""
    assert strip_phones("Green Lion Charger | Tel: 7989696") == \
        "Green Lion Charger | Tel"


def test_preextract_uses_the_same_pattern():
    """One regex, or the entity provider key and the candidate list disagree."""
    from enrich.preextract import _PHONE
    assert _PHONE is PHONE_RE


def test_strip_phones_removes_the_ampersand_sellers_leave_behind():
    """Real corpus form: 'FREE DELIVERY | &7776828' rendered as a title ending
    in '| &' after the number went."""
    assert strip_phones("Green Lion Charger | FREE DELIVERY | &7776828") == \
        "Green Lion Charger | FREE DELIVERY"


def test_strip_phones_keeps_a_sentence_ending_period():
    """'.' is not debris. This is why the strip set is explicit rather than a
    catch-all punctuation class."""
    assert strip_phones("Aircon Repair & Service. 7438649") == \
        "Aircon Repair & Service."
