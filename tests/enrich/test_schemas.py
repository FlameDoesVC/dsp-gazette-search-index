import json

import pytest
from pydantic import ValidationError

from enrich.schemas import (
    ATTRS_FOR_TYPE,
    Allowance,
    Compensation,
    JobAttrs,
    NewsAttrs,
    Occupancy,
    PropertyAttrs,
    ShoppingAttrs,
    schema_text,
)


def test_every_doc_type_has_a_schema():
    assert set(ATTRS_FOR_TYPE) == {"job", "property", "shopping", "news"}


def test_all_fields_are_optional():
    """Spec 5.2 layer 5: a null field is correct behavior, a plausible
    invention is a bug. Every model must construct from nothing."""
    for model in ATTRS_FOR_TYPE.values():
        model()   # must not raise


def test_compensation_defaults_to_unlisted():
    c = Compensation()
    assert c.salary_state == "unlisted"
    assert c.completeness == "none"
    assert c.allowances == []
    assert c.pension_applies is False


def test_allowance_rejects_unknown_basis():
    with pytest.raises(ValidationError):
        Allowance(kind="attendance", label_raw="x", amount=1.0, basis="per_fortnight")


def test_occupancy_rejects_unknown_unit_kind():
    with pytest.raises(ValidationError):
        Occupancy(unit_kind="houseboat")


def test_schema_text_is_json_and_stable():
    """The prompt pastes this verbatim, so it must be deterministic -- a
    dict ordering change would silently invalidate DeepSeek's context cache
    on every call and triple the input cost."""
    a = schema_text("job")
    b = schema_text("job")
    assert a == b
    json.loads(a)


def test_job_attrs_accepts_a_realistic_payload():
    j = JobAttrs(
        role="Administrative Officer",
        employer="Ministry of Example",
        grade="GS3",
        compensation=Compensation(
            basic_salary=10750,
            allowances=[
                Allowance(kind="attendance", label_raw="ހާޒިރީ އެލަވަންސް",
                          amount=4400, basis="fixed_monthly"),
            ],
            pension_applies=True,
            salary_state="listed",
            completeness="full",
        ),
        apply_methods=[{"kind": "email", "value": "hr@example.gov.mv"}],
    )
    assert j.compensation.basic_salary == 10750
    assert j.apply_methods[0].kind == "email"


def test_property_and_shopping_and_news_construct():
    PropertyAttrs(listing_kind="rent", occupancy=Occupancy(unit_kind="room",
                  rooms_offered=1, rooms_total=3, is_shared=True))
    ShoppingAttrs(condition="used", brand="Apple",
                  specs=[{"key_raw": "voltage", "value_num": 24, "unit": "V"}])
    NewsAttrs(office="Ministry of Example", is_tender=True)
