import pytest
from decimal import Decimal
from ibay.models import Product, Seller
from search.adapters.ibay import IbayAdapter


@pytest.fixture
def product(db):
    seller = Seller.objects.create(id=1, name="Test Seller", is_premium=True)
    return Product.objects.create(
        listing_id=6436842,
        name="SG MEN Eau De Toilette 100ml",
        url="https://ibay.com.mv/index.php?page=item&id=6436842",
        seller=seller,
        price=Decimal("280.00"),
        product_location="Male City/Male",
        description="Description\n\nThe freshness of bergamot.",
        status="SCRAPED",
    )


def test_key(product):
    assert IbayAdapter().key == "ibay"


def test_iter_source_keys_yields_listing_ids(product):
    assert "6436842" in list(IbayAdapter().iter_source_keys())


def test_fetch_raw_returns_none_for_unknown_key(product):
    assert IbayAdapter().fetch_raw("999999999") is None


def test_to_document_maps_the_scraped_fields(product):
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert draft.source == "ibay"
    assert draft.source_key == "6436842"
    assert draft.doc_type == "shopping"
    assert draft.title_en == "SG MEN Eau De Toilette 100ml"
    assert draft.price == Decimal("280.00")
    assert draft.location == "Male City/Male"
    assert "bergamot" in draft.text_en


def test_summary_strips_the_description_boilerplate(product):
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert not draft.summary_en.startswith("Description")
    assert len(draft.summary_en) <= 240


def test_card_carries_the_source_key_not_an_icon_path(product):
    """Spec 4.3.3: cards store the registry key; the icon is resolved via /meta."""
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert draft.card["source"] == "ibay"
    assert "icon" not in draft.card


def test_error_status_products_are_inactive(product):
    product.status = "ERROR"
    product.save(update_fields=["status"])
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw("6436842"))
    assert draft.is_active is False


def test_content_hash_changes_with_the_text(product):
    a = IbayAdapter()
    first = a.to_document(a.fetch_raw("6436842")).content_hash
    product.description = "Something else entirely"
    product.save(update_fields=["description"])
    second = a.to_document(a.fetch_raw("6436842")).content_hash
    assert first and second and first != second
