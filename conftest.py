"""Pytest bootstrap.

Tests point STREAM_DB_ALIAS at `default` because a second connection cannot
see the uncommitted transaction @pytest.mark.django_db wraps each test in.
Production still resolves to `direct` (P4 task 1).
"""

import os


def pytest_configure(config):
    os.environ.setdefault("STREAM_DB_ALIAS", "default")
    from django.conf import settings

    settings.STREAM_DB_ALIAS = os.environ["STREAM_DB_ALIAS"]
