import pytest

from search.models import SearchDocument, SuggestTerm
from search.suggest import rebuild_terms, suggest


@pytest.fixture
def indexed(db):
    for i, (t_en, t_dv, dtype) in enumerate([
        ("iPhone 13 Pro Max", "", "shopping"),
        ("iPhone 12", "", "shopping"),
        ("Samsung Galaxy", "", "shopping"),
        ("", "ވަޒީފާގެ ފުރުޞަތު", "job"),
        ("", "ވަޒީފާގެ ފުރުޞަތު", "job"),
    ], start=1):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type=dtype, url="https://x",
                                      title_en=t_en, title_dv=t_dv)
    rebuild_terms()


@pytest.mark.django_db
def test_rebuild_extracts_terms_with_frequencies(indexed):
    iphone = SuggestTerm.objects.get(term="iphone")
    assert iphone.frequency == 2
    assert iphone.script == "latin"


@pytest.mark.django_db
def test_thaana_terms_are_recorded_with_their_script(indexed):
    assert SuggestTerm.objects.filter(script="thaana").exists()


@pytest.mark.django_db
def test_suggest_prefix_match_ranks_by_frequency(indexed):
    out = [s["term"] for s in suggest("ipho")]
    assert out[0] == "iphone"


@pytest.mark.django_db
def test_suggest_survives_a_typo_via_trigram(indexed):
    assert "iphone" in [s["term"] for s in suggest("ihpone")]


@pytest.mark.django_db
def test_suggest_works_in_thaana(indexed):
    assert suggest("ވަޒީފާ")


@pytest.mark.django_db
def test_suggest_returns_the_doc_type_a_term_is_most_common_in(indexed):
    """So the frontend can render 'iphone -- in Shopping' with the tab icon."""
    assert suggest("ipho")[0]["doc_type"] == "shopping"


@pytest.mark.django_db
def test_a_single_character_query_returns_nothing():
    """Trigram similarity on one character matches everything."""
    assert suggest("i") == []


@pytest.mark.django_db
def test_rebuild_is_idempotent(indexed):
    before = SuggestTerm.objects.count()
    rebuild_terms()
    assert SuggestTerm.objects.count() == before
