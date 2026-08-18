import pytest
from django.core.management import call_command
from io import StringIO

from gazette.models import IulaanType, Office


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr("core.translate.translate_dv_to_en_sync",
                        lambda t: "EN " + t[:20])


@pytest.mark.django_db
def test_offices_and_types_are_filled():
    Office.objects.create(name="ތިލަދުންމަތީ އުތުރުބުރީ ފިއްލަދޫ ކައުންސިލްގެ އިދާރާ")
    IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    call_command("fill_entity_translations", stdout=StringIO())
    assert Office.objects.get().translated_name.startswith("EN ")
    assert IulaanType.objects.get().translated_name.startswith("EN ")


@pytest.mark.django_db
def test_an_existing_translation_is_not_overwritten():
    Office.objects.create(name="ފިއްލަދޫ", translated_name="Fillhadhoo Council")
    call_command("fill_entity_translations", stdout=StringIO())
    assert Office.objects.get().translated_name == "Fillhadhoo Council"


@pytest.mark.django_db
def test_one_office_translation_serves_every_document_referencing_it():
    """The leverage: 170 translations cover `employer` for all 51,000 iulaan."""
    from gazette.models import Iulaan
    office = Office.objects.create(name="ފިއްލަދޫ ކައުންސިލް")
    for i in range(3):
        Iulaan.objects.create(id=f"IUL-{i}", title="t", office=office,
                              additional_info={}, attachments=[], body="b")
    call_command("fill_entity_translations", stdout=StringIO())
    office.refresh_from_db()
    assert all(i.office.translated_name for i in Iulaan.objects.all())
