import pytest
from search.lang import translit as t
from search.lang.normalize import contains_thaana


@pytest.mark.django_db
def test_office_names_transliterate_without_crashing():
    """Golden-file source: Office rows already pair Thaana with English, so
    the corpus supplies free fixtures (spec 6.3). A handful of real names are
    created here because the test database starts empty."""
    from gazette.models import Office

    real_names = [
        "ހައްދުންމަތީ ކުނަހަންދޫ ކައުންސިލްގެ އިދާރާ",
        "ހައްދުންމަތީ ކުނަހަންދޫ ސްކޫލް",
        "ހުރަވީ ސްކޫލް، ހުޅުމާލެ",
        "ވަޒީފާގެ ފުރުޞަތު",
    ]
    for name in real_names:
        Office.objects.create(name=name)

    checked = 0
    for office in Office.objects.exclude(name="").iterator(chunk_size=100):
        if not contains_thaana(office.name):
            continue
        latin = t.translit_dv_to_latin(office.name)
        assert latin
        assert not contains_thaana(latin), f"{office.name!r} -> {latin!r}"
        checked += 1
    assert checked > 0, "no Thaana office names found to check"


@pytest.mark.django_db
def test_transliteration_is_deterministic():
    from gazette.models import Office

    Office.objects.create(name="ހުރަވީ ސްކޫލް")
    Office.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")

    for office in Office.objects.exclude(name="")[:50]:
        assert t.translit_dv_to_latin(office.name) == t.translit_dv_to_latin(
            office.name
        )
