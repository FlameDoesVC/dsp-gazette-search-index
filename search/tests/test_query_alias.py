import pytest
from django.conf import settings
from search.models import QueryAlias


@pytest.mark.django_db
def test_alias_expands_a_term():
    QueryAlias.objects.create(term="phone", expands_to=["mobile", "ފޯނު"])
    alias = QueryAlias.objects.get(term="phone")
    assert "ފޯނު" in alias.expands_to


@pytest.mark.django_db
def test_term_is_unique():
    from django.db import IntegrityError
    QueryAlias.objects.create(term="phone", expands_to=["mobile"])
    with pytest.raises(IntegrityError):
        QueryAlias.objects.create(term="phone", expands_to=["handset"])


def test_ranking_weights_are_configured():
    r = settings.SEARCH_RANKING
    for key in ("w_en", "w_dv", "w_latin", "w_trigram", "w_same_lang",
                "w_freshness", "w_quality", "w_phrase"):
        assert key in r


def test_freshness_half_lives_cover_every_doc_type():
    hl = settings.SEARCH_RANKING["freshness_half_life_days"]
    assert hl == {"news": 7, "job": 14, "shopping": 30, "property": 45}


def test_dv_index_mode_defaults_to_dual():
    assert settings.SEARCH_DV_INDEX_MODE == "dual"
