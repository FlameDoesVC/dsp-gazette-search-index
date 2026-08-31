import pytest
from search.models import Source


@pytest.mark.django_db
def test_seeded_sources_exist():
    assert Source.objects.filter(key="other").exists()
    assert Source.objects.filter(key="gazette").exists()


@pytest.mark.django_db
def test_source_key_is_unique():
    from django.db import IntegrityError
    with pytest.raises(IntegrityError):
        Source.objects.create(key="other", label_en="Dupe", site_url="https://x.mv")


@pytest.mark.django_db
def test_source_carries_bilingual_labels_and_an_icon():
    gazette = Source.objects.get(key="gazette")
    assert gazette.label_en
    assert gazette.label_dv
    # Served from the frontend's own public/ folder, not Django's /static/:
    # a browser on the Next.js origin has no rewrite for /static/* (only
    # /api/*), so that prefix 404s there even though Django itself serves it.
    assert gazette.icon.startswith("/sources/")
