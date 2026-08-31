import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone

from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import SearchDocument
from search.query import search_page


def _listing(key, title, *, days_ago, seller="s1", price=500):
    # Through the indexer so dedupe_key is populated exactly as in production.
    upsert_drafts([DocumentDraft(
        source="other", source_key=key, doc_type="property",
        url=f"https://x/{key}", title_en=title, price=price,
        published_at=timezone.now() - dt.timedelta(days=days_ago),
        attrs={"seller_id": seller},
        card={"seller_name": seller},
    )])
    return SearchDocument.objects.get(source_key=key)


@pytest.mark.django_db
def test_only_the_most_recent_of_a_group_survives():
    _listing("1", "Room for daily rent 9940965", days_ago=5)
    keep = _listing("2", "Room for daily rent 9940965", days_ago=0)
    _listing("3", "Room for daily rent 9940965", days_ago=3)
    call_command("dedupe_listings")
    live = SearchDocument.objects.filter(is_duplicate=False)
    assert [d.source_key for d in live] == [keep.source_key]


@pytest.mark.django_db
def test_the_survivor_records_how_many_it_represents():
    for i in range(4):
        _listing(str(i), "Room for daily rent 9940965", days_ago=i)
    call_command("dedupe_listings")
    kept = SearchDocument.objects.get(is_duplicate=False)
    assert kept.duplicate_count == 4


@pytest.mark.django_db
def test_duplicates_are_flagged_never_deleted():
    """Spec 12.6: nothing is destroyed."""
    for i in range(3):
        _listing(str(i), "Same title here", days_ago=i)
    call_command("dedupe_listings")
    assert SearchDocument.objects.count() == 3
    assert SearchDocument.objects.filter(is_duplicate=True).count() == 2


@pytest.mark.django_db
def test_flagged_duplicates_do_not_appear_in_search():
    for i in range(3):
        _listing(str(i), "Room for daily rent 9940965", days_ago=i)
    call_command("reindex_vectors")
    call_command("dedupe_listings")
    page = search_page("room daily rent", doc_type="property")
    assert page.total == 1


@pytest.mark.django_db
def test_different_sellers_with_the_same_title_are_not_collapsed():
    """Two landlords may honestly advertise 'Room for rent'. The key includes
    the seller, so this collapses reposts, not competitors."""
    _listing("1", "Room for rent", days_ago=1, seller="a")
    _listing("2", "Room for rent", days_ago=0, seller="b")
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 2


@pytest.mark.django_db
def test_a_different_price_is_a_different_listing():
    _listing("1", "iPhone 13", days_ago=1, price=9000)
    _listing("2", "iPhone 13", days_ago=0, price=14000)
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 2


@pytest.mark.django_db
def test_titles_differing_only_in_case_and_punctuation_are_one_group():
    """'MN-2 ROOM FOR DAILY/HOURLY RENT' and 'MN-2 Room for Daily/Hourly Rent'
    are the same ad."""
    _listing("1", "MN-2 ROOM FOR DAILY/HOURLY RENT.", days_ago=1)
    _listing("2", "MN-2 Room for Daily / Hourly Rent", days_ago=0)
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 1


@pytest.mark.django_db
def test_rerunning_is_idempotent():
    for i in range(3):
        _listing(str(i), "Same", days_ago=i)
    call_command("dedupe_listings")
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 1


@pytest.mark.django_db
def test_a_newly_synced_repost_becomes_the_survivor():
    """The seller reposts tomorrow. After the next dedupe the new row is the
    live one and yesterday's is flagged -- the flag is recomputed, not sticky."""
    old = _listing("1", "Room for rent", days_ago=1)
    call_command("dedupe_listings")
    _listing("2", "Room for rent", days_ago=0)
    call_command("dedupe_listings")
    old.refresh_from_db()
    assert old.is_duplicate is True
    assert SearchDocument.objects.get(is_duplicate=False).source_key == "2"


@pytest.mark.django_db
def test_gazette_documents_are_never_deduplicated():
    """Two councils may publish identically-titled notices. A published
    government notice is not a repost, and gazette is write-once (spec 5.7)."""
    SearchDocument.objects.create(source="gazette", source_key="A",
                                  doc_type="news", url="https://x/a",
                                  title_en="Public Information")
    SearchDocument.objects.create(source="gazette", source_key="B",
                                  doc_type="news", url="https://x/b",
                                  title_en="Public Information")
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=True).count() == 0
