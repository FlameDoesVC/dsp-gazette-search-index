"""The stale refresh has to actually clear the staleness.

`Product.updated_at` is `auto_now=True`, and auto_now is applied by
`Model.save()`, not by `QuerySet.update()`. `fetch_product_detail` writes with
`aupdate()`, so every refresh rewrote the price, description, images and info
rows while leaving `updated_at` where it was. The product therefore never left
`update_stale_products`'s queryset: measured 359 products whose `updated_at` sat
between 2026-05-26 and 2026-05-31 while their ProductImage rows had been
deleted and recreated minutes earlier.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ibay.models import Product
from ibay.sync_service import STALE_DAYS, fetch_product_detail


class _Response:
    status_code = 200
    # No seller element, no breadcrumbs: the parse falls through to the update
    # without reaching for the network again.
    text = "<html><body><div class='iw-description-div'>body</div></body></html>"


class _Client:
    async def get(self, url, **kwargs):
        return _Response()


def _stale_product():
    product = Product.objects.create(
        listing_id=999001, name="Stale item",
        url="https://ibay.com.mv/index.php?page=item&id=999001",
        status="SCRAPED",
    )
    old = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS + 30)
    # bypass auto_now to plant the old timestamp
    Product.objects.filter(id=product.id).update(updated_at=old)
    return product.id, old


@pytest.mark.django_db
def test_queryset_update_leaves_auto_now_alone():
    """The Django behaviour the bug rested on. If this ever fails, the explicit
    timestamp in fetch_product_detail is redundant and can go."""
    product_id, old = _stale_product()
    Product.objects.filter(id=product_id).update(description="changed")
    assert Product.objects.get(id=product_id).updated_at == old


# `transaction=True`: the async ORM runs each write in a worker thread on its
# own connection, which cannot see rows still uncommitted on the test's
# connection. Without it `filter(id=...).aupdate()` matches zero rows and the
# test passes or fails for reasons that have nothing to do with the code.
@pytest.mark.django_db(transaction=True)
def test_a_refreshed_product_leaves_the_stale_queryset():
    product_id, old = _stale_product()
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    due = Product.objects.filter(status="SCRAPED", updated_at__lt=cutoff)
    assert product_id in set(due.values_list("id", flat=True))

    asyncio.run(fetch_product_detail(
        _Client(), asyncio.Semaphore(1), product_id, 999001,
        "Stale item", "https://ibay.com.mv/index.php?page=item&id=999001",
    ))

    refreshed = Product.objects.get(id=product_id)
    assert refreshed.updated_at > old
    assert product_id not in set(due.values_list("id", flat=True))


@pytest.mark.django_db(transaction=True)
def test_a_dead_listing_also_stops_being_stale():
    """A 404 marks the row ERROR, which drops it from the SCRAPED filter, but
    the timestamp still has to move: otherwise 'when did we last ask about
    this' reads three months stale on a row we just checked."""
    product_id, old = _stale_product()

    class _Gone(_Response):
        status_code = 404

    class _GoneClient(_Client):
        async def get(self, url, **kwargs):
            return _Gone()

    asyncio.run(fetch_product_detail(
        _GoneClient(), asyncio.Semaphore(1), product_id, 999001,
        "Stale item", "https://ibay.com.mv/index.php?page=item&id=999001",
    ))

    refreshed = Product.objects.get(id=product_id)
    assert refreshed.status == "ERROR"
    assert refreshed.updated_at > old
