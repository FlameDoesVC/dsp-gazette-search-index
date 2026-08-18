"""Fire-and-forget logging. Spec 16.3.

Never on the hot path: the write goes to a small thread pool and every
exception is swallowed. A search response must not slow down for, or fail
because of, analytics.

The query id is needed in the response so a click can reference it, so the
QueryLog row is written synchronously and only the ClickLog write is deferred.
That single INSERT is measured in the P5 load check; if it shows up in p95,
move it to the pool and return a client-generated UUID instead.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import connection as db_connection

from search.models import ClickLog, QueryLog

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="searchlog")


def _today() -> dt.date:
    return dt.date.today()


def session_hash(request) -> str:
    """Salted, daily-rotating. Supports same-session analysis without building
    a durable per-person history. Neither the IP nor the user agent is stored.
    """
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() \
        or request.META.get("REMOTE_ADDR", "")
    ua = request.META.get("HTTP_USER_AGENT", "")
    material = f"{settings.SEARCH_LOG_SALT}:{_today().isoformat()}:{ip}:{ua}"
    return hashlib.sha256(material.encode()).hexdigest()


def _write_query_log(**kwargs) -> int:
    return QueryLog.objects.create(**kwargs).id


def log_query(request, *, raw, plan, doc_type, filters, result_count,
              latency_ms) -> int | None:
    if not settings.SEARCH_LOGGING_ENABLED:
        return None
    try:
        return _write_query_log(
            q_raw=(raw or "")[:256],
            q_normalized=(getattr(plan, "raw", "") or "")[:256],
            detected_lang=getattr(plan, "lang", ""),
            response_lang=getattr(plan, "response_lang", ""),
            doc_type=doc_type or "",
            filters=list(filters or []),
            result_count=result_count,
            latency_ms=latency_ms,
            session_hash=session_hash(request),
        )
    except Exception:
        logger.exception("query logging failed")
        return None


def _write_click(query_id: int, document_id: int, position: int) -> None:
    try:
        if not QueryLog.objects.filter(id=query_id).exists():
            return
        ClickLog.objects.create(query_id=query_id, document_id=document_id,
                                position=position)
    except Exception:
        logger.exception("click logging failed")


def _pooled_write_click(query_id: int, document_id: int, position: int) -> None:
    try:
        _write_click(query_id, document_id, position)
    finally:
        # Each pool thread owns its connection and must not leak it.
        db_connection.close()


def log_click(query_id: int, document_id: int, position: int) -> None:
    if not settings.SEARCH_LOGGING_ENABLED:
        return
    if settings.SEARCH_LOGGING_SYNC:
        # Synchronous for tests; do NOT close the shared connection here.
        _write_click(query_id, document_id, position)
        return
    try:
        _POOL.submit(_pooled_write_click, query_id, document_id, position)
    except Exception:
        logger.exception("click dispatch failed")
