import pytest
from django.db import connection


@pytest.mark.django_db
def test_running_on_postgres():
    assert connection.vendor == "postgresql"


@pytest.mark.django_db
def test_required_extensions_are_installed():
    with connection.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cur.fetchall()}
    assert "pg_trgm" in installed
    assert "unaccent" in installed
