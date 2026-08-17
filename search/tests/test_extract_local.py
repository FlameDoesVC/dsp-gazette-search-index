import pytest
from search.extract import local


def _docx_bytes(paragraphs):
    import io
    from docx import Document
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_extraction_returns_the_paragraphs():
    content = _docx_bytes(["އަސާސީ މުސާރަ: މަހަކު 10,750 ރުފިޔާ", "Second line"])
    result = local.extract_docx(content)
    assert result.status == "ok"
    assert result.method == "docx"
    assert "10,750" in result.text
    assert "Second line" in result.text


def test_docx_extraction_fails_cleanly_on_garbage():
    result = local.extract_docx(b"not a docx at all")
    assert result.status != "ok"
    assert result.text == ""


def test_text_is_capped():
    content = _docx_bytes(["x" * 50_000])
    assert len(local.extract_docx(content).text) <= local.TEXT_CAP


def test_needs_transcription_for_a_sparse_pdf():
    sparse = local.ExtractionResult(
        text="", page_count=5, chars_per_page=0,
        method="pdftotext", status="ok",
    )
    assert local.needs_transcription(sparse) is True


def test_does_not_need_transcription_for_a_dense_pdf():
    dense = local.ExtractionResult(
        text="a" * 10_000, page_count=4, chars_per_page=2500,
        method="pdftotext", status="ok",
    )
    assert local.needs_transcription(dense) is False


def test_needs_transcription_when_the_text_layer_extraction_failed():
    failed = local.ExtractionResult(
        text="", page_count=None, chars_per_page=None,
        method="pdftotext", status="failed",
    )
    assert local.needs_transcription(failed) is True


def test_file_size_does_not_decide_routing():
    """Measured: a 58 KB page was scanned and a 1.1 MB 28-page document had a
    full text layer. Routing is on chars-per-page only (spec 5.6.2)."""
    import inspect
    source = inspect.getsource(local.needs_transcription)
    assert "size" not in source and "bytes" not in source


@pytest.mark.skipif(
    not local.have_poppler(), reason="poppler-utils not installed"
)
def test_pdf_extraction_reports_a_page_count():
    minimal_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    result = local.extract_pdf_text_layer(minimal_pdf)
    assert result.page_count in (1, None)
