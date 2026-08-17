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
