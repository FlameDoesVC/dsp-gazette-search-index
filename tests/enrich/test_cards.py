import pytest

from enrich.cards import build_card, capacity_display, rent_display, spec_chips
from enrich.schemas import (
    Allowance, Compensation, JobAttrs, NewsAttrs, Occupancy,
    PropertyAttrs, ShoppingAttrs, Spec,
)


# --- jobs ---------------------------------------------------------------

def test_job_card_leads_with_role_employer_salary():
    attrs = JobAttrs(
        role="Administrative Officer", employer="Ministry of Example", grade="GS3",
        compensation=Compensation(
            basic_salary=10750,
            allowances=[Allowance(kind="attendance", label_raw="ހާޒިރީ",
                                  amount=4400, basis="fixed_monthly")],
            pension_applies=True, salary_state="listed", completeness="full",
        ),
        deadline="2026-08-31",
        apply_methods=[{"kind": "form", "value": "https://forms.gle/x"},
                       {"kind": "email", "value": "hr@example.gov.mv"}],
    )
    card = build_card("job", attrs, base={"source": "gazette",
                                          "detail_source": "attachment"})
    assert card["role"] == "Administrative Officer"
    assert card["employer"] == "Ministry of Example"
    assert card["salary_display"] == "MVR 10,750 / month"
    assert card["salary_state"] == "listed"
    assert card["net_estimate"]["value"] == pytest.approx(14397.50)
    assert card["net_estimate"]["is_floor"] is False
    assert card["apply_kinds"] == ["form", "email"]
    assert card["detail_source"] == "attachment"


def test_job_card_carries_the_line_items_so_the_client_can_recompute():
    """Spec 4.3.2: the working-days control recomputes client-side from the
    same pure logic; nothing is re-fetched."""
    attrs = JobAttrs(compensation=Compensation(
        basic_salary=8000,
        allowances=[Allowance(kind="attendance", label_raw="daily", amount=100,
                              basis="per_day")],
        pension_applies=True, salary_state="listed", completeness="full"))
    card = build_card("job", attrs, base={})
    assert card["compensation"]["allowances"][0]["basis"] == "per_day"
    assert card["compensation"]["allowances"][0]["amount"] == 100


def test_job_card_stores_the_raw_deadline_and_no_computed_state():
    """Spec 8: nothing time-dependent in `card`. A gazette document is written
    once and never reprocessed, so a frozen `deadline_state` would advertise a
    closed vacancy as open indefinitely."""
    card = build_card("job", JobAttrs(deadline="2026-08-31"), base={})
    assert card["deadline"] == "2026-08-31"
    assert "deadline_state" not in card
    assert "days_left" not in card
    assert "is_open" not in card


def test_job_card_omits_a_net_estimate_that_would_restate_basic():
    card = build_card("job", JobAttrs(compensation=Compensation(
        basic_salary=10000, salary_state="listed", completeness="basic_only")),
        base={})
    assert card["net_estimate"] is None


@pytest.mark.parametrize(
    "state,expected",
    [("negotiable", "Negotiable"), ("unlisted", "Unlisted")],
)
def test_job_card_salary_display_never_null(state, expected):
    card = build_card("job", JobAttrs(compensation=Compensation(salary_state=state)),
                      base={})
    assert card["salary_display"] == expected


# --- property -----------------------------------------------------------

@pytest.mark.parametrize(
    "occ,expected",
    [
        (Occupancy(unit_kind="whole_unit", rooms_total=3), "Whole unit, 3 rooms"),
        (Occupancy(unit_kind="room", rooms_offered=1, rooms_total=3, is_shared=True),
         "1 room of 3, shared"),
        (Occupancy(unit_kind="bed_space", beds_offered=2, is_shared=True),
         "Bed space, 2 available, shared"),
        (Occupancy(unit_kind="guest_house", max_occupants=4),
         "Guest house room, up to 4"),
        (Occupancy(unit_kind="whole_unit"), "Whole unit"),
        (Occupancy(unit_kind="land"), "Land"),
    ],
)
def test_capacity_display_table(occ, expected):
    assert capacity_display(occ) == expected


def test_one_room_of_three_never_renders_as_three_bedrooms():
    """The concrete failure spec 8.2 exists to prevent."""
    attrs = PropertyAttrs(
        occupancy=Occupancy(unit_kind="room", rooms_offered=1, rooms_total=3,
                            is_shared=True),
        bedrooms=3,
    )
    card = build_card("property", attrs, base={"price": 7000, "currency": "MVR"})
    assert card["capacity_display"] == "1 room of 3, shared"
    assert card["is_shared"] is True


@pytest.mark.parametrize(
    "price,currency,period,expected",
    [
        (7000, "MVR", "month", "MVR 7,000 / month"),
        (450, "USD", "month", "USD 450 / month"),
        (300, "MVR", "day", "MVR 300 / day"),
        (None, "MVR", "month", "Price on request"),
    ],
)
def test_rent_display(price, currency, period, expected):
    assert rent_display(price, currency, period) == expected


def test_property_card_marks_an_inferred_currency():
    card = build_card("property", PropertyAttrs(currency_inferred=True),
                      base={"price": 7000, "currency": "MVR"})
    assert card["currency_inferred"] is True


# --- shopping -----------------------------------------------------------

def test_spec_chips_are_capped_and_formatted():
    specs = [Spec(key_raw="voltage", value_num=24, unit="V"),
             Spec(key_raw="current", value_num=5, unit="A"),
             Spec(key_raw="power", value_num=120, unit="W"),
             Spec(key_raw="colour", value_text="black")]
    assert spec_chips(specs) == ["24V", "5A", "120W"]


def test_shopping_card():
    card = build_card(
        "shopping",
        ShoppingAttrs(condition="Used", brand="Apple",
                      specs=[Spec(key_raw="storage", value_num=128, unit="GB")]),
        base={"title": "iPhone 13", "price": 9500, "currency": "MVR",
              "hero_image": "https://x/1.jpg", "image_count": 4,
              "seller_name": "Ali", "seller_is_premium": True},
    )
    assert card["price_display"] == "MVR 9,500"
    assert card["condition"] == "Used"
    assert card["spec_chips"] == ["128GB"]
    assert card["seller_is_premium"] is True


# --- news ---------------------------------------------------------------

def test_news_card_is_four_things_and_nothing_else():
    """Spec 8.4: icon, title, excerpt, link out."""
    card = build_card(
        "news", NewsAttrs(office="Ministry of Example", announcement_type="ބީލަން",
                          is_tender=True),
        base={"source": "gazette", "title": "Tender for X",
              "summary": "The ministry invites bids for X.",
              "external_url": "https://gazette.gov.mv/iulaan/1",
              "attachment_count": 2, "published_at": "2026-08-01"},
    )
    assert card["source"] == "gazette"
    assert card["title"] == "Tender for X"
    assert card["summary"] == "The ministry invites bids for X."
    assert card["external_url"].startswith("https://")
    assert card["attachment_count"] == 2
    assert set(card) == {
        "source", "title", "summary", "office", "announcement_type",
        "announcement_type_label",
        "published_at", "external_url", "attachment_count", "is_tender",
    }


def test_every_card_carries_its_source():
    for doc_type, attrs in [("job", JobAttrs()), ("property", PropertyAttrs()),
                            ("shopping", ShoppingAttrs()), ("news", NewsAttrs())]:
        card = build_card(doc_type, attrs, base={"source": "ibay"})
        assert card["source"] == "ibay", doc_type


def test_no_card_embeds_an_icon_path():
    """Spec 4.3.3: card stores the source key, not the icon URL. Embedding a
    path would duplicate the same string across 71,445 rows and make
    re-skinning a source a full reindex."""
    for doc_type, attrs in [("job", JobAttrs()), ("property", PropertyAttrs()),
                            ("shopping", ShoppingAttrs()), ("news", NewsAttrs())]:
        card = build_card(doc_type, attrs, base={"source": "ibay"})
        assert not any("icon" in k or "svg" in str(v) for k, v in card.items())
