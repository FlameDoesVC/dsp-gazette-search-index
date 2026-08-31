import pytest
from django.core.management import call_command
from io import StringIO

from search.models import SearchDocument


@pytest.fixture(autouse=True)
def stub_translate(monkeypatch):
    calls = []

    def fake_en(text):
        calls.append(("en", text))
        if "iPhone" in text:
            return None          # the measured pure-brand stall
        return "ދިވެހި " + text[:20]

    def fake_dv(text):
        calls.append(("dv", text))
        return "English: " + text[:20]

    monkeypatch.setattr("core.translate.translate_en_to_dv_sync", fake_en)
    monkeypatch.setattr("core.translate.translate_dv_to_en_sync", fake_dv)
    return calls


def _doc(**kw):
    base = dict(source="other", source_key="1", doc_type="shopping",
                url="https://x", title_en="Washing machine")
    base.update(kw)
    return SearchDocument.objects.create(**base)


@pytest.mark.django_db
def test_a_missing_dhivehi_title_is_filled():
    _doc()
    call_command("fill_bilingual", stdout=StringIO())
    assert SearchDocument.objects.get().title_dv.startswith("ދިވެހި")


@pytest.mark.django_db
def test_a_translation_failure_falls_back_to_english_never_to_empty():
    """An empty title_dv is the defect this task exists to remove. A copy of
    the English is imperfect; a blank is broken."""
    _doc(source_key="2", title_en="iPhone 13 Pro Max 256GB")
    call_command("fill_bilingual", stdout=StringIO())
    d = SearchDocument.objects.get(source_key="2")
    assert d.title_dv == "iPhone 13 Pro Max 256GB"


@pytest.mark.django_db
def test_an_existing_translation_is_never_overwritten(stub_translate):
    _doc(title_dv="ފެންމެޝިން")
    call_command("fill_bilingual", stdout=StringIO())
    assert SearchDocument.objects.get().title_dv == "ފެންމެޝިން"
    assert stub_translate == []


@pytest.mark.django_db
def test_the_english_side_is_filled_from_dhivehi_too():
    _doc(source_key="3", title_en="", title_dv="ވަޒީފާގެ ފުރުޞަތު")
    call_command("fill_bilingual", stdout=StringIO())
    assert SearchDocument.objects.get(source_key="3").title_en


@pytest.mark.django_db
def test_very_short_strings_are_copied_rather_than_translated():
    """'A4', 'XL', '2BR' -- a translator adds nothing and costs a GPU slot."""
    _doc(source_key="4", title_en="XL")
    call_command("fill_bilingual", stdout=StringIO())
    assert SearchDocument.objects.get(source_key="4").title_dv == "XL"


@pytest.mark.django_db
def test_identical_strings_are_translated_once(stub_translate):
    """40% of Other titles are duplicates; TranslationCache should absorb them,
    but the command must not queue the same string twice either."""
    _doc(source_key="5", title_en="Same title")
    _doc(source_key="6", title_en="Same title")
    call_command("fill_bilingual", stdout=StringIO())
    assert stub_translate.count(("en", "Same title")) == 1


@pytest.mark.django_db
def test_dry_run_reports_and_translates_nothing(stub_translate, capsys):
    for i in range(3):
        _doc(source_key=f"d{i}", title_en=f"Thing {i}")
    call_command("fill_bilingual", "--dry-run", stdout=StringIO())
    assert stub_translate == []


@pytest.mark.django_db
def test_vectors_are_rebuilt_so_the_new_text_is_searchable():
    _doc(source_key="7")
    call_command("fill_bilingual", stdout=StringIO())
    assert SearchDocument.objects.get(source_key="7").vector_dv is not None


@pytest.mark.django_db
def test_closed_vocabulary_fields_are_never_sent_to_the_translator(stub_translate):
    """position_type has two distinct values in the whole corpus. Translating
    it per document is both wasteful and inconsistent."""
    SearchDocument.objects.create(
        source="other", source_key="1", doc_type="job", url="https://x",
        title_en="Officer", attrs={"position_type": "Permanent",
                                   "job_category": "Medical"},
    )
    call_command("fill_bilingual", stdout=StringIO())
    sent = {t for _, t in stub_translate}
    assert "Permanent" not in sent
    assert "Medical" not in sent


@pytest.mark.django_db
def test_open_prose_attrs_are_translated_into_dv_siblings(stub_translate):
    SearchDocument.objects.create(
        source="other", source_key="1", doc_type="job", url="https://x",
        title_en="Officer",
        attrs={"role": "Medical Officer", "qualifications": ["MBBS", "Board cert"]},
    )
    call_command("fill_bilingual", stdout=StringIO())
    d = SearchDocument.objects.get()
    assert d.attrs["role_dv"].startswith("ދިވެހި")
    assert d.attrs["qualifications_dv"] == [
        "ދިވެހި MBBS", "ދިވެހި Board cert"
    ]
