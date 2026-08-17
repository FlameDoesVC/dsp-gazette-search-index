import pytest

from enrich.preextract import (
    extract_candidates,
    parse_count,
    parse_money,
    split_multivalue,
)
from tests.enrich.fixtures.corpus_samples import (
    BEDSPACE_TITLE,
    GAZETTE_JOB_BODY,
    POWER_SUPPLY_TITLE,
    ROOM_TITLE,
)


# --- phones -------------------------------------------------------------

def test_phone_hidden_at_the_end_of_a_spec_title():
    """'KICO METAL POWER SUPPLY 24V-5A-120W / 7884445' -- the trailing seven
    digits are a mobile number, and 24/5/120 must not be mistaken for one."""
    c = extract_candidates(POWER_SUPPLY_TITLE)
    assert c.phones == ["7884445"]


def test_phone_in_a_property_title():
    c = extract_candidates(ROOM_TITLE)
    assert c.phones == ["9223232"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Call 7994400", ["7994400"]),          # mobile, 7
        ("Viber 9483252", ["9483252"]),          # mobile, 9
        ("Tel 3323838", ["3323838"]),            # landline, 3
        ("Office 6650123", ["6650123"]),         # landline, 6
        ("+960 7994400", ["7994400"]),
        ("+9607994400", ["7994400"]),
        ("960-7994400", ["7994400"]),
        ("call 79944001", []),                   # eight digits, not a number
        ("ref 1234567", []),                     # starts with 1
        ("2026 08 17", []),                      # a date, not a phone
    ],
)
def test_phone_boundaries(text, expected):
    assert extract_candidates(text).phones == expected


def test_phone_is_deduplicated_and_ordered_by_first_appearance():
    c = extract_candidates("Call 7994400 or 9483252, again 7994400")
    assert c.phones == ["7994400", "9483252"]


# --- money --------------------------------------------------------------

@pytest.mark.parametrize(
    "text,amount,currency",
    [
        ("މަހަކު 10,750 ރުފިޔާ", 10750.0, "MVR"),
        ("-/32,632", 32632.0, "MVR"),
        ("7000/-", 7000.0, "MVR"),
        ("MVR 5,000", 5000.0, "MVR"),
        ("Rf 5,000", 5000.0, "MVR"),
        ("USD 450", 450.0, "USD"),
        ("$450", 450.0, "USD"),
        ("450 dollars", 450.0, "USD"),
    ],
)
def test_parse_money_handles_every_local_shape(text, amount, currency):
    assert parse_money(text) == (amount, currency)


def test_money_candidates_from_the_gazette_body():
    c = extract_candidates(GAZETTE_JOB_BODY)
    amounts = [m["amount"] for m in c.money]
    assert 10750.0 in amounts
    assert 4400.0 in amounts
    assert 2000.0 in amounts


def test_bare_price_in_a_title_is_a_money_candidate():
    c = extract_candidates(BEDSPACE_TITLE)
    assert 2800.0 in [m["amount"] for m in c.money]


def test_a_seven_digit_phone_is_not_offered_as_money():
    """Otherwise every listing with a contact number gets a 7,884,445 rufiyaa
    price tag."""
    c = extract_candidates(POWER_SUPPLY_TITLE)
    assert 7884445.0 not in [m["amount"] for m in c.money]


# --- units --------------------------------------------------------------

def test_units_parsed_out_of_a_compact_title():
    c = extract_candidates(POWER_SUPPLY_TITLE)
    got = {(u["value"], u["unit"]) for u in c.units}
    assert got == {(24.0, "V"), (5.0, "A"), (120.0, "W")}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("128GB storage", (128.0, "GB")),
        ("6.7 inch display", (6.7, "inch")),
        ("5000mAh battery", (5000.0, "mAh")),
        ("1.5 kW", (1.5, "kW")),
        ("750 sqft", (750.0, "sqft")),
    ],
)
def test_more_unit_shapes(text, expected):
    c = extract_candidates(text)
    assert (c.units[0]["value"], c.units[0]["unit"]) == expected


def test_a_year_is_not_a_unit_and_not_money():
    c = extract_candidates("Model year 2019 A/C unit")
    assert c.units == []


# --- emails, urls, dates ------------------------------------------------

def test_email_and_url():
    c = extract_candidates("Apply via https://forms.gle/abc123 or hr@example.gov.mv")
    assert c.emails == ["hr@example.gov.mv"]
    assert c.urls == ["https://forms.gle/abc123"]


@pytest.mark.parametrize(
    "text,iso",
    [
        ("Apply before 2026-08-31", "2026-08-31"),
        ("31 August 2026", "2026-08-31"),
        ("31/08/2026", "2026-08-31"),
        ("2026 އޯގަސްޓް 31", "2026-08-31"),
    ],
)
def test_dates_normalize_to_iso(text, iso):
    assert iso in extract_candidates(text).dates


@pytest.mark.parametrize(
    "text,iso",
    [
        ("23 އޮގަސްޓް 2026 13:00", "2026-08-23"),
        ("11 ސެޕްޓެންބަރު 2026", "2026-09-11"),
        ("6 އޮކްޓޫބަރު 2026", "2026-10-06"),
    ],
)
def test_day_first_dhivehi_dates_parse(text, iso):
    """The corpus writes day-month-year. The original pattern expected
    year-month-day and matched nothing at all."""
    assert iso in extract_candidates(text).dates


# --- normalization helpers used by the adapters -------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3 Rooms", (3, False)),
        ("1 Room", (1, False)),
        ("4 Rooms and More", (4, True)),
        ("2", (2, False)),
        ("", None),
        ("Studio", None),
    ],
)
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected


def test_split_multivalue():
    assert split_multivalue("Air Conditioning, Fans, Towels") == [
        "Air Conditioning", "Fans", "Towels"
    ]
    assert split_multivalue("Couples or Expatriates") == ["Couples", "Expatriates"]
    assert split_multivalue("") == []


# --- the block handed to the model --------------------------------------

def test_candidates_block_is_deterministic():
    from enrich.preextract import candidates_block
    c = extract_candidates(GAZETTE_JOB_BODY)
    assert candidates_block(c) == candidates_block(c)
    assert "10750" in candidates_block(c) or "10,750" in candidates_block(c)
