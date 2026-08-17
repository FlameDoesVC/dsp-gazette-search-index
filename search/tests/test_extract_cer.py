import pytest
from search.extract.cer import char_error_rate, passes_gate


def test_identical_text_has_zero_error():
    assert char_error_rate("ހަކަތަ", "ހަކަތަ") == 0.0


def test_completely_different_text_has_high_error():
    assert char_error_rate("ހަކަތަ", "xyz") >= 1.0


def test_one_substitution_in_ten_characters():
    assert char_error_rate("abcdefghij", "abcdefghiX") == pytest.approx(0.1)


def test_whitespace_differences_are_ignored():
    assert char_error_rate("ހަކަތަ  ސަރުކާރު", "ހަކަތަ ސަރުކާރު") == 0.0


def test_empty_reference_is_undefined_and_returns_one():
    assert char_error_rate("", "anything") == 1.0


def test_gate_accepts_low_error(settings):
    settings.TRANSCRIBE_MAX_CER = 0.15
    assert passes_gate(0.05) is True


def test_gate_rejects_high_error(settings):
    """Tesseract's published ~69% Thaana accuracy sits far above any workable
    gate. Text that is confidently wrong is worse than absent text (spec 5.6)."""
    settings.TRANSCRIBE_MAX_CER = 0.15
    assert passes_gate(0.31) is False
