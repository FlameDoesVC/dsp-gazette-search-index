import pytest
from search.lang import build_query_plan


@pytest.fixture(autouse=True)
def enable_translation(settings):
    settings.SEARCH_TRANSLATE_QUERIES = True


@pytest.fixture
def stub(monkeypatch):
    calls = []

    def fake(text, target_lang=None, **kw):
        calls.append(text)
        return {"preschool teacher": "ޕްރީސްކޫލް ޓީޗަރ"}.get(text, "")

    monkeypatch.setattr("core.translate.translate_auto", fake)
    return calls


@pytest.mark.django_db
def test_an_english_query_gains_dhivehi_terms(stub):
    """Without this an English query cannot reach vector_dv, so no English
    query can ever match a Thaana gazette body."""
    plan = build_query_plan("preschool teacher")
    assert plan.terms_dv, "English query produced no Dhivehi terms"


@pytest.mark.django_db
def test_a_thaana_query_is_not_sent_to_the_translator(stub):
    build_query_plan("ވަޒީފާ")
    assert stub == []


@pytest.mark.django_db
def test_translation_is_cached_across_identical_queries(stub):
    build_query_plan("preschool teacher")
    build_query_plan("preschool teacher")
    assert len(stub) == 1


@pytest.mark.django_db
def test_a_translator_failure_degrades_rather_than_raises(monkeypatch):
    monkeypatch.setattr("core.translate.translate_auto",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    plan = build_query_plan("preschool teacher")
    assert plan.terms_en == ["preschool", "teacher"]   # English search still works


@pytest.mark.django_db
def test_translation_can_be_disabled_for_the_eval_harness(stub):
    """The harness must be able to measure the lexical baseline alone."""
    build_query_plan("preschool teacher", translate=False)
    assert stub == []
