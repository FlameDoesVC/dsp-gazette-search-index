import pytest
from django.utils import translation

from search.vocab import canonical, label


@pytest.mark.parametrize("raw,key", [
    ("Full-time", "full_time"), ("Full time", "full_time"),
    ("FULL TIME", "full_time"), ("  Permanent  ", "permanent"),
])
def test_spelling_variants_canonicalise_to_one_key(raw, key):
    """The consistency failure this exists to prevent: a per-document
    translator would give each variant its own Dhivehi spelling."""
    assert canonical(raw) == key


def test_label_resolves_in_english():
    with translation.override("en"):
        assert label("position_type", "Full time") == "Full-time"


def test_label_resolves_in_dhivehi():
    with translation.override("dv"):
        out = label("position_type", "Permanent")
        assert out and out != "Permanent", "dv catalog entry missing"


def test_an_unknown_value_falls_back_to_itself_not_to_blank():
    assert label("position_type", "Seasonal") == "Seasonal"


def test_an_unknown_field_is_passed_through():
    assert label("nonexistent_field", "x") == "x"
