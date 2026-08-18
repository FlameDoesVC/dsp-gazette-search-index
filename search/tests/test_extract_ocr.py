import pytest

from search.extract.ocr import anchor_overlap, rasterize


def _make_pdf(page_count: int = 2) -> bytes:
    """A minimal valid PDF with `page_count` pages, generated with a correct
    xref so poppler's pdftoppm can render it without repair."""
    objects = [None]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")          # 1
    kids = " ".join(f"{3 + i} 0 R" for i in range(page_count))
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode()
    )                                                            # 2
    for i in range(page_count):
        objects.append(                                            # page
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
             f"/Contents {4 + i * 2} 0 R /Resources << >> >>").encode()
        )
        content = (f"BT /Helvetica 24 Tf 100 700 Td "
                   f"(Page {i + 1}) Tj ET\n").encode()
        objects.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"endstream"
        )                                                        # contents

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for n, obj in enumerate(objects[1:], start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % len(objects)
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n"
        % (len(objects), xref_at)
    )
    return bytes(out)


@pytest.fixture
def tiny_pdf_2_pages():
    return _make_pdf(2)


def test_rasterize_returns_one_png_per_page(tiny_pdf_2_pages):
    pages = rasterize(tiny_pdf_2_pages, dpi=150, first=1, last=2)
    assert len(pages) == 2
    assert all(p.startswith(b"\x89PNG") for p in pages)


def test_rasterizing_is_what_makes_the_test_honest(tiny_pdf_2_pages):
    """Vision must see pixels. Handing it a PDF with a text layer would let it
    read the embedded text and score perfectly while measuring nothing."""
    assert rasterize(tiny_pdf_2_pages, dpi=150, first=1, last=1)[0][:4] == b"\x89PNG"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("އަރިއަތޮޅު ދެކުނުބުރީ މަންދޫ ކައުންސިލްގެ އިދާރާ", 1.0),
        ("ދެކުނުބުރީ ކައުންސިލްގެ", 0.5),
        ("މިނިސްޓްރީ އޮފް ހެލްތް ޢިމާރާތުގެ މަރާމާތު", 0.0),
    ],
)
def test_anchor_overlap_scores_against_known_metadata(text, expected):
    """Title and office come from the gazette HTML and are never OCR'd, so
    this needs no reference text. It is the only metric that caught the
    fabrication: 0% for an invented page, 87% for a good one."""
    got = anchor_overlap(
        text,
        title="މަންދޫ ފުޓްސަލް",
        office="އަރިއަތޮޅު ދެކުނުބުރީ މަންދޫ ކައުންސިލްގެ އިދާރާ",
    )
    assert got == pytest.approx(expected, abs=0.35)


def test_anchor_overlap_with_no_metadata_does_not_divide_by_zero():
    assert anchor_overlap("anything", title="", office="") == 0.0


def test_short_tokens_are_ignored():
    """Two- and three-character tokens match almost anything."""
    assert anchor_overlap("ހއ", title="ހއ", office="") == 0.0


# --- the paid-call cache (step 1b) ---------------------------------------

def _reset_cache(settings, tmp_path):
    settings.OCR_CACHE_DIR = str(tmp_path)
    import search.extract.cache as cache_mod
    if cache_mod._cache is not None:
        cache_mod._cache.close()
        cache_mod._cache = None


def test_an_identical_call_is_never_paid_for_twice(settings, tmp_path):
    _reset_cache(settings, tmp_path)
    calls = []

    def _fn():
        calls.append(1)
        return {"text": "x"}

    from search.extract.cache import cached_call
    a, hit_a = cached_call("vision", "m", b"page-bytes", "p", _fn)
    b, hit_b = cached_call("vision", "m", b"page-bytes", "p", _fn)
    assert (a, b) == ({"text": "x"}, {"text": "x"})
    assert (hit_a, hit_b) == (False, True)
    assert len(calls) == 1


def test_different_bytes_miss(settings, tmp_path):
    _reset_cache(settings, tmp_path)
    from search.extract.cache import cached_call
    cached_call("vision", "m", b"page-a", "p", lambda: 1)
    _, hit = cached_call("vision", "m", b"page-b", "p", lambda: 2)
    assert hit is False


def test_a_changed_prompt_misses(settings, tmp_path):
    """A prompt edit changes the output, so it must not serve a stale hit."""
    _reset_cache(settings, tmp_path)
    from search.extract.cache import cached_call
    cached_call("vision", "m", b"page", "prompt-v1", lambda: 1)
    _, hit = cached_call("vision", "m", b"page", "prompt-v2", lambda: 2)
    assert hit is False
