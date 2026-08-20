import pytest

from search.identifiers import (candidates, extract, looks_like_identifier,
                                value_key)

# Real strings from iulaan 408123 and its neighbours. Every case below is a
# thing the corpus actually contains, not an invented example.
DV = """
(IUL)171-Y(FBM2)/IUL/2026/146 ge dhashun
Project Number: PC-171/2026/T327
171-Y(FMB2)/IUL/2026/166
BC-171/2026/094 ge bid committee
"""
EN = """
Following the announcement number 171-Y(FBM2)/IUL/2026/146
Project Number:

PC-171/2026/T327

Announcement Number:

171-Y(FMB2)/IUL/2026/166

This decision was made by the Bid Committee in meeting number BC-171/2026/094
"""


# --------------------------------------------------------------------------
# value_key: digits in order, plus the letter multiset sorted
# --------------------------------------------------------------------------

def test_a_transposed_office_code_keys_the_same():
    """The defect this exists for: one document spells its own announcement
    number FBM2 in the scraped field and FMB2 in the body, both in the Thaana."""
    assert (value_key("171-Y(FBM2)/IUL/2026/166")
            == value_key("171-Y(FMB2)/IUL/2026/166"))


def test_a_stray_parenthesis_does_not_change_the_key():
    assert (value_key("(IUL)142-A5/142/2026/183")
            == value_key("IUL)142-A5/142/2026/183"))


def test_trailing_punctuation_does_not_change_the_key():
    """'19/2014' against '19/2014.' was 46 points of recall."""
    assert value_key("19/2014") == value_key("19/2014.")


def test_different_prefixes_do_not_collide():
    """Discarding letters entirely was the obvious simplification. BC is a bid
    committee meeting and PC is a project; they must not merge."""
    assert value_key("BC-171/2026/094") != value_key("PC-171/2026/094")


def test_different_sequence_numbers_do_not_collide():
    assert value_key("674-A/2026/46") != value_key("674-A/2026/44")


def test_the_key_is_case_insensitive():
    assert value_key("pc-171/2026/t327") == value_key("PC-171/2026/T327")


# --------------------------------------------------------------------------
# candidates: the shape filter
# --------------------------------------------------------------------------

def test_candidates_keep_every_real_shape():
    found = set(candidates(
        "674-A/2026/46 FSM-ADV/2026/171 (IUL)142-A5/142/2026/183 "
        "(IUL)179-4/1/2026/15 PC-171/2026/T327"
    ).values())
    assert len(found) == 5


def test_a_css_class_is_rejected():
    """'col-md-12' is HTML leaking into the scraped body. It has digits and a
    hyphen; requiring a slash is what excludes it."""
    assert candidates("col-md-12 col-sm-6") == {}


def test_a_url_is_rejected():
    assert candidates("www.csc.gov.mv/download/2024/84/Annex") == {}
    assert candidates("https://gazette.gov.mv/iulaan/2026/1") == {}


def test_two_phone_numbers_joined_by_a_slash_are_rejected():
    """Maldivian numbers are seven digits starting 7, 9, 3 or 6, and sellers
    write pairs. '7924894/3315555' is not a reference number."""
    assert candidates("call 7924894/3315555 now") == {}


def test_a_bare_year_or_date_is_rejected():
    assert candidates("2026 30/06/2026") == {} or "2026" not in candidates(
        "2026 30/06/2026").values()


def test_candidates_are_keyed_for_matching_and_valued_for_display():
    got = candidates("PC-171/2026/T327")
    assert list(got.values()) == ["PC-171/2026/T327"]
    assert list(got) == [value_key("PC-171/2026/T327")]


# --------------------------------------------------------------------------
# extract: the intersection
# --------------------------------------------------------------------------

def test_extract_returns_only_tokens_present_in_both_texts():
    got = {r["value_key"] for r in extract(DV, EN)}
    assert got == {value_key(x) for x in (
        "171-Y(FBM2)/IUL/2026/146", "PC-171/2026/T327",
        "171-Y(FMB2)/IUL/2026/166", "BC-171/2026/094")}


def test_extract_cannot_invent_a_number():
    """Structural, not validated: a token only in the translation never appears.
    This is what replaces the grounding validator."""
    got = {r["value_raw"] for r in extract("nothing here",
                                           "Project Number: PC-171/2026/T327")}
    assert got == set()


def test_extract_uses_the_thaana_spelling_for_display():
    """The Thaana side is the source of record. Here the two sides disagree on
    the office code, and the stored display form must follow the source."""
    rows = extract("171-Y(FBM2)/IUL/2026/166", "171-Y(FMB2)/IUL/2026/166")
    assert [r["value_raw"] for r in rows] == ["171-Y(FBM2)/IUL/2026/166"]




def test_extract_is_empty_without_a_translation():
    """96 of 125 local iulaan have no translated body. They contribute only
    their scraped number, which the command adds separately."""
    assert extract(DV, "") == []


def test_extract_deduplicates_repeated_mentions():
    rows = extract("PC-171/2026/T327 and PC-171/2026/T327",
                   "PC-171/2026/T327 again PC-171/2026/T327")
    assert len(rows) == 1


# --------------------------------------------------------------------------
# classify_kind and the query gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("q,expected", [
    ("PC-171/2026/T327", True),
    ("171-Y(FBM2)/IUL/2026/166", True),
    ("19/2014", True),
    ("iphone charger", False),
    ("samsung a15 128gb", False),
    ("", False),
    ("7924894/3315555", False),          # phones, not an identifier
    ("www.csc.gov.mv/download/2024/84", False),
])
def test_looks_like_identifier(q, expected):
    assert looks_like_identifier(q) is expected


def test_extract_reports_only_the_value_and_its_key():
    """Kind is deliberately not determined. Correlating the number to the
    document is the whole feature; naming it bought a label vocabulary, a
    proximity window and a class of mislabelling bugs for nothing a reader can
    use."""
    rows = extract("PC-171/2026/T327", "Project Number: PC-171/2026/T327")
    assert rows == [{"value_raw": "PC-171/2026/T327",
                     "value_key": value_key("PC-171/2026/T327")}]
