import pytest

from catalog.models import Brand, Entity, EntityLink
from catalog.resolve import resolve_document, resolve_source
from search.models import Category, SearchDocument, SourceCategoryMap
from search.taxonomy import path_key


@pytest.fixture
def fixtures(db):
    Brand.objects.create(name="Samsung")
    family = Category.objects.create(key="mobile", label_en="Mobile", tier="family")
    phones = Category.objects.create(key="mobile-phones", label_en="Mobile Phones",
                                     parent=family, tier="primary")
    wiring = Category.objects.create(key="electrical-wiring",
                                     label_en="Electrical & Wiring",
                                     parent=family, tier="service")
    for path, node in [
        (["For Sale", "Mobile Phones & Accessories", "Mobile Phones"], phones),
        (["Services", "Repairs, Maintenance & Household Work",
          "Electrical & Wiring"], wiring),
    ]:
        SourceCategoryMap.objects.create(source="ibay", path=path,
                                         path_key=path_key("ibay", path),
                                         category=node)
    return {"phones": phones, "wiring": wiring}


def make_doc(source_key, title, path, **kw):
    return SearchDocument.objects.create(
        source="ibay", source_key=source_key, doc_type="shopping",
        url=f"https://ibay.com.mv/{source_key}", title_en=title,
        attrs={"category_path": path}, card=kw.pop("card", {}), **kw)


@pytest.mark.django_db
def test_two_listings_of_the_same_product_share_one_entity(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    a = make_doc("1", "Samsung Galaxy A15 128GB brand new 7438649", path)
    b = make_doc("2", "SAMSUNG GALAXY A15 128GB free delivery 9663178", path)
    ea, eb = resolve_document(a), resolve_document(b)
    assert ea is not None and ea.pk == eb.pk
    assert ea.kind == "product"
    assert Entity.objects.count() == 1


@pytest.mark.django_db
def test_a_listing_with_no_identity_gets_no_entity(fixtures):
    doc = make_doc("3", "Excellent condition item for sale",
                   ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"])
    assert resolve_document(doc) is None
    assert Entity.objects.count() == 0


@pytest.mark.django_db
def test_service_listings_group_by_phone_not_by_seller(fixtures):
    """Measured: 781 distinct phones against 946 seller accounts, so one
    operator posts under several accounts and the phone merges them."""
    path = ["Services", "Repairs, Maintenance & Household Work",
            "Electrical & Wiring"]
    a = make_doc("4", "Electrician wiring repair 7438649", path,
                 contact_phone="7438649", card={"seller_name": "Miabulbul"})
    b = make_doc("5", "Room light board installation 7438649", path,
                 contact_phone="7438649", card={"seller_name": "OtherAccount"})
    ea, eb = resolve_document(a), resolve_document(b)
    assert ea.pk == eb.pk
    assert ea.kind == "service"
    assert ea.provider_key == "7438649"


@pytest.mark.django_db
def test_a_service_with_no_phone_falls_back_to_the_seller(fixtures):
    path = ["Services", "Repairs, Maintenance & Household Work",
            "Electrical & Wiring"]
    doc = make_doc("6", "Wiring work", path, card={"seller_name": "Markspencer"})
    entity = resolve_document(doc)
    assert entity is not None
    assert entity.provider_key == "seller:Markspencer"


@pytest.mark.django_db
def test_resolution_is_idempotent(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("7", "Samsung Galaxy A15 128GB", path)
    first = resolve_document(doc)
    second = resolve_document(doc)
    assert first.pk == second.pk
    assert EntityLink.objects.filter(source="ibay", source_key="7").count() == 1


@pytest.mark.django_db
def test_a_document_links_to_at_most_one_entity(fixtures):
    """Re-resolving after the title changes moves the link, never duplicates."""
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("8", "Samsung Galaxy A15 128GB", path)
    resolve_document(doc)
    doc.title_en = "Samsung Galaxy A25 256GB"
    doc.save(update_fields=["title_en"])
    resolve_document(doc)
    assert EntityLink.objects.filter(source="ibay", source_key="8").count() == 1
    assert Entity.objects.count() == 2


@pytest.mark.django_db
def test_listing_count_is_maintained(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    make_doc("9", "Samsung Galaxy A15 128GB", path)
    make_doc("10", "Samsung Galaxy A15 128GB used", path)
    counts = resolve_source("ibay")
    entity = Entity.objects.get(kind="product")
    assert entity.listing_count == 2
    assert counts["linked"] == 2


@pytest.mark.django_db
def test_an_unmapped_category_still_resolves(fixtures):
    """The mapped key contributes the empty string; identity carries the key."""
    doc = make_doc("11", "Samsung Galaxy A15 128GB",
                   ["For Sale", "Never Reviewed"])
    assert resolve_document(doc) is not None


# --------------------------------------------------------------------------
# Scope. Spec section 2 covers For Sale and Services only, and without an
# explicit gate 714 property listings resolved as products on the live corpus.
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_property_listing_is_out_of_scope(fixtures):
    """'FACE2' in a real apartment title looks exactly like a model designator,
    so identity extraction alone cannot exclude housing."""
    doc = SearchDocument.objects.create(
        source="ibay", source_key="p1", doc_type="property",
        url="https://x/p1", title_en="3ROOM APARTMENT @HULHUMALE FACE2 VINARES",
        attrs={"category_path": ["Housing & Real Estate",
                                 "Apartments & Houses for Rent"]})
    assert resolve_document(doc) is None
    assert Entity.objects.count() == 0


@pytest.mark.django_db
def test_a_job_listing_is_out_of_scope(fixtures):
    doc = SearchDocument.objects.create(
        source="ibay", source_key="j1", doc_type="job", url="https://x/j1",
        title_en="Accountant required GS3 grade", attrs={"category_path": ["Jobs"]})
    assert resolve_document(doc) is None


@pytest.mark.django_db
def test_a_shopping_listing_with_no_category_path_is_out_of_scope(fixtures):
    """106 such documents resolved as products before the gate existed. With no
    path there is no way to tell a product from a service."""
    doc = SearchDocument.objects.create(
        source="ibay", source_key="e1", doc_type="shopping", url="https://x/e1",
        title_en="Samsung Galaxy A15 128GB", attrs={})
    assert resolve_document(doc) is None


@pytest.mark.django_db
def test_wanted_and_business_opportunities_are_out_of_scope(fixtures):
    for i, root in enumerate(["Wanted", "Business Opportunities", "Free Stuff"]):
        doc = SearchDocument.objects.create(
            source="ibay", source_key=f"w{i}", doc_type="shopping",
            url=f"https://x/w{i}", title_en="Samsung Galaxy A15 128GB wanted",
            attrs={"category_path": [root, "Other Stuff"]})
        assert resolve_document(doc) is None, root


@pytest.mark.django_db
def test_for_sale_and_services_remain_in_scope(fixtures):
    """The gate must not be so tight that it excludes the corpus it is for."""
    from catalog.resolve import in_scope
    sale = SearchDocument.objects.create(
        source="ibay", source_key="s1", doc_type="shopping", url="https://x/s1",
        title_en="Samsung Galaxy A15 128GB",
        attrs={"category_path": ["For Sale", "Mobile Phones & Accessories",
                                 "Mobile Phones"]})
    svc = SearchDocument.objects.create(
        source="ibay", source_key="s2", doc_type="shopping", url="https://x/s2",
        title_en="Wiring repair 7438649", contact_phone="7438649",
        attrs={"category_path": ["Services", "Repairs, Maintenance & Household Work",
                                 "Electrical & Wiring"]})
    assert in_scope(sale) and in_scope(svc)
    assert resolve_document(sale) is not None
    assert resolve_document(svc) is not None


# --------------------------------------------------------------------------
# Discriminating identity. The golden set scored 50% on products without this:
# brand-only grouped 214 Apple accessories, and the token PS5 grouped 291 games.
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_a_brand_with_no_model_designator_forms_no_entity(fixtures):
    """A brand is a category, not an identity. 'Apple' as the only signal put
    214 different accessories in one entity on the live corpus."""
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("d1", "Apple original accessory brand new", path)
    assert resolve_document(doc, stopwords=set()) is None


@pytest.mark.django_db
def test_a_platform_token_forms_no_entity(fixtures):
    """PS5 appears in 426 For Sale listings; it names a console, not a game."""
    path = ["For Sale", "Video & Computer Gaming", "Games"]
    # Both titles must carry NO designator other than the platform token.
    # 'Alan Wake 2' used to sit here and now correctly yields WAKE-2: a numbered
    # game title is a real product identity, so it was the fixture that was
    # wrong, not the rule. Both of these are real corpus titles.
    a = make_doc("d2", "Immortals Fenyx Rising - PS5 Brand New Sealed", path)
    b = make_doc("d3", "Silent Hill f - PS5 Brand New Sealed PS5 Game", path)
    assert resolve_document(a, stopwords={"PS5"}) is None
    assert resolve_document(b, stopwords={"PS5"}) is None
    assert Entity.objects.count() == 0


@pytest.mark.django_db
def test_a_real_model_designator_still_resolves(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("d4", "SONY WH-1000XM5 Noise Cancelling Headset", path)
    entity = resolve_document(doc, stopwords={"PS5", "256GB"})
    assert entity is not None
    assert "WH-1000XM5" in entity.model_name


@pytest.mark.django_db
def test_a_stopword_token_is_dropped_from_the_key_not_the_listing(fixtures):
    """'Galaxy A15 256GB' keys on A15; 256GB is a capacity 163 listings share.
    The listing still resolves -- only the useless token is discarded."""
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("d5", "Samsung Galaxy A15 256GB brand new", path)
    entity = resolve_document(doc, stopwords={"256GB"})
    assert entity is not None
    assert "256GB" not in entity.model_name
    assert "A15" in entity.model_name


@pytest.mark.django_db
def test_identity_stopwords_are_derived_from_document_frequency(db):
    """Derived, not curated: a blocklist needs an entry per new console."""
    from catalog.identity import clear_stopword_cache, identity_stopwords
    from django.test import override_settings

    path = ["For Sale", "Video & Computer Gaming", "Games"]
    for i in range(4):
        make_doc(f"f{i}", f"Game Title {i} - PS9 Sealed X{i}00", path)
    clear_stopword_cache()
    with override_settings(CATALOG_IDENTITY_STOPWORD_DF=3):
        sw = identity_stopwords(refresh=True)
    clear_stopword_cache()
    assert "PS9" in sw           # in all four listings
    assert "X100" not in sw      # in exactly one


@pytest.mark.django_db
def test_a_pass_prunes_entities_that_kept_no_listings():
    """Changing identity extraction re-keys entities and abandons the old rows.
    They inflate the entity count the profiling spend is estimated from, and
    build_profiles selects them before finding they have nothing to profile."""
    from catalog.models import Entity
    from catalog.resolve import resolve_source

    abandoned = Entity.objects.create(
        kind="product", key="stale-key-with-no-links", model_name="GONE-1")
    counts = resolve_source("ibay")

    assert counts["pruned"] >= 1
    assert not Entity.objects.filter(id=abandoned.id).exists()


@pytest.mark.django_db
def test_a_dry_run_prunes_nothing():
    from catalog.models import Entity
    from catalog.resolve import resolve_source

    abandoned = Entity.objects.create(
        kind="product", key="stale-key-dry-run", model_name="GONE-2")
    counts = resolve_source("ibay", dry_run=True)

    assert "pruned" not in counts
    assert Entity.objects.filter(id=abandoned.id).exists()


@pytest.mark.django_db
def test_a_creative_title_leaf_resolves_no_product():
    """'PS5' was stopworded because 291 games had landed in one entity.
    Compound designators are exempt from the frequency filter, which is right
    for IPHONE-17-PRO-MAX and wrong for 'PlayStation 5' -- the same platform
    spelled longhand walked back in and took 32 games with it. Frequency cannot
    tell them apart (40 documents against 37), so the category does."""
    from catalog.resolve import TITLE_IDENTITY_LEAVES, resolve_document

    games = Category.objects.create(key="video-computer-gaming-games",
                                    label_en="Games", tier="primary")
    assert games.key in TITLE_IDENTITY_LEAVES
    SourceCategoryMap.objects.create(
        source="ibay", path_key=path_key("ibay", ["For Sale", "Games"]),
        category=games)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="g1", doc_type="shopping",
        url="https://x/g1",
        title_en="PS5 CD Hogwarts Legacy PlayStation 5 Game | 7776828",
        attrs={"category_path": ["For Sale", "Games"]})

    assert resolve_document(doc) is None


@pytest.mark.django_db
def test_a_platform_keyed_accessory_still_resolves():
    """Game Controllers is deliberately not in the set. Its listings are keyed
    on the platform too, and there a DualSense controller is one product."""
    from catalog.resolve import resolve_document

    node = Category.objects.create(key="accessories-game-controllers",
                                   label_en="Game Controllers", tier="accessory")
    SourceCategoryMap.objects.create(
        source="ibay", path_key=path_key("ibay", ["For Sale", "Game Controllers"]),
        category=node)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="c1", doc_type="shopping",
        url="https://x/c1",
        title_en="PlayStation 5 DualSense Wireless Controller (PS5 Joystick)",
        attrs={"category_path": ["For Sale", "Game Controllers"]})

    entity = resolve_document(doc)
    assert entity is not None
    assert "PLAYSTATION-5" in entity.model_name


@pytest.mark.django_db
def test_a_miss_removes_the_link_a_looser_rule_gave():
    """A miss must be able to undo a hit, or a rule tightened later never takes
    effect on what the looser rule already linked. Excluding the Games leaf
    changed nothing on its first run for exactly this reason: 32 games kept the
    PLAYSTATION-5 link they were given before the exclusion existed, while the
    pass cheerfully reported them as missed."""
    from catalog.models import Entity, EntityLink
    from catalog.resolve import resolve_document

    games = Category.objects.create(key="video-computer-gaming-games",
                                    label_en="Games", tier="primary")
    SourceCategoryMap.objects.create(
        source="ibay", path_key=path_key("ibay", ["For Sale", "Games"]),
        category=games)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="g9", doc_type="shopping",
        url="https://x/g9", title_en="PS5 CD Hogwarts Legacy PlayStation 5 Game",
        attrs={"category_path": ["For Sale", "Games"]})
    stale = Entity.objects.create(kind="product", key="platform-key",
                                  model_name="PLAYSTATION-5")
    EntityLink.objects.create(entity=stale, source="ibay", source_key="g9")

    assert resolve_document(doc) is None
    assert not EntityLink.objects.filter(source="ibay", source_key="g9").exists()


@pytest.mark.django_db
def test_an_out_of_scope_document_also_loses_its_link(fixtures):
    """Same rule for the scope gate. 714 property listings once resolved as
    products off strings like 'FACE2'; adding the gate had to actually detach
    them, not just stop making new ones."""
    from catalog.models import Entity, EntityLink
    from catalog.resolve import resolve_document

    doc = SearchDocument.objects.create(
        source="ibay", source_key="p9", doc_type="property",
        url="https://x/p9", title_en="3ROOM APARTMENT @HULHUMALE FACE2 VINARES",
        attrs={"category_path": ["Property", "Apartments"]})
    stale = Entity.objects.create(kind="product", key="face2-key",
                                  model_name="FACE2")
    EntityLink.objects.create(entity=stale, source="ibay", source_key="p9")

    assert resolve_document(doc) is None
    assert not EntityLink.objects.filter(source="ibay", source_key="p9").exists()
