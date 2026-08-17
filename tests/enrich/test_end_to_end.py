import pytest
from django.core.management import call_command

from enrich.models import EnrichedRecord
from search.models import SearchDocument


@pytest.mark.django_db(transaction=True)
def test_enrich_then_reindex_puts_a_typed_card_on_the_document(monkeypatch):
    """The full chain: adapter -> enrich -> overlay -> SearchDocument.card.
    This is the test that would catch a break anywhere in the seam between the
    two apps, which is the seam nothing else covers."""
    from gazette.models import Iulaan, IulaanType, Office
    t = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    o = Office.objects.create(name="Ministry of Example")
    Iulaan.objects.create(
        id="IUL-1", title="Administrative Officer", office=o, iulaan_type=t,
        additional_info={}, attachments=[],
        body="Basic salary: 10,750 per month. Attendance allowance 4,400. "
             "Deadline 2026-08-31. Call 3323838.",
    )

    async def _fake_chain(self, messages, rebuild=None):
        return {
            "doc_type": "job", "doc_type_confidence": 0.95,
            "canonical_title_en": "Administrative Officer",
            "summary_en": "A GS3 post at the Ministry of Example.",
            "attrs": {
                "role": "Administrative Officer",
                "compensation": {
                    "basic_salary": 10750, "salary_state": "listed",
                    "pension_applies": True,
                    "allowances": [{"kind": "attendance",
                                    "label_raw": "Attendance allowance",
                                    "amount": 4400, "basis": "fixed_monthly"}],
                },
                "deadline": "2026-08-31",
            },
        }, "stub"

    monkeypatch.setattr("enrich.client.EnrichClient.run_chain", _fake_chain)

    call_command("reindex", "--source", "gazette")
    call_command("enrich_documents", "--source", "gazette", "--type", "job")
    call_command("reindex", "--source", "gazette")

    rec = EnrichedRecord.objects.get()
    assert rec.status == "ok"

    doc = SearchDocument.objects.get()
    assert doc.doc_type == "job"
    assert doc.title_en == "Administrative Officer"
    assert doc.card["salary_display"] == "MVR 10,750 / month"
    assert doc.card["net_estimate"]["value"] == pytest.approx(14397.50)
    assert doc.card["deadline"] == "2026-08-31"
    assert "deadline_state" not in doc.card
