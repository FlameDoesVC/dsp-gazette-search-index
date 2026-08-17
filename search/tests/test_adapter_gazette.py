import pytest
from gazette.models import Iulaan, IulaanType, Office
from search.adapters.gazette import GazetteAdapter


@pytest.fixture
def iulaan(db):
    office = Office.objects.create(name="މިނިސްޓްރީ", translated_name="Ministry")
    jobs = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    return Iulaan.objects.create(
        id="407890",
        title="ވަޒީފާގެ ފުރުޞަތު",
        translated_title="Job Opportunity",
        office=office,
        iulaan_type=jobs,
        additional_info={"ނަންބަރު": "674-A/2026/46"},
        attachments={"iulaan": "https://storage.googleapis.com/x/1.pdf"},
        body='<td><p dir="RTL"><strong>އަސާސީ މުސާރަ:</strong></p></td>'
             '<td><p dir="RTL">މަހަކު 10,750 ރުފިޔާ</p></td>',
        translated_body="Basic salary: 10,750 rufiyaa per month",
    )


def test_key(iulaan):
    assert GazetteAdapter().key == "gazette"


def test_iter_and_fetch_round_trip(iulaan):
    a = GazetteAdapter()
    keys = list(a.iter_source_keys())
    assert "407890" in keys
    assert a.fetch_raw("407890") is not None
    assert a.fetch_raw("does-not-exist") is None


def test_job_type_maps_to_job(iulaan):
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).doc_type == "job"


def test_unmapped_type_falls_back_to_news(iulaan):
    """Spec 5.3: news is the default sink, there is no `unknown` type."""
    iulaan.iulaan_type = IulaanType.objects.create(name="މުބާރާތް")
    iulaan.save(update_fields=["iulaan_type"])
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).doc_type == "news"


def test_missing_type_falls_back_to_news(iulaan):
    iulaan.iulaan_type = None
    iulaan.save(update_fields=["iulaan_type"])
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).doc_type == "news"


def test_html_is_stripped_from_indexed_text(iulaan):
    """Spec 6.2: markup tokens must never reach the tsvector."""
    a = GazetteAdapter()
    draft = a.to_document(a.fetch_raw("407890"))
    for token in ("<td>", "dir=", "strong", "RTL"):
        assert token not in draft.text_dv
    assert "10,750" in draft.text_dv


def test_thaana_title_lands_in_the_dv_field_and_english_in_en(iulaan):
    a = GazetteAdapter()
    draft = a.to_document(a.fetch_raw("407890"))
    assert draft.title_dv == "ވަޒީފާގެ ފުރުޞަތު"
    assert draft.title_en == "Job Opportunity"


def test_url_points_at_the_original(iulaan):
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).url == (
        "https://gazette.gov.mv/iulaan/407890"
    )


def test_card_names_the_source(iulaan):
    a = GazetteAdapter()
    assert a.to_document(a.fetch_raw("407890")).card["source"] == "gazette"
