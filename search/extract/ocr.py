"""Google Cloud Vision OCR over rasterized pages. Spec 5.6.

Vision alone is not good enough -- it drops ~25% of fili and scored CER 0.378
against a 0.15 gate. Its value is that it is *faithful about consonants*: 80%
anchor overlap on a scanned page where an LLM given the same page scored 0%.
That faithful skeleton is what the repair step in `repair.py` re-vowels, and
what the gate verifies against.

Pages are rasterized locally rather than sent as PDFs. Vision would read an
embedded text layer if one existed, which is correct for production but would
have made the evaluation meaningless; keeping one code path avoids a
measured-versus-shipped divergence.
"""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
_TOKEN = re.compile(r"[\wހ-޿]{4,}", re.UNICODE)


def rasterize(pdf: bytes, *, dpi: int | None = None, first: int = 1,
              last: int | None = None) -> list[bytes]:
    dpi = dpi or settings.OCR_DPI
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(pdf)
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        cmd += [str(src), str(Path(tmp) / "page")]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return [p.read_bytes() for p in sorted(Path(tmp).glob("page*.png"))]


def vision_ocr(png: bytes, *, hints=("dv",), client: httpx.Client | None = None) -> str:
    body = {"requests": [{
        "image": {"content": base64.b64encode(png).decode()},
        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        "imageContext": {"languageHints": list(hints)},
    }]}
    http = client or httpx.Client(timeout=180)
    try:
        r = http.post(f"{ENDPOINT}?key={settings.GOOGLE_VISION_API_KEY}", json=body)
        r.raise_for_status()
        payload = r.json()["responses"][0]
    finally:
        if client is None:
            http.close()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "")[:300])
    return payload.get("fullTextAnnotation", {}).get("text", "")


def anchor_overlap(text: str, *, title: str, office: str) -> float:
    """Fraction of the document's own known vocabulary present in `text`.

    Title and office come from the gazette HTML and are never OCR'd, so this
    is a grounding check that needs no reference transcription. Measured: 0%
    for a fabricated page, 83-93% for a faithful one.
    """
    known = set(_TOKEN.findall(title or "")) | set(_TOKEN.findall(office or ""))
    if not known:
        return 0.0
    return len(known & set(_TOKEN.findall(text or ""))) / len(known)
