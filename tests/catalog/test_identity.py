import pytest

from catalog.identity import (brand_vocabulary, clean_title, match_brand,
                              model_tokens, product_key, service_key)
from catalog.models import Brand


def test_clean_title_strips_the_phone_and_the_marketing():
    """A real corpus title. Everything after the product is noise."""
    raw = "ROSY Light 100W LED Flood Light RL-S07100C | NEW FREE DELIVERY:9445252"
    assert clean_title(raw) == "ROSY Light 100W LED Flood Light RL-S07100C"


def test_clean_title_handles_the_call_suffix_form():
    raw = "Electricain Room Light Board installation. Repair & Services Call. 7438649"
    out = clean_title(raw)
    assert "7438649" not in out
    assert "Call" not in out
    assert out.startswith("Electricain Room Light Board installation")


def test_clean_title_keeps_a_model_number_that_looks_like_noise():
    """RL-S07100C must survive; it is the whole identity."""
    assert "RL-S07100C" in clean_title("ROSY RL-S07100C free delivery 9445252")


def test_model_tokens_require_a_digit():
    tokens = model_tokens("Green Lion 200W PD Multi Ports 10 Charging Station")
    assert "200W" in tokens
    assert "Station" not in tokens


def test_model_tokens_keep_hyphenated_part_numbers():
    assert "RL-S07100C" in model_tokens("ROSY Light RL-S07100C")


def test_model_tokens_are_sorted_and_deduplicated():
    """The key must not depend on word order, or a reposted listing with the
    words rearranged becomes a second entity."""
    assert model_tokens("A15 128GB A15") == model_tokens("128GB A15")


def test_model_tokens_drop_a_bare_year():
    assert model_tokens("Model year 2019 aircon") == []


@pytest.mark.django_db
def test_match_brand_uses_the_vocabulary_not_the_first_token():
    Brand.objects.create(name="Samsung")
    Brand.objects.create(name="Green Lion", aliases=["greenlion"])
    vocab = brand_vocabulary()
    assert match_brand("Brand New Samsung Galaxy A15", vocab) == "Samsung"
    assert match_brand("GreenLion 200W charger", vocab) == "Green Lion"


@pytest.mark.django_db
def test_match_brand_prefers_the_longest_alias():
    Brand.objects.create(name="Lion")
    Brand.objects.create(name="Green Lion")
    vocab = brand_vocabulary()
    assert match_brand("Green Lion charger", vocab) == "Green Lion"


@pytest.mark.django_db
def test_an_unknown_brand_is_empty_not_the_first_word():
    """The prototype's 0% miss rate came from first-token-as-brand. An honest
    miss is required here, because a wrong brand makes a wrong entity."""
    Brand.objects.create(name="Samsung")
    assert match_brand("Excellent condition thing for sale", brand_vocabulary()) == ""


def test_product_key_is_stable_and_order_independent():
    a = product_key("Samsung", ["A15", "128GB"], "mobile-phones")
    b = product_key("samsung", ["128GB", "A15"], "mobile-phones")
    assert a == b
    assert len(a) == 64


def test_product_key_separates_the_same_model_in_different_categories():
    assert product_key("Samsung", ["A15"], "mobile-phones") != \
        product_key("Samsung", ["A15"], "phone-cases")


def test_product_key_tolerates_an_unmapped_category():
    """An unmapped path contributes the empty string, never the classified
    category: the key must not depend on a model call."""
    assert product_key("Samsung", ["A15"], "") != ""


def test_service_key_is_provider_scoped():
    assert service_key("7438649", "electrical-wiring") != \
        service_key("9663178", "electrical-wiring")
    assert service_key("7438649", "electrical-wiring") == \
        service_key("7438649", "electrical-wiring")


# --------------------------------------------------------------------------
# seed_brands alias handling. 'Apple (iPhone)' stored verbatim matches nothing,
# and 60 For Sale titles begin with the word iPhone.
# --------------------------------------------------------------------------

def test_split_alias_extracts_the_parenthetical_contents():
    from catalog.management.commands.seed_brands import _split_alias
    base, aliases = _split_alias("Apple (iPhone)")
    assert base == "Apple"
    assert "iPhone" in aliases
    assert "Apple (iPhone)" in aliases


def test_split_alias_leaves_a_plain_name_alone():
    from catalog.management.commands.seed_brands import _split_alias
    assert _split_alias("Samsung") == ("Samsung", [])


@pytest.mark.django_db
def test_a_title_naming_the_parenthetical_resolves_to_the_brand():
    """The whole point: 'iPhone 13 128GB' must find Apple."""
    Brand.objects.create(name="Apple", aliases=["iPhone", "Apple (iPhone)"])
    assert match_brand("iPhone 13 128GB for sale", brand_vocabulary()) == "Apple"


@pytest.mark.django_db
def test_seed_brands_adds_the_curated_brands_with_no_documentspec_rows():
    """DocumentSpec can only supply brands from the 2,313 listings that carry a
    scraped Brand field, so the curated list is not redundant with it."""
    from django.core.management import call_command
    call_command("seed_brands")
    for name in ("JBL", "DJI", "Sharp", "Anker"):
        assert Brand.objects.filter(name=name).exists(), name


@pytest.mark.parametrize("brand,tokens,expected", [
    ("Samsung", ["A15", "128GB"], 0.9),
    ("", ["SQ905"], 0.8),               # 87.9% of unbranded listings land here
    ("", ["SK-319", "256GB"], 0.8),
    ("Samsung", ["256GB"], 0.7),        # brand, but the token is a spec
    ("Samsung", [], 0.6),
    ("", ["256GB"], 0.4),               # identifies nothing on its own
    ("", ["2A"], 0.4),
])
def test_identity_confidence_grades_model_designators_above_brands(
        brand, tokens, expected):
    from catalog.identity import identity_confidence
    assert identity_confidence(brand, tokens) == expected


def test_strong_tokens_rejects_bare_units():
    from catalog.identity import strong_tokens
    assert strong_tokens(["256GB", "2A", "200CM"]) == []
    assert strong_tokens(["QUEST-2", "256GB"]) == ["QUEST-2"]


# --------------------------------------------------------------------------
# Compound designators. Measured: 8,939 of 13,356 detail-scraped For Sale
# listings had no discriminating identity, and 2,588 of those were this case --
# a product line followed by its number, which model_tokens discarded because a
# bare number is usually a quantity.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Apple iPhone 11 Cover Case + Tempered Glass", ["IPHONE-11"]),
    ("Realme Buds T200 Lite Headphones", ["T200"]),
    ("Xiaomi POCO C71 free delivery", ["C71"]),
])
def test_a_product_line_and_its_number_is_a_designator(title, expected):
    from catalog.identity import discriminating_tokens, model_tokens
    assert discriminating_tokens(model_tokens(title), set()) == expected


def test_a_trailing_qualifier_keeps_variants_apart():
    """Without the qualifier, 'iPhone 11 Pro Max' and 'iPhone 11' collapse into
    one entity, which is the merge the discriminating rule exists to prevent."""
    from catalog.identity import compound_tokens
    assert compound_tokens("Apple iPhone 11 Cover") == ["IPHONE-11"]
    assert compound_tokens("Apple iPhone 11 Pro Max Cover") == ["IPHONE-11-PRO-MAX"]
    assert compound_tokens("Apple iPhone 11 Pro Cover") == ["IPHONE-11-PRO"]


@pytest.mark.parametrize("title", [
    "3 IN 1 BOSS INVERTER WELDING MACHINE",
    "SET 2 pcs kitchen knife",
    "Pack 4 assorted covers",
])
def test_a_quantity_is_not_a_designator(title):
    from catalog.identity import compound_tokens
    assert compound_tokens(title) == []


def test_a_number_belonging_to_the_next_word_is_not_a_designator():
    """'Hisun 2-Burner Gas Stove' produced HISUN-2 before the lookahead existed,
    treating a burner count as a model number."""
    from catalog.identity import compound_tokens, discriminating_tokens, model_tokens
    assert compound_tokens("Hisun 2-Burner Gas Stove") == []
    assert discriminating_tokens(
        model_tokens("Hisun 2-Burner Gas Stove"), set()) == ["2-BURNER"]


def test_a_compound_is_exempt_from_the_frequency_filter():
    """A compound is specific by construction, so frequency says nothing useful
    about it. 'IPHONE-11' shared by 200 case listings is a product family, and
    the mapped category in the entity key separates a case from a phone."""
    from catalog.identity import discriminating_tokens
    assert discriminating_tokens(["IPHONE-11"], {"IPHONE-11"}) == ["IPHONE-11"]
    assert discriminating_tokens(["PS5"], {"PS5"}) == []


def test_a_plus_glyph_is_a_qualifier_not_punctuation():
    """'Redmi Note 15 Pro' and 'Redmi Note 15 Pro+' are different phones.
    Tokenizing dropped the glyph, so both produced NOTE-15-PRO and 29 listings
    across both models landed in one entity."""
    from catalog.identity import compound_tokens
    assert compound_tokens("REDMI NOTE 15 PRO 256GB/8GB 4G") == ["NOTE-15-PRO"]
    assert compound_tokens("REDMI NOTE 15 PRO+ 512GB/12GB 5G") == [
        "NOTE-15-PRO-PLUS"]


def test_a_repeated_qualifier_is_emphasis():
    """One real title writes it both ways at once."""
    from catalog.identity import compound_tokens
    assert compound_tokens("REDMI NOTE 14 PRO+ PLUS 5G 512GB/12GB RAM") == [
        "NOTE-14-PRO-PLUS"]


@pytest.mark.parametrize("title,expected", [
    # '+' between two capacities is a separator, not a qualifier.
    ("Redmi 15 8+256GB Global", ["REDMI-15"]),
    # '+' with spaces around it is a conjunction listing two products.
    ("Apple iPhone 14 Cover Case + Tempered Glass", ["IPHONE-14"]),
])
def test_a_plus_that_is_not_bound_to_a_word_is_left_alone(title, expected):
    from catalog.identity import compound_tokens
    assert compound_tokens(title) == expected
