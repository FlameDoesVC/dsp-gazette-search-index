import pytest
from django.test import Client


@pytest.fixture
def api(db):
    return Client()


@pytest.fixture
def sources(db):
    from search.models import Source
    Source.objects.create(key="other", label_en="Other", label_dv="އެހެން",
                          site_url="https://other-source.example",
                          icon="/static/sources/other.svg", icon_fallback_text="އެ")
    Source.objects.create(key="gazette", label_en="Gazette", label_dv="ގެޒެޓް",
                          site_url="https://gazette.gov.mv",
                          icon="/sources/gazette.png",
                          icon_fallback_text="ގ")
    Source.objects.create(key="retired", label_en="Retired", site_url="https://x",
                          is_active=False)
