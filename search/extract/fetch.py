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
