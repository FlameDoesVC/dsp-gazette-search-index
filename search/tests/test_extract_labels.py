import pytest
from search.extract.labels import classify_label, guess_mime


@pytest.mark.parametrize("label,expected", [
    ("iulaan", "main"),
    ("އިޢުލާން", "main"),
    ("vazeefa ah edhey form", "application_form"),
    ("ވަޒީފާއަށް އެދޭ ފޯމު", "application_form"),
    ("application form", "application_form"),
    ("A2 sheet", "annex"),
    ("annex 1", "annex"),
    ("something unrecognised", "unknown"),
])
def test_labels_route_correctly(label, expected):
    assert classify_label(label, "https://x/1.pdf") == expected


def test_application_forms_are_not_indexed_as_job_text():
    """A blank form indexed as the job description is the obvious failure
    this classifier exists to prevent (spec 5.6)."""
    assert classify_label("vazeefa ah edhey form", "https://x/2.pdf") != "main"


@pytest.mark.parametrize("url,mime", [
    ("https://x/1.pdf", "application/pdf"),
    ("https://x/1.PDF", "application/pdf"),
    ("https://x/1.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("https://x/1.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("https://x/1.bin", ""),
])
def test_mime_is_guessed_from_the_url(url, mime):
    assert guess_mime(url) == mime
