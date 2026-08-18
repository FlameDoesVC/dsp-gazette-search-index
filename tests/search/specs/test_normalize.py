import pytest

from search.models import SpecKey
from search.specs.normalize import normalize_value, parse_bool


@pytest.fixture
def brand(db):
    return SpecKey.objects.create(
        key="brand", label_en="Brand", datatype="enum",
        value_aliases={"Apple (iPhone)": "Apple", "SAMSUNG": "Samsung"},
    )


@pytest.fixture
def facilities(db):
    return SpecKey.objects.create(key="room_facilities", label_en="Facilities",
                                  datatype="enum")


@pytest.mark.django_db
def test_aliases_collapse(brand):
    assert normalize_value(brand, "Apple (iPhone)") == ["Apple"]
    assert normalize_value(brand, "SAMSUNG") == ["Samsung"]


@pytest.mark.django_db
def test_a_multi_value_string_becomes_independent_values(facilities):
    """'Air Conditioning, Fans, Towels' appears 1,137 times and must become
    three checkboxes, not one. Spec 4.4."""
    assert normalize_value(facilities, "Air Conditioning, Fans, Towels") == [
        "Air Conditioning", "Fans", "Towels"
    ]


@pytest.mark.django_db
def test_aliases_apply_after_splitting(brand):
    assert normalize_value(brand, "Apple (iPhone), Nokia") == ["Apple", "Nokia"]


@pytest.mark.django_db
def test_empty_and_whitespace_yield_nothing(brand):
    assert normalize_value(brand, "") == []
    assert normalize_value(brand, "   ") == []


@pytest.mark.django_db
def test_a_value_too_long_for_the_column_is_dropped_not_truncated(brand):
    """A truncated brand is a wrong brand."""
    assert normalize_value(brand, "x" * 500) == []


@pytest.mark.parametrize(
    "raw,expected",
    [("Yes", True), ("yes", True), ("true", True), ("1", True), ("Available", True),
     ("No", False), ("false", False), ("0", False), ("None", False),
     ("maybe", None), ("", None)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected
