import pytest
from unittest import mock

from search.extract import ocr, repair
from search.extract.local import ExtractionResult
from search.extract import transcribe


@pytest.fixture(autouse=True)
def no_ocr_cache(settings):
    """The persistent diskcache would short-circuit the mocked vision call
    after the first identical payload -- these tests exercise the pipeline,
    not the cache (which has its own tests)."""
    settings.OCR_CACHE_ENABLED = False


def _ok(*, anchor=0.9, kept=0.9, text="މަންދޫ ކައުންސިލްގެ އިދާރާ"):
    pages = [b"\x89PNG page1"]
    mocks = {}
    with mock.patch.object(ocr, "rasterize", return_value=pages) as m_raster, \
         mock.patch.object(ocr, "vision_ocr", return_value=text) as m_vision, \
         mock.patch.object(ocr, "anchor_overlap", return_value=anchor) as m_anchor, \
         mock.patch.object(repair, "repair_text", return_value=text) as m_repair, \
         mock.patch.object(repair, "skeleton_gate",
                           return_value=(text, kept)) as m_gate:
        result = transcribe.transcribe_pdf(
            b"%PDF fake", title="މަންދޫ ފުޓްސަލް",
            office="މަންދޫ ކައުންސިލް"
        )
        mocks.update(raster=m_raster, vision=m_vision, anchor=m_anchor,
                     repair=m_repair, gate=m_gate)
    return result, mocks


@pytest.mark.django_db
def test_the_pipeline_rasterizes_ocrs_repairs_gates_and_checks_anchor():
    result, m = _ok()
    assert result.status == "ok"
    assert result.method == "transcribed"
    assert result.transcribed is True
    m["vision"].assert_called_once_with(b"\x89PNG page1")
    m["anchor"].assert_called_once()


@pytest.mark.django_db
def test_a_low_anchor_score_is_rejected():
    """Measured 0% for a fabricated page. The gate is the difference between a
    transcription and a confident hallucination."""
    result, _m = _ok(anchor=0.1)
    assert result.status == "failed"
    assert "anchor" in result.error


@pytest.mark.django_db
def test_empty_ocr_is_a_failed_attempt_not_an_empty_document():
    result, _m = _ok(text="   ")
    assert result.status != "ok"


@pytest.mark.django_db
def test_a_rasterize_failure_is_reported_not_crashed():
    with mock.patch.object(ocr, "rasterize",
                           side_effect=RuntimeError("poppler down")):
        result = transcribe.transcribe_pdf(b"bad pdf")
    assert result.status == "failed"
    assert "rasterize" in result.error
