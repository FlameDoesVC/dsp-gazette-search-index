"""Pytest bootstrap.

Tests point STREAM_DB_ALIAS at `default` because a second connection cannot
see the uncommitted transaction @pytest.mark.django_db wraps each test in.
Production still resolves to `direct` (P4 task 1).
"""

import os


def pytest_configure(config):
    # Force, not setdefault: .env sets STREAM_DB_ALIAS=direct for production,
    # and that var is already present in the process environment (e.g. under
    # Docker's env_file) well before this hook runs, so setdefault was a no-op.
    os.environ["STREAM_DB_ALIAS"] = "default"
    from django.conf import settings

    settings.STREAM_DB_ALIAS = os.environ["STREAM_DB_ALIAS"]
    settings.SEARCH_LOGGING_SYNC = True
    settings.SEARCH_LOGGING_ENABLED = True
    # Query-side translation would hit the real provider on every English
    # query in tests. The query-translation tests override this per module.
    settings.SEARCH_TRANSLATE_QUERIES = False
