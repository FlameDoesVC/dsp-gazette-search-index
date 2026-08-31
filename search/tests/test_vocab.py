import pytest
from django.utils import translation

from core.models import TranslationCache
from search.vocab import (
    annotate_free_text, annotate_labels, bilingual_label, canonical, label,
)


@pytest.mark.parametrize("raw,key", [
    ("Full-time", "full_time"), ("Full time", "full_time"),
    ("FULL TIME", "full_time"), ("  Permanent  ", "permanent"),
])
def test_spelling_variants_canonicalise_to_one_key(raw, key):
    """The consistency failure this exists to prevent: a per-document
    translator would give each variant its own Dhivehi spelling."""
    assert canonical(raw) == key


def test_label_resolves_in_english():
    with translation.override("en"):
        assert label("position_type", "Full time") == "Full-time"


def test_label_resolves_in_dhivehi():
    with translation.override("dv"):
        out = label("position_type", "Permanent")
        assert out and out != "Permanent", "dv catalog entry missing"


def test_an_unknown_value_falls_back_to_itself_not_to_blank():
    assert label("position_type", "Seasonal") == "Seasonal"


def test_an_unknown_field_is_passed_through():
    assert label("nonexistent_field", "x") == "x"


def test_bilingual_label_returns_both_sides_regardless_of_active_locale():
    """The active locale when a card is *enriched* must not decide the label
    forever -- see the enrichment-time bug this replaces."""
    with translation.override("dv"):
        en, dv = bilingual_label("position_type", "Permanent")
    assert en == "Permanent"
    assert dv and dv != "Permanent"


def test_annotate_labels_adds_both_sides_for_the_doc_types_fields():
    card = {"position_type": "Permanent", "job_category": "Medical"}
    out = annotate_labels("job", card)
    assert out["position_type_label_en"] == "Permanent"
    assert out["position_type_label_dv"] and out["position_type_label_dv"] != "Permanent"
    assert out["job_category_label_en"] == "Medical"
    # original card is untouched
    assert "position_type_label_en" not in card


def test_annotate_labels_skips_a_missing_value():
    out = annotate_labels("job", {"position_type": "", "job_category": None})
    assert "position_type_label_en" not in out
    assert "job_category_label_en" not in out


def test_annotate_labels_is_a_no_op_for_a_doc_type_with_no_vocab_fields():
    card = {"foo": "bar"}
    assert annotate_labels("unknown_doc_type", card) is card


def _cache(text: str, translated: str) -> None:
    import hashlib
    TranslationCache.objects.create(
        source_hash=hashlib.sha256(text.encode()).hexdigest(),
        translated_text=translated,
    )


@pytest.mark.django_db
def test_annotate_free_text_is_a_no_op_for_a_non_job_doc_type():
    card = {"role": "Technician"}
    assert annotate_free_text("news", card) is card


@pytest.mark.django_db
def test_annotate_free_text_fills_role_qualifications_and_required_documents():
    _cache("Laboratory Technician", "ލެބޯޓްރީ ޓެކްނީޝަން")
    _cache("A related degree", "ގުޅުންހުރި ދާއިރާއަކުން ޑިގްރީއެއް")
    card = {
        "role": "Laboratory Technician",
        "qualifications": ["A related degree"],
        "required_documents": ["A related degree"],
    }
    out = annotate_free_text("job", card)
    assert out["role_dv"] == "ލެބޯޓްރީ ޓެކްނީޝަން"
    assert out["qualifications_dv"] == ["ގުޅުންހުރި ދާއިރާއަކުން ޑިގްރީއެއް"]
    assert out["required_documents_dv"] == ["ގުޅުންހުރި ދާއިރާއަކުން ޑިގްރީއެއް"]


@pytest.mark.django_db
def test_annotate_free_text_is_a_no_op_when_nothing_is_cached_yet():
    """Before `translate_card_vocab` has ever run for a string, the card comes
    back untouched -- the frontend's own English fallback covers this, and
    there is no reason to emit a field full of empty strings."""
    card = {"qualifications": ["Never translated"]}
    assert annotate_free_text("job", card) is card


@pytest.mark.django_db
def test_annotate_free_text_fills_employer():
    _cache("The Maldives National University", "ދިވެހިރާއްޖޭގެ ޤައުމީ ޔުނިވަރސިޓީ")
    card = {"employer": "The Maldives National University"}
    out = annotate_free_text("job", card)
    assert out["employer_dv"] == "ދިވެހިރާއްޖޭގެ ޤައުމީ ޔުނިވަރސިޓީ"


@pytest.mark.django_db
def test_annotate_free_text_leaves_an_untranslated_sibling_item_as_an_empty_string():
    """One item cached, one not: the list still comes back index-aligned."""
    _cache("Translated one", "ތަރުޖަމާކުރެވިފައި")
    card = {"qualifications": ["Translated one", "Not yet translated"]}
    out = annotate_free_text("job", card)
    assert out["qualifications_dv"] == ["ތަރުޖަމާކުރެވިފައި", ""]


@pytest.mark.django_db
def test_annotate_free_text_fills_allowance_and_apply_method_labels():
    _cache("Service Allowance", "ޚިދުމަތު އިނާޔަތް")
    _cache("Online via form link", "ފޯމު މެދުވެރިކޮށް")
    card = {
        "compensation": {"allowances": [{"kind": "service", "label_raw": "Service Allowance"}]},
        "apply_methods": [{"kind": "form", "label_en": "Online via form link", "label_dv": ""}],
    }
    out = annotate_free_text("job", card)
    assert out["compensation"]["allowances"][0]["label_dv"] == "ޚިދުމަތު އިނާޔަތް"
    assert out["apply_methods"][0]["label_dv"] == "ފޯމު މެދުވެރިކޮށް"


@pytest.mark.django_db
def test_annotate_free_text_never_overwrites_an_apply_method_label_the_model_already_gave():
    _cache("Online via form link", "ފޯމު މެދުވެރިކޮށް")
    card = {"apply_methods": [{"kind": "form", "label_en": "Online via form link",
                               "label_dv": "މޮޑެލް ދިން ލޭބަލް"}]}
    out = annotate_free_text("job", card)
    assert out["apply_methods"][0]["label_dv"] == "މޮޑެލް ދިން ލޭބަލް"
