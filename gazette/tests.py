import asyncio

import pytest

from gazette.models import Iulaan


@pytest.fixture
def sync_env(monkeypatch):
    """A minimal sync cycle: one page, one new iulaan, no network."""
    import gazette.sync_service

    calls = []

    async def fake_max(client, *a, **k):
        return 1

    async def fake_index(client, *a, **k):
        return ["IUL-1"]

    async def fake_fetch(client, iulaan_id):
        from types import SimpleNamespace
        data = SimpleNamespace(
            id="IUL-1", title="މަސައްކަތް ފުރުޞަތު",
            office_name="މިނިސްޓްރީ", iulaan_type="ބީލަން",
            additional_info={}, attachments=[],
            body="<p>ބިނާކުރުމުގެ މަސައްކަތް ކުރިއަށް ގެންދިއުމަށް</p>",
        )
        calls.append(iulaan_id)
        return data

    monkeypatch.setattr(gazette.sync_service, "get_max_page_number", fake_max)
    monkeypatch.setattr(gazette.sync_service, "fetch_index_links", fake_index)
    monkeypatch.setattr(gazette.sync_service, "fetch_and_parse_announcement", fake_fetch)
    return calls


@pytest.mark.django_db(transaction=True)
def test_sync_stores_raw_and_translates_nothing(sync_env, monkeypatch):
    """Ingest is network-bound and retryable; translation is a separate,
    resumable pass. A failed sync currently re-translates everything it had
    already done."""
    translate_calls = []

    async def fake_translate(*a, **k):
        translate_calls.append(a)
        return "x"

    monkeypatch.setattr("core.translate.translate_auto", fake_translate)
    from gazette.sync_service import sync_all
    asyncio.run(sync_all())
    assert translate_calls == []


@pytest.mark.django_db(transaction=True)
def test_bodies_are_never_translated(sync_env):
    """Spec 5.5: short fields only. 254M characters at full corpus size."""
    from gazette.sync_service import sync_all
    asyncio.run(sync_all())
    iulaan = Iulaan.objects.get(id="IUL-1")
    assert iulaan.title == "މަސައްކަތް ފުރުޞަތު"
    assert iulaan.translated_body == ""
