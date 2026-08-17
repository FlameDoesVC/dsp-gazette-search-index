import pytest
from search.lang import keymap as k


@pytest.mark.parametrize("keys,thaana", [
    ("migotawq", "މިގޮތައް"),
    ("liyegenq", "ލިޔެގެން"),
    ("wewqcewq", "އެއްޗެއް"),
    ("walawikumq", "އަލައިކުމް"),
    ("wawqsalAmq", "އައްސަލާމް"),
])
def test_decodes_known_pairs(keys, thaana):
    assert k.decode_keys(keys) == thaana


def test_the_mapping_is_a_bijection():
    assert len(k.KEY_TO_THAANA) == len(k.THAANA_TO_KEY)
    for key, th in k.KEY_TO_THAANA.items():
        assert k.THAANA_TO_KEY[th] == key


def test_round_trips_every_mapped_codepoint():
    for th in k.THAANA_TO_KEY:
        assert k.decode_keys(k.encode_keys(th)) == th


def test_detects_keyboard_space():
    assert k.looks_like_keys("migotawq") is True


@pytest.mark.parametrize("phrase", [
    "kuyyah", "dhinun", "firihen", "bahattaden", "vikkanee",
    "kuyyah dhinun", "firihen kudhin bahattaden",
    "Halaalukuvefa hunna", "vazeefaa ah edhey form",
])
def test_does_not_misread_phonetic_latin_dhivehi(phrase):
    """The decisive test. `text_latin` holds phonetic Latin Dhivehi like these
    real corpus titles; misreading them as keyboard space is silent corruption
    (spec 6.4). Note `kuyyah` decodes to ކުޔޔަހ under a naive check -- only the
    every-consonant-carries-a-fili rule rejects it."""
    assert k.looks_like_keys(phrase) is False


@pytest.mark.parametrize("phrase", [
    "washing", "machine", "delivery", "apartment",
    "washing machine", "iphone 13 pro", "apartment for rent",
])
def test_does_not_misread_plain_english(phrase):
    assert k.looks_like_keys(phrase) is False


def test_decode_returns_none_for_undecodable_input():
    assert k.decode_keys("iphone!") is None
    assert k.decode_keys("hello @ world") is None


def test_decode_preserves_spaces_and_digits():
    assert k.decode_keys("migotawq 13") == "މިގޮތައް 13"


def test_every_corpus_codepoint_is_mapped():
    """Guards against an incomplete table. If this fails, the unmapped
    codepoints are printed -- fill them in from the standard layout."""
    unmapped = sorted(
        ch for ch in k.CORPUS_CODEPOINTS if ch not in k.THAANA_TO_KEY
    )
    assert not unmapped, f"unmapped: {[f'{c}=U+{ord(c):04X}' for c in unmapped]}"
