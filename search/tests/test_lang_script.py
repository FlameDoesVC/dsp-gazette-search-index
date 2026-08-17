from search.lang import script as s


def test_thaana_is_detected():
    assert s.detect_script("ވަޒީފާ") == "dv-Thaa"


def test_keyboard_space_is_detected_before_phonetic():
    assert s.detect_script("migotawq") == "dv-Keys"


def test_phonetic_latin_dhivehi_is_detected():
    for token in ("kuyyah", "bahattaden", "firihen", "vikkanee"):
        assert s.detect_script(token) == "dv-Latn"


def test_plain_english_is_detected():
    for token in ("washing", "apartment", "delivery"):
        assert s.detect_script(token) == "en"


def test_digits_and_model_numbers_are_english():
    assert s.detect_script("13") == "en"


def test_labels_are_per_token_not_per_query():
    """Real queries are mixed: `iPhone 13 vikkan` is half English (spec 6.1)."""
    dominant, tokens = s.detect_query_script("iPhone 13 vikkan")
    labels = dict(tokens)
    assert labels["iphone"] == "en"
    assert labels["vikkan"] == "dv-Latn"
    assert dominant in {"en", "dv-Latn"}


def test_dominant_label_of_pure_thaana_query():
    dominant, _ = s.detect_query_script("ވަޒީފާގެ ފުރުޞަތު")
    assert dominant == "dv-Thaa"


def test_empty_query_is_english():
    assert s.detect_query_script("") == ("en", [])
