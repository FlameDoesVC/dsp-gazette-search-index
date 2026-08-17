import datetime as dt

import pytest

from search.extract.dates import parse_dv_datetime, parse_dv_month


@pytest.mark.parametrize(
    "token,month",
    [
        # Spellings that actually occur in the corpus.
        ("އޮގަސްޓް", 8),
        ("ސެޕްޓެންބަރު", 9),
        ("އޮކްޓޫބަރު", 10),
        # Competing conventions for the same months. Nasal `ން` vs `މް`, and a
        # trailing `ު`. Both are correct Dhivehi; exact matching cannot win.
        ("ސެޕްޓެމްބަރު", 9),
        ("ސެޕްޓެމްބަރ", 9),
        ("އޮކްޓޯބަރު", 10),
        ("ނޮވެންބަރު", 11),
        ("ނޮވެމްބަރ", 11),
        ("ޑިސެންބަރު", 12),
        ("ޑިސެމްބަރ", 12),
        ("ޖެނުއަރީ", 1),
        ("ފެބްރުއަރީ", 2),
        ("މާރިޗު", 3),
        ("މާރޗް", 3),
        ("އޭޕްރީލް", 4),
        ("އެޕްރީލް", 4),
        ("މެއި", 5),
        ("މޭ", 5),
        ("ޖޫން", 6),
        ("ޖުލައި", 7),
        ("އޯގަސްޓް", 8),
    ],
)
def test_every_month_spelling_the_corpus_can_produce(token, month):
    assert parse_dv_month(token) == month


def test_an_unknown_token_is_none_not_a_guess():
    assert parse_dv_month("ބީލަން") is None
    assert parse_dv_month("") is None


def test_the_real_deadline_format():
    """'23 އޮގަސްޓް 2026 13:00' -- day, month, year, time. The old regex
    expected year-month-day and silently matched nothing."""
    got = parse_dv_datetime("23 އޮގަސްޓް 2026 13:00")
    assert got.date() == dt.date(2026, 8, 23)
    assert (got.hour, got.minute) == (13, 0)


def test_a_date_with_the_time_in_a_separate_field():
    got = parse_dv_datetime("16 އޮގަސްޓް 2026", time_str="14:12")
    assert got.date() == dt.date(2026, 8, 16)
    assert (got.hour, got.minute) == (14, 12)


def test_a_date_with_no_time_defaults_to_end_of_day():
    """A deadline of '17 August' has not passed at 09:00 on the 17th."""
    got = parse_dv_datetime("17 އޮގަސްޓް 2026")
    assert got.date() == dt.date(2026, 8, 17)
    assert (got.hour, got.minute) == (23, 59)


def test_midnight_is_preserved_and_not_treated_as_missing():
    """'17 އޮގަސްޓް 2026 00:00' occurs in the corpus and means midnight,
    not 'no time given'."""
    got = parse_dv_datetime("17 އޮގަސްޓް 2026 00:00")
    assert (got.hour, got.minute) == (0, 0)


def test_the_result_is_timezone_aware():
    from django.utils import timezone
    got = parse_dv_datetime("23 އޮގަސްޓް 2026 13:00")
    assert timezone.is_aware(got)


@pytest.mark.parametrize(
    "bad", ["", "not a date", "23 2026", "99 އޮގަސްޓް 2026", "23 އޮގަސްޓް 1026"]
)
def test_garbage_returns_none_rather_than_a_wrong_date(bad):
    assert parse_dv_datetime(bad) is None


def test_a_latin_month_also_parses():
    """Some offices publish in English."""
    got = parse_dv_datetime("23 August 2026 13:00")
    assert got.date() == dt.date(2026, 8, 23)
