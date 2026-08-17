from search.lang import translit as t


def test_thaana_to_latin_produces_readable_output():
    assert t.translit_dv_to_latin("ކުޔޔަހ") == "kuyyah"


def test_transliteration_is_many_to_one_so_variants_are_generated():
    """ށ and ސ both reach `sh`/`s`; a single string would lose recall."""
    variants = t.translit_latin_to_dv_variants("sh")
    assert len(variants) > 1
    assert any("ށ" in v for v in variants)


def test_variant_generation_is_bounded():
    """Combinatorial explosion would make long queries unusable."""
    variants = t.translit_latin_to_dv_variants("bahattaden")
    assert 0 < len(variants) <= t.MAX_VARIANTS


def test_long_vowels_map_to_doubled_latin():
    assert "aa" in t.translit_dv_to_latin("ސާ")


def test_empty_input_is_safe():
    assert t.translit_dv_to_latin("") == ""
    assert t.translit_latin_to_dv_variants("") == []


def test_latin_input_passes_through_dv_to_latin_unchanged():
    assert t.translit_dv_to_latin("iphone") == "iphone"
