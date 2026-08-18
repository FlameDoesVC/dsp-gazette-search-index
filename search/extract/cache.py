"""Content-addressed cache for billed API calls. Spec 5.6.

Keyed on (provider, model, prompt, sha256 of the exact input bytes), so an
identical request is never paid for twice. The cache is the reason a failed
backfill can be restarted for free: rerunning 40,500 pages after a crash would
otherwise cost the whole $61 again.

Never keyed on the attachment id -- the same page re-fetched must hit, and a
different page under the same id must miss.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import diskcache
from django.conf import settings

_cache = None


def cache():
    global _cache
    if _cache is None:
        _cache = diskcache.Cache(
            str(Path(settings.OCR_CACHE_DIR)),
            size_limit=settings.OCR_CACHE_SIZE_BYTES,
        )
    return _cache


def cached_call(provider: str, model: str, payload: bytes, prompt: str, fn):
    """Return (result, was_cached). `fn` is only invoked on a miss."""
    if not settings.OCR_CACHE_ENABLED:
        return fn(), False
    key = hashlib.sha256(b"|".join([
        provider.encode(), model.encode(), prompt.encode(),
        hashlib.sha256(payload).digest(),
    ])).hexdigest()
    hit = cache().get(key)
    if hit is not None:
        return hit, True
    out = fn()
    cache().set(key, out)
    return out, False
