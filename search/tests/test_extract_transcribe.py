import base64
import pytest
from search.extract import transcribe


def test_request_sends_the_pdf_natively_not_as_images():
    """Claude takes PDFs directly, which deletes the rasterization step and its
    RAM cost entirely (spec 5.6.1)."""
    params = transcribe.build_request(b"%PDF-1.4 fake")
    blocks = params["messages"][0]["content"]
    doc = next(b for b in blocks if b["type"] == "document")
    assert doc["source"]["media_type"] == "application/pdf"
    assert doc["source"]["type"] == "base64"
    assert base64.b64decode(doc["source"]["data"]) == b"%PDF-1.4 fake"


def test_request_is_deterministic():
    assert transcribe.build_request(b"x")["temperature"] == 0


def test_request_asks_for_verbatim_transcription_not_translation():
    """Chaining OCR into translation compounds error (spec 5.6.3)."""
    prompt = transcribe.build_request(b"x")["messages"][0]["content"][-1]["text"]
    lowered = prompt.lower()
    assert "verbatim" in lowered
    assert "do not translate" in lowered


def test_base64_payload_has_no_newlines():
    params = transcribe.build_request(b"x" * 500)
    data = params["messages"][0]["content"][0]["source"]["data"]
    assert "\n" not in data


@pytest.mark.parametrize("pages,expected", [
    (1, [(1, 1)]),
    (5, [(1, 5)]),
    (20, [(1, 20)]),
    (28, [(1, 20), (21, 28)]),
    (60, [(1, 20), (21, 40), (41, 60)]),
])
def test_long_documents_are_chunked_at_the_output_ceiling(pages, expected):
    """A 60-page document emits ~90k output tokens against Haiku 4.5's 64k
    ceiling, so it must be split (spec 5.6.2)."""
    assert transcribe.chunk_ranges(pages) == expected


def test_no_chunking_when_page_count_is_unknown():
    assert transcribe.chunk_ranges(None) == [(1, None)]


def test_result_parsing_rejects_an_empty_response():
    """Anthropic documents occasional empty content in JSON-ish modes; treat it
    as a failed attempt, not as an empty document."""
    result = transcribe.parse_response("")
    assert result.status != "ok"


def test_result_parsing_keeps_thaana_intact():
    result = transcribe.parse_response("އަސާސީ މުސާރަ: މަހަކު 10,750 ރުފިޔާ")
    assert result.status == "ok"
    assert result.transcribed is True
    assert "10,750" in result.text
