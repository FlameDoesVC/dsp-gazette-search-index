# Search Engine P3 Attachments - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> ## ⚠ This plan shipped with three defects. Corrections live in P5 Task 0.
>
> Found after P3 landed. All three were errors in **this document** and were
> implemented faithfully; the fixes and their tests are in
> `2026-08-18-search-p5-api.md`, Task 0. Do not re-derive them from the text
> below, and do not run `extract_attachments` until Task 0 has landed.
>
> | Defect | Where in this plan | Effect |
> |---|---|---|
> | `--no-transcribe` writes `ocr_failed`, which is terminal | Task 6, and `test_no_transcribe_flag_marks_ocr_failed_instead_of_spending` asserts it | The free measurement pass permanently disables the paid run it exists to budget for. Already fired: 89 attachments were stranded. |
> | Batch flush sits below the `continue` that queues an item | Task 6 command body | A run of consecutive scanned PDFs accumulates whole PDFs in memory, unbounded by `--batch-size`. |
> | `_store` labels any non-transcription failure `fetch_failed` | Task 6 `_store` | A `.docx` that fails to parse is recorded as a fetch failure and re-downloaded every run. |
>
> Everything else in this plan is sound and its tests pass. The money guard in
> particular works: an attachment with `status='ok'` is never re-fetched.

**Goal:** Extract the text from gazette attachments, because for most job postings the listing is a stub and the salary, qualifications and application instructions live inside an attached file.

**Architecture:** An `Attachment` row per file, populated by a cheapest-first extraction ladder — `.docx` via python-docx, PDFs with a text layer via `pdftotext`, scanned PDFs via Claude Haiku 4.5 with native PDF input over the Batch API. Files are fetched, extracted and discarded; only text and a checksum are kept. A character-error-rate harness gates the transcription path.

**Tech Stack:** Python 3.12, `python-docx`, poppler (`pdftotext`, `pdfinfo`), `anthropic` SDK, PostgreSQL, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` (sections 5.6, 5.6.1, 5.6.2, 5.7)

**Plan index:** `docs/superpowers/plans/README.md` — read the cross-plan contract first.

## Global Constraints

- **Attachment files are never stored.** Fetch, extract, keep `text` + `content_sha` + `url`, discard the bytes. Measured: storing them would be ~40 GB against 194 MB of text. Source: spec 5.6, 12.6.
- **`Attachment.text` is capped at 20,000 characters.**
- **Transcription is guarded by existence.** `status='ok'` is never re-sent to Claude. `ocr_failed` is terminal. The only override is `SearchDocument.stale_marked_at`. Source: spec 5.7.
- **Tesseract is excluded deliberately.** Published Thaana accuracy is ~69% on machine-generated text; at that rate extracted text poisons the index with plausible wrong words. A vision model or nothing. Source: spec 5.6.
- **Claude Haiku 4.5 takes PDFs natively** — no rasterization, no `pdftoppm`, no temp images. That deletion is what keeps this phase inside the RAM budget. Source: spec 5.6.1, 12.4.
- **Chunk long documents at 20 pages per request.** A 60-page document would emit ~90,000 output tokens against Haiku 4.5's 64,000 ceiling. This is a correctness constraint, not a cost one. Source: spec 5.6.2.
- **Batch API, temperature 0.** Extraction is a background command with no latency requirement; paying list price for it is pointless. Source: spec 5.6.1.
- **Transcribed text is lower-trust.** `Attachment.transcribed=True` lowers document `quality` and flags the card. Source: spec 5.6.1.
- **Never chain OCR into translation before extraction.** Transcribed Thaana feeds enrichment and the grounding validator directly; translation is display-only. Source: spec 5.6.3.
- Measured corpus facts to design against: 239/306 iulaan carry attachments; 261 PDFs, 73 `.docx`, 2 `.xlsx`; 45% of PDFs are scanned; pages per PDF mean 4.5, median 2, p90 10, max 28; scanned PDFs average 3.2 pages; mean file size 0.93 MB.
- Version control is **jj**, not git.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `gazette/migrations/00XX_attachment.py` | `Attachment` table |
| `search/extract/__init__.py` | Public surface |
| `search/extract/labels.py` | Attachment label to role classification |
| `search/extract/fetch.py` | Download, hash, discard |
| `search/extract/local.py` | `.docx` and `pdftotext` extractors, ladder routing |
| `search/extract/transcribe.py` | Claude Haiku 4.5 Batch API transcription |
| `search/extract/cer.py` | Character error rate + the gate |
| `search/extract/tables.py` | Gazette HTML table to label/value pairs |
| `search/management/commands/extract_attachments.py` | Ladder orchestration |
| `search/management/commands/cer_harness.py` | Model selection and gate calibration |
| `search/tests/test_extract_*.py` | One module per extractor concern |

**Modified:**

| Path | Change |
|---|---|
| `gazette/models.py` | Add `Attachment` |
| `search/adapters/gazette.py` | Fold attachment text and table pairs into `text_dv` |
| `beynunehcheh/settings.py` | `TRANSCRIBE_*` settings |
| `requirements.txt` | `anthropic`, `python-docx` |

---

### Task 1: The `Attachment` model and label classification

**Files:**
- Create: `search/extract/__init__.py`, `search/extract/labels.py`, `search/tests/test_extract_labels.py`
- Modify: `gazette/models.py`
- Test: `search/tests/test_extract_labels.py`

**Interfaces:**
- Produces: `gazette.models.Attachment`; `search.extract.labels.classify_label(label, url) -> str` returning `main | application_form | annex | unknown`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_labels.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_labels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.extract'`.

- [ ] **Step 3: Write the classifier**

```bash
mkdir -p search/extract && touch search/extract/__init__.py
```

Create `search/extract/labels.py`:

```python
"""Attachment label classification. Spec 5.6.

Labels carry meaning: `iulaan` is the notice itself, `vazeefa ah edhey form` is
the blank application form. Indexing the second as job text is the failure this
prevents; it also becomes an `apply_method` in P4 rather than searchable body.
"""

from __future__ import annotations

import re

MAIN = "main"
APPLICATION_FORM = "application_form"
ANNEX = "annex"
UNKNOWN = "unknown"

_FORM = re.compile(
    r"form|foam|ފޯމ|އެދޭ|application|apply",
    re.IGNORECASE,
)
_MAIN = re.compile(
    r"iulaan|iulan|announcement|notice|އިޢުލާން|އިއުލާން|ނޯޓިސް",
    re.IGNORECASE,
)
_ANNEX = re.compile(
    r"annex|attachment|sheet|schedule|appendix|a\d\b|ޖަދުވަލު",
    re.IGNORECASE,
)

_MIME = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document"
    ),
    ".doc": "application/msword",
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument"
        ".spreadsheetml.sheet"
    ),
}


def classify_label(label: str, url: str = "") -> str:
    text = f"{label or ''} {url or ''}"
    # Form check first: "vazeefa ah edhey form" would otherwise match nothing
    # useful, and a form mislabelled as main is the expensive mistake.
    if _FORM.search(text):
        return APPLICATION_FORM
    if _MAIN.search(text):
        return MAIN
    if _ANNEX.search(text):
        return ANNEX
    return UNKNOWN


def guess_mime(url: str) -> str:
    lowered = (url or "").lower()
    for suffix, mime in _MIME.items():
        if lowered.endswith(suffix):
            return mime
    return ""
```

- [ ] **Step 4: Add the model**

Append to `gazette/models.py`:

```python
class Attachment(models.Model):
    """One file attached to an iulaan. Spec 5.6.

    The file itself is never stored: fetch, extract, keep the text and a
    checksum, discard the bytes. Measured, that is 194 MB of text instead of
    ~40 GB of PDFs, and the source URLs are stable and public.
    """

    STATUS = [
        ("pending", "pending"),
        ("ok", "ok"),
        ("ocr_failed", "ocr_failed"),
        ("fetch_failed", "fetch_failed"),
        ("skipped", "skipped"),
    ]
    METHOD = [
        ("docx", "docx"),
        ("pdftotext", "pdftotext"),
        ("transcribed", "transcribed"),
        ("none", "none"),
    ]

    iulaan = models.ForeignKey(
        "gazette.Iulaan", on_delete=models.CASCADE, related_name="attachment_files"
    )
    label_raw = models.CharField(max_length=512, blank=True)
    role = models.CharField(max_length=32, default="unknown")
    url = models.URLField(max_length=1024)
    content_sha = models.CharField(max_length=64, blank=True)
    mime = models.CharField(max_length=128, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)

    text = models.TextField(blank=True)
    page_count = models.IntegerField(null=True, blank=True)
    chars_per_page = models.IntegerField(null=True, blank=True)
    method = models.CharField(max_length=32, choices=METHOD, default="none")
    status = models.CharField(max_length=32, choices=STATUS, default="pending")
    transcribed = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    attempts = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    TEXT_CAP = 20_000

    class Meta:
        unique_together = ("iulaan", "url")
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["iulaan", "role"]),
        ]

    def __str__(self):
        return f"{self.iulaan_id}:{self.role}"
```

- [ ] **Step 5: Migrate and run the test**

```bash
./venv/bin/python manage.py makemigrations gazette --name attachment
./venv/bin/python manage.py migrate gazette
./venv/bin/pytest search/tests/test_extract_labels.py -v
```
Expected: PASS, 14 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(gazette): attachment model and label classification"
```

---

### Task 2: Fetching

**Files:**
- Create: `search/extract/fetch.py`, `search/tests/test_extract_fetch.py`
- Modify: `beynunehcheh/settings.py`
- Test: `search/tests/test_extract_fetch.py`

**Interfaces:**
- Produces: `sync_attachments(iulaan) -> int` creating `Attachment` rows from `Iulaan.attachments`; `fetch_bytes(url) -> tuple[bytes, str] | None` returning `(content, sha256)`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_fetch.py`:

```python
import hashlib
import pytest
from gazette.models import Attachment, Iulaan
from search.extract import fetch


@pytest.fixture
def iulaan(db):
    return Iulaan.objects.create(
        id="1", title="Notice", additional_info={}, body="",
        attachments={
            "iulaan": "https://storage.googleapis.com/gazette.gov.mv/docs/iulaan/1.pdf",
            "vazeefa ah edhey form": "https://storage.googleapis.com/gazette.gov.mv/docs/iulaan/2.pdf",
        },
    )


@pytest.mark.django_db
def test_sync_creates_one_row_per_attachment(iulaan):
    assert fetch.sync_attachments(iulaan) == 2
    assert Attachment.objects.filter(iulaan=iulaan).count() == 2


@pytest.mark.django_db
def test_sync_assigns_roles_from_labels(iulaan):
    fetch.sync_attachments(iulaan)
    roles = set(Attachment.objects.values_list("role", flat=True))
    assert roles == {"main", "application_form"}


@pytest.mark.django_db
def test_sync_is_idempotent(iulaan):
    fetch.sync_attachments(iulaan)
    fetch.sync_attachments(iulaan)
    assert Attachment.objects.filter(iulaan=iulaan).count() == 2


@pytest.mark.django_db
def test_sync_handles_an_empty_attachments_dict(db):
    empty = Iulaan.objects.create(
        id="2", title="No files", additional_info={}, body="", attachments={}
    )
    assert fetch.sync_attachments(empty) == 0


@pytest.mark.django_db
def test_sync_records_the_guessed_mime(iulaan):
    fetch.sync_attachments(iulaan)
    assert all(
        a.mime == "application/pdf" for a in Attachment.objects.all()
    )


def test_fetch_bytes_returns_content_and_sha(monkeypatch):
    payload = b"%PDF-1.4 fake"

    class _Resp:
        status_code = 200
        content = payload

        def raise_for_status(self):
            pass

    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: _Resp())
    content, sha = fetch.fetch_bytes("https://x/1.pdf")
    assert content == payload
    assert sha == hashlib.sha256(payload).hexdigest()


def test_fetch_bytes_returns_none_on_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch.httpx, "get", _boom)
    assert fetch.fetch_bytes("https://x/1.pdf") is None


def test_fetch_bytes_refuses_oversized_files(monkeypatch):
    class _Resp:
        status_code = 200
        content = b"x" * (fetch.MAX_BYTES + 1)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(fetch.httpx, "get", lambda *a, **k: _Resp())
    assert fetch.fetch_bytes("https://x/big.pdf") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.extract.fetch'`.

- [ ] **Step 3: Write the module**

Create `search/extract/fetch.py`:

```python
"""Attachment discovery and download. Spec 5.6.

Bytes are returned to the caller and never persisted. The bucket at
storage.googleapis.com/gazette.gov.mv is public per object but not listable,
so discovery comes from each iulaan's own `attachments` dict.
"""

from __future__ import annotations

import hashlib
import logging
import time

import httpx
from django.conf import settings

from gazette.models import Attachment, Iulaan
from search.extract.labels import classify_label, guess_mime

logger = logging.getLogger(__name__)

# Measured p90 file size is 2.66 MB and max 4.92 MB; 32 MB is also the
# Anthropic per-request ceiling, so anything above it cannot be transcribed.
MAX_BYTES = 32 * 1024 * 1024
_TIMEOUT = 60.0


def sync_attachments(iulaan: Iulaan) -> int:
    """Create an Attachment row per entry in the iulaan's attachments dict."""
    entries = iulaan.attachments or {}
    if not isinstance(entries, dict):
        return 0
    created = 0
    for label, url in entries.items():
        if not url or not isinstance(url, str):
            continue
        _obj, was_created = Attachment.objects.get_or_create(
            iulaan=iulaan,
            url=url,
            defaults={
                "label_raw": str(label)[:512],
                "role": classify_label(str(label), url),
                "mime": guess_mime(url),
            },
        )
        created += 1 if was_created else 0
    return len(entries)


def fetch_bytes(url: str) -> tuple[bytes, str] | None:
    """Download once. Returns `(content, sha256)` or None on any failure."""
    delay = getattr(settings, "ATTACHMENT_FETCH_DELAY", 0.5)
    try:
        response = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("fetch failed %s: %s", url, exc)
        return None
    finally:
        if delay:
            time.sleep(delay)

    content = response.content
    if not content or len(content) > MAX_BYTES:
        logger.warning("rejecting %s: %d bytes", url, len(content or b""))
        return None
    return content, hashlib.sha256(content).hexdigest()
```

- [ ] **Step 4: Add the settings**

Append to `beynunehcheh/settings.py`:

```python
# --- attachment extraction ------------------------------------------------
ATTACHMENT_FETCH_DELAY = float(os.environ.get("ATTACHMENT_FETCH_DELAY", "0.5"))
# Below this many extracted characters per page a PDF is treated as scanned.
SCANNED_CHARS_PER_PAGE = int(os.environ.get("SCANNED_CHARS_PER_PAGE", "200"))
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "claude-haiku-4-5")
TRANSCRIBE_MAX_PAGES = int(os.environ.get("TRANSCRIBE_MAX_PAGES", "10"))
# A 60-page document would emit ~90k output tokens against Haiku 4.5's 64k
# ceiling, so long documents are split. Correctness, not cost.
TRANSCRIBE_PAGES_PER_CHUNK = int(os.environ.get("TRANSCRIBE_PAGES_PER_CHUNK", "20"))
# Character error rate above which transcription output is rejected outright.
# Calibrated by the cer_harness command; text that is confidently wrong is
# worse than absent text.
TRANSCRIBE_MAX_CER = float(os.environ.get("TRANSCRIBE_MAX_CER", "0.15"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_extract_fetch.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(extract): attachment discovery and bounded fetching"
```

---

### Task 3: Local extractors and ladder routing

**Files:**
- Create: `search/extract/local.py`, `search/tests/test_extract_local.py`, `search/tests/fixtures/`
- Modify: `requirements.txt`, `docker/api.Dockerfile` (already has poppler-utils from P1)
- Test: `search/tests/test_extract_local.py`

**Interfaces:**
- Produces: `ExtractionResult(text, page_count, chars_per_page, method, status)`; `extract_docx(content) -> ExtractionResult`; `extract_pdf_text_layer(content) -> ExtractionResult`; `needs_transcription(result) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_local.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_local.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.extract.local'`.

- [ ] **Step 3: Install dependencies**

```bash
./venv/bin/pip install python-docx anthropic
grep -qi '^python-docx' requirements.txt || ./venv/bin/pip freeze | grep -i '^python-docx' >> requirements.txt
grep -qi '^anthropic' requirements.txt || ./venv/bin/pip freeze | grep -i '^anthropic' >> requirements.txt
which pdftotext pdfinfo || echo "install poppler-utils on this machine"
```

- [ ] **Step 4: Write the module**

Create `search/extract/local.py`:

```python
"""Local extraction: the cheap rungs of the ladder. Spec 5.6.

Routing decision is chars-per-page, never file size. Measured on the corpus: a
58 KB single page was scanned while a 1.1 MB 28-page document had a complete
text layer, so any size heuristic is wrong.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

TEXT_CAP = 20_000
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass(slots=True)
class ExtractionResult:
    text: str = ""
    page_count: int | None = None
    chars_per_page: int | None = None
    method: str = "none"
    status: str = "failed"
    error: str = ""


def _clean(text: str) -> str:
    text = _WS.sub(" ", text or "")
    return _BLANKS.sub("\n\n", text).strip()[:TEXT_CAP]


def have_poppler() -> bool:
    return bool(shutil.which("pdftotext") and shutil.which("pdfinfo"))


def extract_docx(content: bytes) -> ExtractionResult:
    """python-docx, pure Python, flat memory. 73 files in the corpus and often
    the detail sheet -- the best value on the whole ladder."""
    try:
        from docx import Document

        doc = Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = _clean("\n".join(parts))
    except Exception as exc:
        return ExtractionResult(
            method="docx", status="failed", error=str(exc)[:500]
        )
    if not text:
        return ExtractionResult(method="docx", status="failed", error="empty")
    return ExtractionResult(text=text, method="docx", status="ok")


def _page_count(path: str) -> int | None:
    try:
        out = subprocess.run(
            ["pdfinfo", path], capture_output=True, timeout=60, check=False
        ).stdout.decode("utf-8", "replace")
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def extract_pdf_text_layer(content: bytes) -> ExtractionResult:
    """`pdftotext -layout`: a subprocess with a flat memory profile.

    `-layout` preserves column structure, which matters because the content we
    most want out of these files is a salary table.
    """
    if not have_poppler():
        return ExtractionResult(
            method="pdftotext", status="failed", error="poppler not installed"
        )
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(content)
        handle.flush()
        pages = _page_count(handle.name)
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", "-q", handle.name, "-"],
                capture_output=True,
                timeout=120,
                check=False,
            )
            raw = proc.stdout.decode("utf-8", "replace")
        except Exception as exc:
            return ExtractionResult(
                page_count=pages, method="pdftotext",
                status="failed", error=str(exc)[:500],
            )

    text = _clean(raw)
    dense = len(re.sub(r"\s", "", text))
    per_page = int(dense / pages) if pages else (dense if text else 0)
    return ExtractionResult(
        text=text,
        page_count=pages,
        chars_per_page=per_page,
        method="pdftotext",
        status="ok",
    )


def needs_transcription(result: ExtractionResult) -> bool:
    """True when the PDF has no usable text layer and must go to a vision
    model. Routing is on extracted density alone."""
    if result.status != "ok":
        return True
    threshold = getattr(settings, "SCANNED_CHARS_PER_PAGE", 200)
    return (result.chars_per_page or 0) < threshold
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_extract_local.py -v`
Expected: PASS, 8 tests (the poppler one may skip).

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(extract): docx and pdf text-layer extraction with density routing"
```

---

### Task 4: Transcription via Claude Haiku 4.5

**Files:**
- Create: `search/extract/transcribe.py`, `search/tests/test_extract_transcribe.py`
- Test: `search/tests/test_extract_transcribe.py`

**Interfaces:**
- Produces: `build_request(content, *, page_range=None) -> dict` (the message params); `transcribe_batch(items) -> dict[str, ExtractionResult]` keyed by `custom_id`; `chunk_ranges(page_count) -> list[tuple[int, int]]`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_transcribe.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.extract.transcribe'`.

- [ ] **Step 3: Write the module**

Create `search/extract/transcribe.py`:

```python
"""Scanned-PDF transcription with Claude Haiku 4.5. Spec 5.6.1.

Native PDF input: the file goes up as a `document` content block and Claude
renders the pages. No rasterization, no temp images, no RAM spike -- and the
model sees the page as laid out, which matters because the content we want most
is a salary table.

Runs through the Batch API. Extraction is a background command with no latency
requirement, so paying list price would be pointless.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from django.conf import settings

from search.extract.local import TEXT_CAP, ExtractionResult, _clean

logger = logging.getLogger(__name__)

_PROMPT = """Transcribe this document verbatim.

Rules:
- Output only the transcribed text. No preamble, no commentary, no summary.
- Do not translate. Dhivehi (Thaana) stays in Thaana; English stays in English.
- Preserve numbers, dates and reference codes exactly as written.
- Preserve table structure using " | " between cells, one row per line.
- If a region is illegible, write [illegible] rather than guessing.
"""


@dataclass(slots=True)
class TranscriptionItem:
    custom_id: str
    content: bytes
    page_range: tuple[int, int | None] | None = None


def chunk_ranges(page_count: int | None) -> list[tuple[int, int | None]]:
    """Split a document into per-request page ranges.

    Haiku 4.5 caps output at 64,000 tokens; a page transcribes to roughly 1,500,
    so ~20 pages is the safe ceiling per request.
    """
    if not page_count:
        return [(1, None)]
    per_chunk = getattr(settings, "TRANSCRIBE_PAGES_PER_CHUNK", 20)
    ranges: list[tuple[int, int | None]] = []
    start = 1
    while start <= page_count:
        end = min(start + per_chunk - 1, page_count)
        ranges.append((start, end))
        start = end + 1
    return ranges


def build_request(content: bytes, *, page_range=None) -> dict:
    """Message params for one transcription request."""
    encoded = base64.standard_b64encode(content).decode("ascii")
    instruction = _PROMPT
    if page_range and page_range[1]:
        instruction += (
            f"\nTranscribe pages {page_range[0]} to {page_range[1]} only.\n"
        )
    return {
        "model": getattr(settings, "TRANSCRIBE_MODEL", "claude-haiku-4-5"),
        "max_tokens": 32_000,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": instruction},
                ],
            }
        ],
    }


def parse_response(text: str) -> ExtractionResult:
    cleaned = _clean(text or "")
    if not cleaned:
        return ExtractionResult(
            method="transcribed", status="failed", error="empty response"
        )
    result = ExtractionResult(
        text=cleaned[:TEXT_CAP], method="transcribed", status="ok"
    )
    result.transcribed = True   # set via the dataclass attribute below
    return result


def transcribe_batch(items: list[TranscriptionItem]) -> dict[str, ExtractionResult]:
    """Submit every item as one batch, wait for it, return results by custom_id.

    Batch results arrive in arbitrary order, so they are keyed by `custom_id`
    and never by position.
    """
    if not items:
        return {}

    import anthropic
    from anthropic.types.message_create_params import (
        MessageCreateParamsNonStreaming,
    )
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(
        requests=[
            Request(
                custom_id=item.custom_id,
                params=MessageCreateParamsNonStreaming(
                    **build_request(item.content, page_range=item.page_range)
                ),
            )
            for item in items
        ]
    )
    logger.info("submitted batch %s with %d requests", batch.id, len(items))

    import time

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        time.sleep(30)

    out: dict[str, ExtractionResult] = {}
    for entry in client.messages.batches.results(batch.id):
        if entry.result.type != "succeeded":
            out[entry.custom_id] = ExtractionResult(
                method="transcribed",
                status="failed",
                error=str(entry.result.type),
            )
            continue
        text = "".join(
            block.text
            for block in entry.result.message.content
            if block.type == "text"
        )
        out[entry.custom_id] = parse_response(text)
    return out
```

- [ ] **Step 4: Add the `transcribed` field to `ExtractionResult`**

`parse_response` sets `result.transcribed`, so add it to the dataclass in `search/extract/local.py`:

```python
    error: str = ""
    transcribed: bool = False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_extract_transcribe.py -v`
Expected: PASS, 13 tests. No API key is needed — every test exercises request construction and parsing, not the network.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(extract): claude haiku transcription with native pdf input and chunking"
```

---

### Task 5: The character-error-rate gate

**Files:**
- Create: `search/extract/cer.py`, `search/management/commands/cer_harness.py`, `search/tests/test_extract_cer.py`
- Test: `search/tests/test_extract_cer.py`

**Interfaces:**
- Produces: `char_error_rate(reference, hypothesis) -> float`; `passes_gate(cer) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_cer.py`:

```python
import pytest
from search.extract.cer import char_error_rate, passes_gate


def test_identical_text_has_zero_error():
    assert char_error_rate("ހަކަތަ", "ހަކަތަ") == 0.0


def test_completely_different_text_has_high_error():
    assert char_error_rate("ހަކަތަ", "xyz") >= 1.0


def test_one_substitution_in_ten_characters():
    assert char_error_rate("abcdefghij", "abcdefghiX") == pytest.approx(0.1)


def test_whitespace_differences_are_ignored():
    assert char_error_rate("ހަކަތަ  ސަރުކާރު", "ހަކަތަ ސަރުކާރު") == 0.0


def test_empty_reference_is_undefined_and_returns_one():
    assert char_error_rate("", "anything") == 1.0


def test_gate_accepts_low_error(settings):
    settings.TRANSCRIBE_MAX_CER = 0.15
    assert passes_gate(0.05) is True


def test_gate_rejects_high_error(settings):
    """Tesseract's published ~69% Thaana accuracy sits far above any workable
    gate. Text that is confidently wrong is worse than absent text (spec 5.6)."""
    settings.TRANSCRIBE_MAX_CER = 0.15
    assert passes_gate(0.31) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_cer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.extract.cer'`.

- [ ] **Step 3: Write the module**

Create `search/extract/cer.py`:

```python
"""Character error rate and the transcription quality gate. Spec 5.6.1.

The evaluation corpus is free: PDFs that *do* have a text layer give
near-ground-truth Thaana via pdftotext. Transcribe the same files and compare.
Real Maldivian government documents, zero labelling cost.
"""

from __future__ import annotations

import re

from django.conf import settings

_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS.sub("", s or "")


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over characters, divided by reference length.

    Whitespace is stripped from both sides: layout differences between
    `pdftotext -layout` and a transcription are not errors.
    """
    ref, hyp = _normalize(reference), _normalize(hypothesis)
    if not ref:
        return 1.0
    if ref == hyp:
        return 0.0

    previous = list(range(len(hyp) + 1))
    for i, ref_char in enumerate(ref, start=1):
        current = [i]
        for j, hyp_char in enumerate(hyp, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ref_char != hyp_char),
                )
            )
        previous = current
    return previous[len(hyp)] / len(ref)


def passes_gate(cer: float) -> bool:
    return cer <= getattr(settings, "TRANSCRIBE_MAX_CER", 0.15)
```

- [ ] **Step 4: Write the harness command**

Create `search/management/commands/cer_harness.py`:

```python
"""Measure transcription accuracy against text-layer PDFs. Spec 5.6.1.

Run before committing to a transcription model, and again whenever the model
changes. This decides the model empirically rather than by reputation.
"""

import random

from django.core.management.base import BaseCommand

from gazette.models import Attachment
from search.extract import local, transcribe
from search.extract.cer import char_error_rate
from search.extract.fetch import fetch_bytes


class Command(BaseCommand):
    help = "Measure CER of the transcription path against text-layer PDFs."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=20)
        parser.add_argument("--seed", type=int, default=20260818)

    def handle(self, *args, **options):
        candidates = list(
            Attachment.objects.filter(
                status="ok", method="pdftotext", transcribed=False
            ).exclude(text="")[:500]
        )
        dense = [a for a in candidates if (a.chars_per_page or 0) >= 500]
        if not dense:
            self.stdout.write(
                self.style.ERROR(
                    "no text-layer attachments available; run "
                    "extract_attachments first"
                )
            )
            return

        rng = random.Random(options["seed"])
        rng.shuffle(dense)
        sample = dense[: options["sample"]]
        self.stdout.write(f"sampling {len(sample)} text-layer PDFs")

        items, references = [], {}
        for attachment in sample:
            fetched = fetch_bytes(attachment.url)
            if not fetched:
                continue
            content, _sha = fetched
            items.append(
                transcribe.TranscriptionItem(
                    custom_id=str(attachment.id), content=content
                )
            )
            references[str(attachment.id)] = attachment.text

        results = transcribe.transcribe_batch(items)

        rates = []
        for custom_id, result in results.items():
            if result.status != "ok":
                self.stdout.write(f"  {custom_id}: FAILED {result.error}")
                continue
            rate = char_error_rate(references[custom_id], result.text)
            rates.append(rate)
            self.stdout.write(f"  {custom_id}: CER {rate:.3f}")

        if not rates:
            self.stdout.write(self.style.ERROR("no successful transcriptions"))
            return

        rates.sort()
        mean = sum(rates) / len(rates)
        self.stdout.write(
            self.style.SUCCESS(
                f"n={len(rates)} mean={mean:.3f} "
                f"median={rates[len(rates) // 2]:.3f} "
                f"p90={rates[int(len(rates) * 0.9)]:.3f} max={rates[-1]:.3f}"
            )
        )
        self.stdout.write(
            "Set TRANSCRIBE_MAX_CER above the median and below the tail, and "
            "record these numbers in docs/superpowers/measurements/."
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_extract_cer.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(extract): character error rate gate and measurement harness"
```

---

### Task 6: Ladder orchestration

**Files:**
- Create: `search/management/commands/extract_attachments.py`, `search/tests/test_extract_command.py`
- Test: `search/tests/test_extract_command.py`

**Interfaces:**
- Produces: `manage.py extract_attachments [--type job] [--limit N] [--no-transcribe] [--stale]`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_command.py`:

```python
import pytest
from io import StringIO
from django.core.management import call_command
from gazette.models import Attachment, Iulaan, IulaanType
from search.extract.local import ExtractionResult


@pytest.fixture
def job_with_pdf(db, monkeypatch):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    iulaan = Iulaan.objects.create(
        id="1", title="Job", iulaan_type=jobs, additional_info={}, body="",
        attachments={"iulaan": "https://x/1.pdf"},
    )
    from search.extract import fetch
    monkeypatch.setattr(
        fetch, "fetch_bytes", lambda url: (b"%PDF-1.4 fake", "deadbeef")
    )
    return iulaan


@pytest.mark.django_db
def test_dense_pdf_uses_the_text_layer(job_with_pdf, monkeypatch):
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="salary 10,750", page_count=2, chars_per_page=2000,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", stdout=StringIO())
    a = Attachment.objects.get()
    assert a.status == "ok"
    assert a.method == "pdftotext"
    assert a.transcribed is False
    assert "10,750" in a.text


@pytest.mark.django_db
def test_sparse_pdf_is_queued_for_transcription(job_with_pdf, monkeypatch):
    from search.extract import local, transcribe
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="", page_count=3, chars_per_page=0,
            method="pdftotext", status="ok",
        ),
    )
    monkeypatch.setattr(
        transcribe, "transcribe_batch",
        lambda items: {
            items[0].custom_id: ExtractionResult(
                text="ޓްރާންސްކްރައިބްޑް", method="transcribed",
                status="ok", transcribed=True,
            )
        },
    )
    call_command("extract_attachments", stdout=StringIO())
    a = Attachment.objects.get()
    assert a.method == "transcribed"
    assert a.transcribed is True


@pytest.mark.django_db
def test_no_transcribe_flag_marks_ocr_failed_instead_of_spending(
    job_with_pdf, monkeypatch
):
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="", page_count=3, chars_per_page=0,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", "--no-transcribe", stdout=StringIO())
    assert Attachment.objects.get().status == "ocr_failed"


@pytest.mark.django_db
def test_already_ok_attachments_are_never_reprocessed(job_with_pdf, monkeypatch):
    """Spec 5.7: guarded by existence, because the failure mode costs money."""
    from search.extract import fetch
    call_command("extract_attachments", "--no-transcribe", stdout=StringIO())
    Attachment.objects.update(status="ok", text="already done", method="docx")

    def _explode(url):
        raise AssertionError("must not re-fetch an attachment already ok")

    monkeypatch.setattr(fetch, "fetch_bytes", _explode)
    call_command("extract_attachments", stdout=StringIO())
    assert Attachment.objects.get().text == "already done"


@pytest.mark.django_db
def test_stale_flag_overrides_the_existence_guard(job_with_pdf, monkeypatch):
    from django.utils import timezone
    from search.models import SearchDocument
    from search.extract import local
    monkeypatch.setattr(
        local, "extract_pdf_text_layer",
        lambda c: ExtractionResult(
            text="fresh text", page_count=1, chars_per_page=900,
            method="pdftotext", status="ok",
        ),
    )
    call_command("extract_attachments", stdout=StringIO())
    Attachment.objects.update(text="old text")
    SearchDocument.objects.create(
        source="gazette", source_key="1", doc_type="job",
        url="https://gazette.gov.mv/iulaan/1",
        stale_marked_at=timezone.now(),
    )
    call_command("extract_attachments", "--stale", stdout=StringIO())
    assert Attachment.objects.get().text == "fresh text"


@pytest.mark.django_db
def test_type_filter_restricts_the_run(job_with_pdf):
    call_command(
        "extract_attachments", "--type", "news", "--no-transcribe",
        stdout=StringIO(),
    )
    assert Attachment.objects.filter(status="pending").count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_command.py -v`
Expected: FAIL — `Unknown command: 'extract_attachments'`.

- [ ] **Step 3: Write the command**

Create `search/management/commands/extract_attachments.py`:

```python
"""Run the extraction ladder over gazette attachments. Spec 5.6.

Ladder, cheapest first:
  1. .docx via python-docx           -- free, no OCR
  2. PDF with a text layer           -- free, pdftotext
  3. scanned PDF                     -- Claude Haiku 4.5, batched
  4. give up, record ocr_failed

Order the corpus jobs-first: jobs are where attachment detail is load-bearing,
and only 16 of 306 iulaan state salary in the body.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from gazette.models import Attachment, Iulaan
from search.adapters.gazette import IULAAN_TYPE_DOC_TYPE
from search.extract import local, transcribe
from search.extract.fetch import fetch_bytes, sync_attachments
from search.models import SearchDocument

_TERMINAL = {"ok", "ocr_failed"}


class Command(BaseCommand):
    help = "Fetch and extract text from gazette attachments."

    def add_arguments(self, parser):
        parser.add_argument("--type", dest="doc_type", default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument(
            "--no-transcribe",
            action="store_true",
            help="Skip the paid rung; scanned PDFs record ocr_failed.",
        )
        parser.add_argument(
            "--stale",
            action="store_true",
            help="Reprocess documents marked stale, overriding the guard.",
        )

    def handle(self, *args, **options):
        iulaan_qs = Iulaan.objects.all()

        if options["doc_type"]:
            wanted = {
                name for name, dt in IULAAN_TYPE_DOC_TYPE.items()
                if dt == options["doc_type"]
            }
            if options["doc_type"] == "news":
                iulaan_qs = iulaan_qs.filter(
                    Q(iulaan_type__isnull=True)
                    | ~Q(iulaan_type__name__in=IULAAN_TYPE_DOC_TYPE)
                )
            else:
                iulaan_qs = iulaan_qs.filter(iulaan_type__name__in=wanted)

        stale_keys: set[str] = set()
        if options["stale"]:
            stale_keys = set(
                SearchDocument.objects.filter(
                    source="gazette", stale_marked_at__isnull=False
                ).values_list("source_key", flat=True)
            )
            iulaan_qs = iulaan_qs.filter(id__in=stale_keys)

        discovered = 0
        for iulaan in iulaan_qs.iterator(chunk_size=200):
            discovered += sync_attachments(iulaan)
        self.stdout.write(f"discovered {discovered} attachment references")

        pending = Attachment.objects.select_related("iulaan")
        if options["stale"]:
            pending = pending.filter(iulaan_id__in=stale_keys)
        else:
            pending = pending.exclude(status__in=_TERMINAL)
        # Blank application forms are not body text.
        pending = pending.exclude(role="application_form")
        if options["limit"]:
            pending = pending[: options["limit"]]

        to_transcribe: list[transcribe.TranscriptionItem] = []
        by_id: dict[str, Attachment] = {}
        done = 0

        for attachment in pending.iterator(chunk_size=100):
            attachment.attempts += 1
            fetched = fetch_bytes(attachment.url)
            if not fetched:
                attachment.status = "fetch_failed"
                attachment.save(update_fields=["status", "attempts", "updated_at"])
                continue
            content, sha = fetched
            attachment.content_sha = sha
            attachment.size_bytes = len(content)

            if attachment.url.lower().endswith(".docx"):
                result = local.extract_docx(content)
            elif attachment.url.lower().endswith(".pdf"):
                result = local.extract_pdf_text_layer(content)
            else:
                result = local.ExtractionResult(
                    status="skipped", error="unsupported type"
                )

            if result.method == "pdftotext" and local.needs_transcription(result):
                if options["no_transcribe"]:
                    attachment.status = "ocr_failed"
                    attachment.page_count = result.page_count
                    attachment.chars_per_page = result.chars_per_page
                    attachment.save()
                    continue
                to_transcribe.append(
                    transcribe.TranscriptionItem(
                        custom_id=str(attachment.id), content=content
                    )
                )
                by_id[str(attachment.id)] = attachment
                attachment.page_count = result.page_count
                attachment.chars_per_page = result.chars_per_page
                continue

            self._store(attachment, result)
            done += 1

            if len(to_transcribe) >= options["batch_size"]:
                done += self._flush(to_transcribe, by_id)
                to_transcribe, by_id = [], {}

        if to_transcribe:
            done += self._flush(to_transcribe, by_id)

        self.stdout.write(self.style.SUCCESS(f"extracted {done} attachments"))

    def _flush(self, items, by_id) -> int:
        self.stdout.write(f"transcribing {len(items)} scanned PDFs...")
        results = transcribe.transcribe_batch(items)
        count = 0
        for custom_id, result in results.items():
            attachment = by_id.get(custom_id)
            if attachment is None:
                continue
            self._store(attachment, result)
            count += 1 if result.status == "ok" else 0
        return count

    def _store(self, attachment: Attachment, result) -> None:
        attachment.text = (result.text or "")[: Attachment.TEXT_CAP]
        attachment.method = result.method
        attachment.transcribed = getattr(result, "transcribed", False)
        attachment.error = (result.error or "")[:2000]
        if result.page_count is not None:
            attachment.page_count = result.page_count
        if result.chars_per_page is not None:
            attachment.chars_per_page = result.chars_per_page
        attachment.status = "ok" if result.status == "ok" and attachment.text else (
            "ocr_failed" if result.method == "transcribed" else "fetch_failed"
        )
        attachment.save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_extract_command.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Measure the scanned fraction on real data before spending anything**

Spec 5.6.2 makes this the number that swings the transcription budget five-fold.

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python manage.py extract_attachments --no-transcribe
docker compose exec db psql -U beynunehcheh -c "
SELECT status, method, count(*),
       round(avg(page_count)::numeric, 1) AS avg_pages
FROM gazette_attachment GROUP BY 1,2 ORDER BY 3 DESC;"
```

Record the `ocr_failed` share — that is the measured scanned fraction. The 44-PDF sample in the spec put it at 45%; this run measures it across everything synced so far.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(extract): attachment extraction ladder with existence guard"
```

---

### Task 7: Gazette HTML table parsing

**Files:**
- Create: `search/extract/tables.py`, `search/tests/test_extract_tables.py`
- Test: `search/tests/test_extract_tables.py`

**Interfaces:**
- Produces: `parse_label_value_pairs(html) -> list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_extract_tables.py`:

```python
from search.extract.tables import parse_label_value_pairs

REAL_BODY = """
<table><tr>
<td width="150"><p dir="RTL"><strong>އަސާސީ މުސާރަ:</strong></p></td>
<td width="509"><p dir="RTL"> މަހަކު 10,750 ރުފިޔާ</p></td>
</tr><tr>
<td valign="top" width="150"><p dir="RTL"><strong>އެލަވަންސް/އިނާޔަތްތައް:</strong></p></td>
<td width="509"><ul>
<li>ހާޒިރީ އެލަވަންސްގެ ގޮތުގައި ހަމަޖެހިފައިވާ އުސޫލުން މަހަކު 4,400 ރުފިޔާ</li>
<li>ލިވިންގ އެލަވަންސް</li>
</ul></td>
</tr></table>
"""


def test_extracts_label_value_pairs():
    pairs = dict(parse_label_value_pairs(REAL_BODY))
    assert "އަސާސީ މުސާރަ" in " ".join(pairs)
    assert any("10,750" in v for v in pairs.values())


def test_list_items_are_preserved_within_a_value():
    pairs = dict(parse_label_value_pairs(REAL_BODY))
    allowances = next(v for k, v in pairs.items() if "އެލަވަންސް" in k)
    assert "4,400" in allowances
    assert "ލިވިންގ" in allowances


def test_no_markup_survives():
    for _label, value in parse_label_value_pairs(REAL_BODY):
        for token in ("<td", "dir=", "<li", "valign", "<strong"):
            assert token not in value


def test_trailing_colons_are_stripped_from_labels():
    for label, _value in parse_label_value_pairs(REAL_BODY):
        assert not label.endswith(":")


def test_non_table_html_yields_nothing():
    assert parse_label_value_pairs("<p>just a paragraph</p>") == []


def test_empty_input_is_safe():
    assert parse_label_value_pairs("") == []
    assert parse_label_value_pairs(None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_extract_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.extract.tables'`.

- [ ] **Step 3: Write the module**

Create `search/extract/tables.py`:

```python
"""Parse gazette HTML tables into label/value pairs. Spec 5.2.

Gazette bodies are Word-exported HTML and the tables inside them are already
labelled key-value pairs -- `<td>އަސާސީ މުސާރަ:</td><td>މަހަކު 10,750 ރުފިޔާ</td>`.
Structure the source gave us for free should not be re-derived by a language
model, so it is parsed here and handed to P4's extraction as pairs rather than
as markup.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_TRAILING = re.compile(r"[:\s]+$")

MAX_LABEL_CHARS = 120


def _text(node) -> str:
    parts: list[str] = []
    for item in node.iter():
        if item.tag == "li" and item.text_content().strip():
            parts.append(item.text_content().strip())
    if parts:
        return " | ".join(parts)
    return _WS.sub(" ", node.text_content()).strip()


def parse_label_value_pairs(html: str) -> list[tuple[str, str]]:
    if not html or "<" not in html:
        return []
    try:
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(html)
    except Exception:
        return []

    pairs: list[tuple[str, str]] = []
    for row in tree.iter("tr"):
        cells = list(row.iter("td"))
        if len(cells) < 2:
            continue
        label = _TRAILING.sub("", _WS.sub(" ", cells[0].text_content()).strip())
        value = _text(cells[1])
        if not label or not value or len(label) > MAX_LABEL_CHARS:
            continue
        pairs.append((label, value))
    return pairs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_extract_tables.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
jj commit -m "feat(extract): parse gazette html tables into label value pairs"
```

---

### Task 8: Fold attachment text into the index

**Files:**
- Modify: `search/adapters/gazette.py`
- Test: `search/tests/test_adapter_gazette_attachments.py`

**Interfaces:**
- Consumes: `Attachment`, `parse_label_value_pairs`.
- Produces: gazette drafts whose `text_dv` includes attachment text and table pairs, whose `quality` reflects transcription provenance, and whose `card` records `detail_source`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_adapter_gazette_attachments.py`:

```python
import pytest
from gazette.models import Attachment, Iulaan, IulaanType
from search.adapters.gazette import GazetteAdapter


@pytest.fixture
def iulaan(db):
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    return Iulaan.objects.create(
        id="1", title="ވަޒީފާގެ ފުރުޞަތު", translated_title="Job Opportunity",
        iulaan_type=jobs, additional_info={}, attachments={},
        body='<table><tr><td><strong>އަސާސީ މުސާރަ:</strong></td>'
             '<td>މަހަކު 10,750 ރުފިޔާ</td></tr></table>',
    )


def _draft(iulaan):
    a = GazetteAdapter()
    return a.to_document(a.fetch_raw(iulaan.id))


@pytest.mark.django_db
def test_attachment_text_reaches_the_indexed_text(iulaan):
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="pdftotext", text="ޤަވާޢިދު ސާފުކުރުން 4,400",
    )
    assert "4,400" in _draft(iulaan).text_dv


@pytest.mark.django_db
def test_application_form_text_is_excluded(iulaan):
    """A blank form must not become the job description (spec 5.6)."""
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/2.pdf", role="application_form",
        status="ok", method="pdftotext", text="FORM BOILERPLATE ONLY",
    )
    assert "FORM BOILERPLATE" not in _draft(iulaan).text_dv


@pytest.mark.django_db
def test_failed_attachments_contribute_nothing(iulaan):
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/3.pdf", role="main",
        status="ocr_failed", method="transcribed", text="",
    )
    draft = _draft(iulaan)
    assert draft.card["detail_source"] == "listing"


@pytest.mark.django_db
def test_table_pairs_are_folded_into_the_text(iulaan):
    assert "10,750" in _draft(iulaan).text_dv


@pytest.mark.django_db
def test_card_reports_when_details_came_from_an_attachment(iulaan):
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="pdftotext", text="detail text here",
    )
    assert _draft(iulaan).card["detail_source"] == "attachment"


@pytest.mark.django_db
def test_transcribed_provenance_lowers_quality_and_flags_the_card(iulaan):
    """Spec 5.6.1: a salary read off a photographed letter is not the same
    claim as one read off a clean Word export."""
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="transcribed", transcribed=True,
        text="ޓްރާންސްކްރައިބްޑް ޓެކްސްޓް",
    )
    draft = _draft(iulaan)
    assert draft.card["transcribed"] is True
    assert draft.attrs["transcribed"] is True

    Attachment.objects.update(transcribed=False, method="pdftotext")
    clean = _draft(iulaan)
    assert clean.quality > draft.quality


@pytest.mark.django_db
def test_content_hash_covers_attachment_checksums(iulaan):
    """Spec 5.6: a re-published PDF must trigger re-enrichment."""
    before = _draft(iulaan).content_hash
    Attachment.objects.create(
        iulaan=iulaan, url="https://x/1.pdf", role="main",
        status="ok", method="pdftotext", text="new", content_sha="abc123",
    )
    assert _draft(iulaan).content_hash != before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_adapter_gazette_attachments.py -v`
Expected: FAIL — attachment text is absent from `text_dv` and `card` has no `detail_source`.

- [ ] **Step 3: Update the adapter**

In `search/adapters/gazette.py`, add the import `from search.extract.tables import parse_label_value_pairs`, then in `fetch_raw` extend the payload:

```python
        return RawDocument(
            source=self.key,
            source_key=source_key,
            payload={
                "iulaan": iulaan,
                "attachments": list(
                    iulaan.attachment_files.filter(status="ok")
                    .exclude(role="application_form")
                    .exclude(text="")
                ),
            },
        )
```

In `to_document`, after `body_dv = strip_html(i.body)`, insert:

```python
        attachments = raw.payload.get("attachments", [])
        attachment_text = "\n".join(a.text for a in attachments)
        transcribed = any(a.transcribed for a in attachments)

        # Table structure the source already provides (spec 5.2). Parsed here
        # so P4's extraction receives labelled pairs, not markup.
        pairs = parse_label_value_pairs(i.body)
        pair_text = "\n".join(f"{label}: {value}" for label, value in pairs)
```

Change the `text_dv` assignment to:

```python
        text_dv = " ".join(
            part for part in
            (i.title, office_dv, type_name, body_dv, pair_text, attachment_text)
            if part
        ).strip()
```

Add to `attrs`: `"table_pairs": pairs, "transcribed": transcribed,` and to `card`:
`"detail_source": "attachment" if attachment_text else "listing", "transcribed": transcribed,`.

Replace the `content_hash` line with:

```python
            content_hash=hashlib.sha256(
                "".join(
                    [i.title or "", i.body or ""]
                    + [a.content_sha or a.text[:64] for a in attachments]
                ).encode()
            ).hexdigest(),
```

Finally extend `_quality` to take the attachment state:

```python
def _quality(body_dv: str, iulaan: Iulaan, attachments=(), transcribed=False) -> float:
    score = 0.0
    score += 0.3 if len(body_dv) >= 500 else 0.1
    score += 0.2 if iulaan.translated_title else 0.0
    score += 0.15 if iulaan.office_id else 0.0
    score += 0.2 if attachments else 0.0
    score += 0.15 if iulaan.attachments else 0.0
    # Transcribed text is lower-trust than a clean text layer (spec 5.6.1).
    if transcribed:
        score *= 0.8
    return round(min(score, 1.0), 3)
```

and update the call to `_quality(body_dv, i, attachments, transcribed)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_adapter_gazette_attachments.py search/tests/test_adapter_gazette.py -v`
Expected: PASS. The P1 gazette tests must still pass unchanged.

- [ ] **Step 5: Reindex so the new text reaches the vectors**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python manage.py reindex --source gazette
./venv/bin/python manage.py shell -c "
from search import query
for r in query.search('އެލަވަންސް', limit=5):
    print(round(r.score,3), r.card.get('detail_source'), r.title[:50])
"
```
Expected: job notices whose allowance text came from attachments now match a query that the listing body alone would not have satisfied. That is the whole point of this phase.

- [ ] **Step 6: Run the whole suite**

Run: `./venv/bin/pytest -q`
Expected: everything passes, P1 and P2 included.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(search): fold attachment text and table pairs into the gazette index"
```

---

## Out of scope for this plan

- Structured extraction of the parsed table pairs into `JobAttrs.compensation` — P4. This phase produces the pairs; P4 interprets them.
- `estimate_net` and any arithmetic on salary figures — P4, and always in Python, never in a model prompt.
- Deterministic pre-extraction of phone numbers, emails and money — P4, which is where the grounding validator lives.
- Translation of attachment text — display only, P4. Never a prerequisite for extraction (spec 5.6.3).
- Backfilling all 51,000 iulaan. This phase builds the machinery and runs it over what is synced; the full scrape is its own effort and `gazette/sync_service.py:20`'s `MAX_INDEX_PAGES` still derives from `DEBUG` rather than a setting.

## Before P4

Run `cer_harness` and record the result in
`docs/superpowers/measurements/`. Two numbers decide P4's shape: the measured
CER (which sets `TRANSCRIBE_MAX_CER` and confirms the model choice) and the
real scanned fraction from Task 6 Step 5 (which sets the transcription budget).
P4's pre-extraction regexes should be written against actual extracted text
from this phase, not against imagined input.
