import pytest

from search.models import Category, SourceCategoryMap
from search.taxonomy import (family_of, map_path, path_key,
                             primary_sibling_of)


@pytest.fixture
def taxonomy(db):
    phones = Category.objects.create(key="mobile_phones_family",
                                     label_en="Mobile Phones & Accessories",
                                     tier="family")
    primary = Category.objects.create(key="mobile_phones", label_en="Mobile Phones",
                                      parent=phones, tier="primary")
    charger = Category.objects.create(key="phone_charger", label_en="Charger",
                                      parent=phones, tier="accessory")
    laptops = Category.objects.create(key="laptop_family",
                                      label_en="Computer, Tablets & Networking",
                                      tier="family")
    lcharger = Category.objects.create(key="laptop_charger", label_en="Laptop Charger",
                                       parent=laptops, tier="accessory")
    return dict(family=phones, primary=primary, charger=charger,
                laptops=laptops, lcharger=lcharger)


@pytest.mark.django_db
def test_the_tier_is_curated_on_the_node_not_parsed_from_a_path(taxonomy):
    """iBay spells 'Accessories' in its path; another source will not."""
    assert taxonomy["charger"].tier == "accessory"


@pytest.mark.django_db
def test_every_node_resolves_to_its_family(taxonomy):
    assert family_of(taxonomy["charger"]) == taxonomy["family"]
    assert family_of(taxonomy["family"]) == taxonomy["family"]


@pytest.mark.django_db
def test_an_accessory_resolves_to_the_primary_sibling(taxonomy):
    assert primary_sibling_of(taxonomy["charger"]) == taxonomy["primary"]


@pytest.mark.django_db
def test_a_family_with_no_primary_child_returns_none(taxonomy):
    assert primary_sibling_of(taxonomy["lcharger"]) is None


@pytest.mark.django_db
def test_the_map_is_keyed_on_the_full_path_not_the_leaf(taxonomy):
    """The measured defect: two different 'Charger' leaves must not merge."""
    phone_path = ["For Sale", "Mobile Phones & Accessories", "Accessories",
                  "Charger"]
    laptop_path = ["For Sale", "Computer, Tablets & Networking",
                   "Laptop Accessories", "Charger"]
    SourceCategoryMap.objects.create(
        source="ibay", path=phone_path,
        path_key=path_key("ibay", phone_path), category=taxonomy["charger"])
    SourceCategoryMap.objects.create(
        source="ibay", path=laptop_path,
        path_key=path_key("ibay", laptop_path), category=taxonomy["lcharger"])

    assert map_path("ibay", phone_path) == taxonomy["charger"]
    assert map_path("ibay", laptop_path) == taxonomy["lcharger"]


@pytest.mark.django_db
def test_an_unmapped_path_maps_to_none(taxonomy):
    assert map_path("ibay", ["For Sale", "Nothing Like This"]) is None


@pytest.mark.django_db
def test_a_mapped_row_with_no_category_is_legal(taxonomy):
    """'No canonical category for this path' is a decision, not an error."""
    path = ["Services", "Other Services"]
    SourceCategoryMap.objects.create(source="ibay", path=path,
                                     path_key=path_key("ibay", path),
                                     category=None, note="deliberately unmapped")
    assert map_path("ibay", path) is None


@pytest.mark.django_db
def test_path_key_is_order_sensitive_and_stable():
    a = path_key("ibay", ["For Sale", "Games"])
    assert a == path_key("ibay", ["For Sale", "Games"])
    assert a != path_key("ibay", ["Games", "For Sale"])
    assert a != path_key("gazette", ["For Sale", "Games"])
    assert len(a) == 64


# --------------------------------------------------------------------------
# infer_tier: the seed heuristic. Pinned because it was wrong twice before it
# was right, and both wrong versions passed every test above.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    # The family segment names its contents and must not make its children
    # accessories. 507 documents sit on this node.
    (["For Sale", "Mobile Phones & Accessories", "Mobile Phones"], "primary"),
    # An exact 'Accessories' segment below the family does.
    (["For Sale", "Mobile Phones & Accessories", "Accessories", "Charger"],
     "accessory"),
    # Parts is tested first: this path matches both rules.
    (["For Sale", "Mobile Phones & Accessories", "Parts", "Battery"], "part"),
    # A mid-level segment that merely ends with 'Accessories' counts. This is
    # the case an exact-match test misses, and it is the other 'Charger'.
    (["For Sale", "Computer, Tablets & Networking", "Laptop Accessories",
      "Charger"], "accessory"),
    (["For Sale", "Electronics", "Camera, Photo & Video", "Camera Accessories",
      "Lenses"], "accessory"),
    (["For Sale", "Computer, Tablets & Networking", "Accessories & Parts",
      "Motherboard"], "part"),
    # 'Clothing & Accessories' is a family, so a watch stays a product.
    (["For Sale", "Clothing & Accessories", "Watches"], "primary"),
    (["Services", "Repairs, Maintenance & Household Work",
      "Electrical & Wiring"], "service"),
    # Service by leaf name, not by root.
    (["For Sale", "Home & Garden", "Aircon Servicing & Repair"], "service"),
    (["For Sale"], "family"),
])
def test_infer_tier(path, expected):
    from search.management.commands.seed_taxonomy import infer_tier
    assert infer_tier(path) == expected


def test_junk_leaves_are_recognised():
    """1,541 documents sit on an information-free leaf."""
    from search.management.commands.seed_taxonomy import JUNK_LEAF
    for leaf in ("General / Other", "Other", "general / other", "Other Stuff"):
        assert JUNK_LEAF.match(leaf.strip()), leaf
    for leaf in ("Mobile Phones", "Charger", "Other Accessories"):
        assert not JUNK_LEAF.match(leaf.strip()), leaf


# --------------------------------------------------------------------------
# category_label: distinct keys are not enough, because category_leaf is a bare
# string that P9's ranking and spec 8.3's facet override both group on.
# --------------------------------------------------------------------------

def test_a_unique_leaf_label_is_left_alone():
    from search.management.commands.seed_taxonomy import category_label
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    assert category_label(path, colliding=set()) == "Mobile Phones"


def test_a_colliding_leaf_is_qualified_by_its_nearest_useful_ancestor():
    """Measured: 400 phone chargers and 13 laptop chargers shared one bucket."""
    from search.management.commands.seed_taxonomy import category_label
    phone = ["For Sale", "Mobile Phones & Accessories", "Accessories", "Charger"]
    laptop = ["For Sale", "Computer, Tablets & Networking", "Laptop Accessories",
              "Charger"]
    colliding = {"charger"}
    assert category_label(phone, colliding) == "Charger (Mobile Phones & Accessories)"
    assert category_label(laptop, colliding) == "Charger (Laptop Accessories)"
    assert category_label(phone, colliding) != category_label(laptop, colliding)


def test_a_generic_ancestor_is_skipped_when_qualifying():
    """'Charger (Accessories)' would be ambiguous with every other Accessories
    node, so the qualifier climbs past generic segments."""
    from search.management.commands.seed_taxonomy import category_label
    path = ["For Sale", "Mobile Phones & Accessories", "Accessories", "Charger"]
    assert "(Accessories)" not in category_label(path, {"charger"})


def test_qualifying_never_produces_an_empty_or_oversized_label():
    from search.management.commands.seed_taxonomy import category_label
    label = category_label(["Other", "Charger"], {"charger"})
    assert label == "Charger"           # no useful ancestor, so leave it
    long_path = ["For Sale", "x" * 200, "Charger"]
    assert len(category_label(long_path, {"charger"})) <= 128
