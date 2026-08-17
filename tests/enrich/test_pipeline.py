import json

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from enrich.models import EnrichedRecord
from enrich.pipeline import build_input, enrich_one, select_keys
from search.models import SearchDocument


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def run_chain(self, messages, rebuild=None):
        self.calls += 1
        return self.payload, "stub-model"

    async def aclose(self):
        pass


@pytest.fixture
def gazette_job(db):
    from gazette.models import Iulaan, IulaanType, Office
    t = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    o = Office.objects.create(name="Ministry of Example")
    return Iulaan.objects.create(
        id="IUL-1", title="އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ", office=o, iulaan_type=t,
        additional_info={
            "ނަންބަރު": "CS-IUL/2026/00173",
            "ސުންގަޑި": "23 އޮގަސްޓް 2026 13:00",
            "ޕަބްލިޝްކުރި ތާރީޚު": "16 އޮގަސްޓް 2026",
            "ޕަބްލިޝްކުރި ގަޑި": "14:12",
        },
        attachments=[],
        body="އަސާސީ މުސާރަ: މަހަކު 10,750 ރުފިޔާ ފޯނު: 3323838",
    )


@pytest.mark.django_db
def test_the_scraped_deadline_is_passed_to_the_model_as_truth(gazette_job):
    inp = build_input("gazette", "IUL-1")
    assert inp.scraped["deadline"] == "2026-08-23"


@pytest.mark.django_db
def test_the_model_cannot_overwrite_the_scraped_deadline(gazette_job):
    inp = build_input("gazette", "IUL-1")
    client = _StubClient({"doc_type": "job",
                          "attrs": {"deadline": "2027-01-01"}})
    rec = async_to_sync(enrich_one)(inp, client)
    assert rec.attrs["deadline"] == "2026-08-23"
    assert rec.status == "needs_review"


@pytest.mark.django_db
def test_build_input_carries_prior_scraped_and_candidates(gazette_job):
    inp = build_input("gazette", "IUL-1")
    assert inp.doc_type_prior == "job"
    assert inp.scraped["office"] == "Ministry of Example"
    assert "3323838" in inp.candidates.phones
    assert 10750.0 in [m["amount"] for m in inp.candidates.money]


@pytest.mark.django_db
def test_build_input_returns_none_for_a_missing_key():
    assert build_input("gazette", "nope") is None


@pytest.mark.django_db
def test_enrich_one_writes_an_ok_record(gazette_job):
    inp = build_input("gazette", "IUL-1")
    assert inp is not None
    client = _StubClient({
        "doc_type": "job", "doc_type_confidence": 0.95,
        "canonical_title_en": "Administrative Officer",
        "summary_en": "A post at the Ministry of Example.",
        "attrs": {"compensation": {"basic_salary": 10750, "salary_state": "listed"}},
    })
    rec = async_to_sync(enrich_one)(inp, client)
    assert rec.status == "ok"
    assert rec.doc_type == "job"
    assert rec.attrs["compensation"]["basic_salary"] == 10750
    assert rec.model_name == "stub-model"
    assert rec.attempts == 1


@pytest.mark.django_db
def test_low_confidence_override_loses_to_the_prior(gazette_job):
    inp = build_input("gazette", "IUL-1")
    client = _StubClient({"doc_type": "shopping", "doc_type_confidence": 0.4,
                          "attrs": {}})
    rec = async_to_sync(enrich_one)(inp, client)
    assert rec.doc_type == "job"          # the prior wins


@pytest.mark.django_db
def test_an_ungrounded_salary_is_dropped_and_recorded(gazette_job):
    inp = build_input("gazette", "IUL-1")
    client = _StubClient({
        "doc_type": "job",
        "attrs": {"compensation": {"basic_salary": 99999, "salary_state": "listed"}},
    })
    rec = async_to_sync(enrich_one)(inp, client)
    assert rec.attrs["compensation"]["basic_salary"] is None
    assert rec.validation["dropped"]


@pytest.mark.django_db
def test_a_provider_failure_records_failed_and_does_not_raise(gazette_job):
    from enrich.client import ProviderError

    class _Broken:
        async def run_chain(self, messages, rebuild=None):
            raise ProviderError("all stages failed")
        async def aclose(self):
            pass

    inp = build_input("gazette", "IUL-1")
    rec = async_to_sync(enrich_one)(inp, _Broken())
    assert rec.status == "failed"
    assert "all stages failed" in rec.error


# --- selection gates ----------------------------------------------------

@pytest.mark.django_db
def test_a_matching_hash_and_prompt_version_is_skipped():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="h",
                                  doc_type="shopping", status="ok", prompt_version=1)
    assert list(select_keys(source="ibay", prompt_version=1)) == []


@pytest.mark.django_db
def test_a_changed_hash_re_enriches():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="NEW")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="OLD",
                                  doc_type="shopping", status="ok", prompt_version=1)
    assert list(select_keys(source="ibay", prompt_version=1)) == [("ibay", "1")]


@pytest.mark.django_db
def test_a_prompt_version_bump_re_enriches_ibay():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="h",
                                  doc_type="shopping", status="ok", prompt_version=1)
    assert list(select_keys(source="ibay", prompt_version=2)) == [("ibay", "1")]


@pytest.mark.django_db
def test_a_prompt_version_bump_does_not_backfill_gazette():
    """Spec 5.7: gazette documents are write-once. Improving the prompt
    improves only newly-ingested iulaan, by design. Without this gate a
    version bump would re-bill 51,000 documents."""
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="gazette", source_key="IUL-1",
                                  content_hash="h", doc_type="news", status="ok",
                                  prompt_version=1)
    assert list(select_keys(source="gazette", prompt_version=2)) == []


@pytest.mark.django_db
def test_stale_marked_overrides_every_gate_including_gazette():
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x", content_hash="h",
                                  stale_marked_at=timezone.now())
    EnrichedRecord.objects.create(source="gazette", source_key="IUL-1",
                                  content_hash="h", doc_type="news", status="ok",
                                  prompt_version=1)
    assert list(select_keys(source="gazette", prompt_version=1)) == [
        ("gazette", "IUL-1")]


@pytest.mark.django_db
def test_only_stale_selects_nothing_when_nothing_is_marked():
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x", content_hash="h")
    assert list(select_keys(source="gazette", prompt_version=1,
                            only_stale=True)) == []


@pytest.mark.django_db
def test_failed_records_are_retried_up_to_the_attempt_cap():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="h",
                                  doc_type="shopping", status="failed",
                                  prompt_version=1, attempts=1)
    assert list(select_keys(source="ibay", prompt_version=1)) == [("ibay", "1")]

    EnrichedRecord.objects.update(attempts=5)
    assert list(select_keys(source="ibay", prompt_version=1)) == []
