from search.lang import normalize as n


def test_fili_set_is_the_eleven_measured_codepoints():
    assert len(n.FILI) == 11
    assert "ަ" in n.FILI   # abafili
    assert "ް" in n.FILI   # sukun
    assert "ޥ" not in n.FILI   # a consonant, not a fili


def test_strip_fili_produces_the_consonant_skeleton():
    assert n.strip_fili("ހަކަތަ") == "ހކތ"
    assert n.strip_fili("ހިކަތި") == "ހކތ"


def test_strip_fili_leaves_latin_alone():
    assert n.strip_fili("iPhone 13") == "iPhone 13"


def test_strip_html_removes_markup_but_keeps_text():
    html = '<td><p dir="RTL"><strong>އަސާސީ މުސާރަ:</strong></p></td>'
    out = n.strip_html(html)
    for token in ("<td>", "dir=", "strong", "RTL"):
        assert token not in out
    assert "އަސާސީ" in out


def test_strip_html_passes_plain_text_through():
    assert n.strip_html("just text") == "just text"


def test_normalize_collapses_whitespace_and_casefolds_latin():
    assert n.normalize_text("  Hello   WORLD \n") == "hello world"


def test_normalize_maps_arabic_indic_digits_to_ascii():
    assert n.normalize_text("١٢٣") == "123"


def test_normalize_strips_zero_width_characters():
    assert n.normalize_text("a\u200bb") == "ab"


def test_normalize_dv_keeps_fili_by_default():
    assert n.normalize_dv("ހަކަތަ") == "ހަކަތަ"


def test_normalize_is_idempotent():
    once = n.normalize_text("  Hello   WORLD ١٢٣ ")
    assert n.normalize_text(once) == once
