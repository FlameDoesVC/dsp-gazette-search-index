import pytest

from search.rank_signals import stuffing_penalty


@pytest.mark.parametrize("title,expect", [
    ("Apple iPhone 15 Pro Max 256GB", 0.0),
    ("AC Gas Leakage AC- Water Leakage. Maintenance. Water. Leakage. Gas.", 0.4),
    ("USB to USB Cable AM TO AM Male to Male USB-A TO USB-A 1.5M", 0.5),
])
def test_repetition_is_penalised_proportionally(title, expect):
    assert stuffing_penalty(title) == pytest.approx(expect, abs=0.15)


def test_a_short_clean_title_is_never_penalised():
    assert stuffing_penalty("iPhone 13") == 0.0


def test_a_legitimately_long_title_is_not_penalised_for_length_alone():
    """'7-in-1 USB C Hub Type C to USB 3.0 HDMI SD/TF Card Reader' is 20
    tokens and honest. Penalise repetition, not length."""
    assert stuffing_penalty(
        "7-in-1 USB C Hub Type C to USB 3.0 2.0 HDMI SD TF Card Reader"
    ) < 0.2
