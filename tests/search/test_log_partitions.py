import pytest
from django.core.management import call_command
from django.db import connection


def _partitions(table):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = %s ORDER BY 1", [table])
        return [r[0] for r in cur.fetchall()]


@pytest.mark.django_db
def test_query_log_is_partitioned_by_month():
    """Spec 16.3: the fastest-growing table in the system must not share
    partition space or vacuum behaviour with SearchDocument."""
    assert _partitions("search_querylog")


@pytest.mark.django_db
def test_create_log_partitions_is_idempotent():
    call_command("create_log_partitions", "--months", "3")
    first = _partitions("search_querylog")
    call_command("create_log_partitions", "--months", "3")
    assert _partitions("search_querylog") == first


@pytest.mark.django_db
def test_create_log_partitions_creates_the_requested_horizon():
    call_command("create_log_partitions", "--months", "6")
    assert len(_partitions("search_querylog")) >= 6


@pytest.mark.django_db
def test_brin_index_exists_on_created_at():
    with connection.cursor() as cur:
        cur.execute("SELECT indexdef FROM pg_indexes "
                    "WHERE tablename LIKE 'search_querylog%%'")
        defs = " ".join(r[0] for r in cur.fetchall())
    assert "brin" in defs.lower()
