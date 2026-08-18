import pytest
from django.test import Client


@pytest.fixture
def api(db):
    return Client()


@pytest.fixture
def sources(db):
    from search.models import Source
    Source.objects.create(key="ibay", label_en="iBay", label_dv="އައިބޭ",
                          site_url="https://ibay.com.mv",
                          icon="/static/sources/ibay.svg", icon_fallback_text="iB")
    Source.objects.create(key="gazette", label_en="Gazette", label_dv="ގެޒެޓް",
                          site_url="https://gazette.gov.mv",
                          icon="/static/sources/gazette.svg",
                          icon_fallback_text="ގ")
    Source.objects.create(key="retired", label_en="Retired", site_url="https://x",
                          is_active=False)
