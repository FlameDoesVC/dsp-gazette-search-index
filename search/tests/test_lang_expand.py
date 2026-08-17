import pytest
from search.lang.expand import build_query_plan


def test_english_query_populates_english_terms():
    plan = build_query_plan("washing machine", use_aliases=False)
    assert plan.lang == "en"
    assert "washing" in plan.terms_en
    assert plan.response_lang == "en"


def test_thaana_query_populates_dv_and_latin_terms():
    plan = build_query_plan("ކުއްޔަށް", use_aliases=False)
    assert plan.lang == "dv-Thaa"
    assert plan.terms_dv
    assert plan.terms_latin, "a Thaana query must also probe the latin vector"
    assert plan.response_lang == "dv"


def test_keyboard_query_is_decoded_into_thaana_terms():
    plan = build_query_plan("migotawq", use_aliases=False)
    assert plan.lang == "dv-Keys"
    assert any("މިގޮތ" in t for t in plan.terms_dv)
    assert plan.response_lang == "dv"


def test_phonetic_latin_query_yields_thaana_candidates():
    plan = build_query_plan("kuyyah dhinun", use_aliases=False)
    assert plan.lang == "dv-Latn"
    assert plan.terms_latin
    assert plan.terms_dv, "phonetic latin must probe the dv skeleton"


def test_mixed_query_populates_both_sides():
    plan = build_query_plan("iphone vikkan", use_aliases=False)
    assert "iphone" in plan.terms_en
    assert plan.terms_latin


def test_quoted_phrases_are_extracted_and_not_expanded():
    plan = build_query_plan('"exact phrase" other', use_aliases=False)
    assert plan.phrases == ["exact phrase"]
    assert "other" in plan.terms_en


def test_empty_query_is_empty_everywhere():
    plan = build_query_plan("", use_aliases=False)
    assert not plan.terms_en and not plan.terms_dv and not plan.terms_latin


@pytest.mark.django_db
def test_aliases_are_applied_when_enabled():
    from search.models import QueryAlias
    QueryAlias.objects.create(term="phone", expands_to=["mobile"])
    plan = build_query_plan("phone", use_aliases=True)
    assert "mobile" in plan.terms_en


@pytest.mark.django_db
def test_inactive_aliases_are_ignored():
    from search.models import QueryAlias
    QueryAlias.objects.create(
        term="phone", expands_to=["mobile"], is_active=False
    )
    plan = build_query_plan("phone", use_aliases=True)
    assert "mobile" not in plan.terms_en
