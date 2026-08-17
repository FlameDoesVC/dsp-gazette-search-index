import pytest
from core.models import TranslationCache
from core import translate


@pytest.mark.django_db
def test_cache_round_trips():
    TranslationCache.objects.create(source_hash="abc", translated_text="hello")
    assert TranslationCache.objects.get(source_hash="abc").translated_text == "hello"


def test_is_dhivehi_detects_thaana():
    assert translate.is_dhivehi("ވަޒީފާގެ ފުރުޞަތު") is True
    assert translate.is_dhivehi("Job Opportunity") is False


def test_sentence_boundary_returns_full_length_for_short_text():
    assert translate.sentence_boundary("short") == len("short")


def test_gazette_shim_still_exports_the_same_callables():
    from gazette import translate as legacy
    assert legacy.translate_auto is translate.translate_auto
    assert legacy.is_dhivehi is translate.is_dhivehi
