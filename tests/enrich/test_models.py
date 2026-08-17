import pytest
from django.db import IntegrityError

from enrich.models import EnrichedRecord


@pytest.mark.django_db
def test_identity_is_source_plus_source_key():
    EnrichedRecord.objects.create(
        source="ibay", source_key="1", content_hash="a" * 64, doc_type="shopping"
    )
    with pytest.raises(IntegrityError):
        EnrichedRecord.objects.create(
            source="ibay", source_key="1", content_hash="b" * 64, doc_type="job"
        )


@pytest.mark.django_db
def test_defaults_are_pending_and_empty():
    r = EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="c" * 64, doc_type="news"
    )
    assert r.status == "pending"
    assert r.attrs == {}
    assert r.validation == {}
    assert r.keywords == []
    assert r.attempts == 0
