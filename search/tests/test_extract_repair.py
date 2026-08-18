import pytest

from search.extract.repair import skeleton_gate


def test_a_re_voweled_word_is_accepted():
    """Same consonants, added fili -- exactly what the repairer is for."""
    out, kept = skeleton_gate("ކއުންސިލްގެ", "ކައުންސިލްގެ")
    assert out == "ކައުންސިލްގެ"
    assert kept == 1.0


def test_a_substituted_word_is_rejected_back_to_the_ocr():
    """The measured failure: `އދ` became `އާދަމުގެފާނމަންދޫ`. Different
    skeleton, so the OCR's own word is kept."""
    out, kept = skeleton_gate("އދ", "އާދަމުގެފާނމަންދޫ")
    assert out == "އދ"
    assert kept == 0.0


def test_an_inserted_word_does_not_invalidate_the_page():
    """Alignment is by difflib on the skeleton sequence. A strict positional
    zip fails on a single off-by-one and discards a whole good page."""
    src = "ކއުންސިލްގެ އދާރާ"
    rep = "ކައުންސިލްގެ އައު އިދާރާ"
    out, kept = skeleton_gate(src, rep)
    assert "ކައުންސިލްގެ" in out
    assert "އިދާރާ" in out
    assert "އައު" not in out          # inserted, never in the OCR


def test_latin_and_digits_pass_through_untouched():
    src = "ނަންބަރު:351/351/2026/41(IUL)"
    out, _ = skeleton_gate(src, src)
    assert "351/351/2026/41(IUL)" in out


def test_empty_input_is_safe():
    assert skeleton_gate("", "") == ("", 1.0)


def test_the_gate_never_introduces_a_word_absent_from_the_ocr():
    """The property the whole rung rests on. Every Thaana word in the output
    must be a re-voweling of a Thaana word in the OCR."""
    import re
    from search.lang.normalize import strip_fili
    src = "ޅޮސްމަޑުލު ދެކުނުބުރި ހތދ ކއުންސިލްގެ"
    rep = "މާޅޮސްމަޑުލު ދެކުނުބުރީ ހިތާދޫ ކައުންސިލްގެ"
    out, _ = skeleton_gate(src, rep)
    W = re.compile(r"[ހ-޿]+")
    src_skeletons = {strip_fili(w) for w in W.findall(src)}
    assert all(strip_fili(w) in src_skeletons for w in W.findall(out))
