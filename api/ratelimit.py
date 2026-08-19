"""Rate limiting without new infrastructure.

Counted against the reports table itself rather than a cache backend: the
production stack has three gunicorn workers and LocMemCache is per-process, so
an in-memory limiter would grant 3x the intended budget. Adding Redis for one
counter is not worth an extra service; this is one indexed COUNT over a small
table.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.utils import timezone


def report_quota_exceeded(ip_hash: str) -> bool:
    from search.models import DocumentReport

    window = timezone.now() - dt.timedelta(seconds=settings.REPORT_RATE_WINDOW)
    used = DocumentReport.objects.filter(
        reporter_ip_hash=ip_hash, created_at__gte=window
    ).count()
    return used >= settings.REPORT_RATE_LIMIT


def proposal_quota_exceeded(ip_hash: str) -> bool:
    """Counted over the proposals table, like reports: three gunicorn workers
    make an in-process limiter grant three times the budget, and Redis for one
    counter is not worth a service."""
    from catalog.models import FieldProposal

    window = timezone.now() - dt.timedelta(
        seconds=settings.CATALOG_PROPOSAL_RATE_WINDOW)
    used = FieldProposal.objects.filter(
        proposer_ip_hash=ip_hash, created_at__gte=window).count()
    return used >= settings.CATALOG_PROPOSAL_RATE_LIMIT
