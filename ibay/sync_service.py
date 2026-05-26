import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from ibay.models import Category, Product, ProductCategory, ProductImage, ProductInfo, Seller

logger = logging.getLogger(__name__)

BASE_URL = "https://ibay.com.mv"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/58.0.3029.110 Safari/537.3"
)
STALE_DAYS = 1
CATEGORY_SYNC_INTERVAL_HOURS = 24
DETAIL_SEMAPHORE_LIMIT = 12
LINK_SEMAPHORE_LIMIT = 10
BATCH_SIZE = 20
LINK_BATCH_CONCURRENCY = 3
SYNC_INTERVAL_SECONDS = 600
REQUEST_DELAY = 0.5


async def sync_categories(client: httpx.AsyncClient):
    cat_ids_synced = set()

    async def scrape_category(category_id, parent_id=None, level=0):
        nonlocal cat_ids_synced
        if category_id in cat_ids_synced and level > 0:
            return
        url = f"{BASE_URL}/index.php?page=cat_ajax&id={category_id}"
        response = await client.get(url, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            logger.error("Error fetching %s: Status %d", url, response.status_code)
            return
        cats = response.json()
        if not cats:
            return
        tasks = []
        for item in cats:
            cid, cname = list(item.items())[0]
            cid = int(cid)
            await Category.objects.aupdate_or_create(
                id=cid, defaults={"name": cname, "parent_id": parent_id}
            )
            cat_ids_synced.add(cid)
            logger.info("%s%s (ID: %d)", "  " * level, cname, cid)
            task = asyncio.create_task(scrape_category(cid, cid, level + 1))
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(REQUEST_DELAY)

    await scrape_category(0)


async def sync_product_links(client: httpx.AsyncClient):
    sem = asyncio.Semaphore(LINK_SEMAPHORE_LIMIT)
    parent_cats = [
        c async for c in Category.objects.filter(parent__isnull=True)
    ]
    total_cats = len(parent_cats)
    logger.info("Found %d parent categories for product link sync.", total_cats)

    async def scrape_category_links(category):
        pages_with_data = 0
        while True:
            async with sem:
                url = (
                    f"{BASE_URL}/index.php?page=search&s_res=GO&lite=0"
                    f"&cid={category.id}&hw_num=100&off={pages_with_data}"
                )
                try:
                    response = await client.get(
                        url, headers={"User-Agent": USER_AGENT}, timeout=30
                    )
                    if response.status_code != 200:
                        logger.error("HTTP error %d for %s", response.status_code, url)
                        break
                    soup = BeautifulSoup(response.text, "lxml")
                    items = soup.find_all(class_="bg-light latest-list-item")
                    if not items:
                        break
                    products = []
                    for item in items:
                        link = item.find("div", class_="col m7 s8").h5.a
                        href = link["href"]
                        listing_id = int(
                            href.split("-o")[-1].split(".html")[0]
                        )
                        products.append(
                            Product(
                                listing_id=listing_id,
                                name=link.text.strip(),
                                url=BASE_URL + "/" + href,
                            )
                        )
                    for p in products:
                        await Product.objects.aupdate_or_create(
                            listing_id=p.listing_id,
                            defaults={"name": p.name, "url": p.url},
                        )
                    pages_with_data += 1
                    logger.info(
                        "  %s: scraped %d products (page %d)",
                        category.name, len(products), pages_with_data,
                    )
                    await asyncio.sleep(REQUEST_DELAY)
                except Exception:
                    logger.exception("Error for %s", category.name)
                    break

    tasks = [
        asyncio.create_task(scrape_category_links(c))
        for c in parent_cats
    ]
    batch_size = LINK_BATCH_CONCURRENCY
    for i in range(0, len(tasks), batch_size):
        await asyncio.gather(*tasks[i : i + batch_size])


def extract_seller_id(seller_url: str) -> int | None:
    m = re.search(r"id=(\d+)", seller_url)
    return int(m.group(1)) if m else None


async def fetch_product_detail(client, sem, product_id, listing_id, name, url):
    async with sem:
        try:
            response = await client.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=30
            )
            if response.status_code in (301, 404):
                await Product.objects.filter(id=product_id).aupdate(
                    status="ERROR", error_message=f"Status: {response.status_code}"
                )
                return
            if response.status_code != 200:
                logger.warning("Non-200 for %s: %d", url, response.status_code)
                return

            soup = BeautifulSoup(response.text, "lxml")

            if soup.find("font", class_="pagetitle", text="Listing disabled") or \
               soup.find("p", class_="pagetitle", text="Listing not found"):
                await Product.objects.filter(id=product_id).aupdate(
                    status="ERROR", error_message="Listing disabled or not found"
                )
                logger.warning("Listing %d not available.", listing_id)
                return

            seller_url_el = soup.select_one(".iw-user-name")
            seller_id = None
            if seller_url_el and seller_url_el.get("href"):
                seller_id = extract_seller_id(seller_url_el["href"])
                seller_name_el = seller_url_el.select_one("b")
                seller_name = seller_name_el.text.strip() if seller_name_el else None
                contact_el = soup.select_one(".i-detail-des-n")
                contact = contact_el.text.strip() if contact_el else ""
                await Seller.objects.aupdate_or_create(
                    id=seller_id,
                    defaults={"name": seller_name or "", "contact_number": contact},
                )

            price_el = soup.select_one(".details-page_product-info .price")
            price = (
                float(re.sub(r"[^\d.]+", "", price_el.text.strip()))
                if price_el else None
            )

            desc_el = soup.select_one(".iw-description-div")
            description = desc_el.get_text().strip() if desc_el else ""

            images = [
                img["src"] for img in soup.select("#fullscreen-viewer img")
            ]

            last_updated_el = soup.find(
                "div", string=re.compile("Last Updated : ")
            )
            last_updated = None
            if last_updated_el:
                m = re.search(
                    r"Last Updated : (\d{1,2}-[A-Za-z]{3}-\d{4})",
                    last_updated_el.text,
                )
                if m:
                    last_updated = datetime.strptime(
                        m.group(1), "%d-%b-%Y"
                    ).date()

            product_info = []
            location = None
            for row in soup.select(".item-info-table > table > tbody > tr"):
                k = row.select_one("td:nth-child(1)")
                v = row.select_one("td:nth-child(2)")
                if k and v:
                    key = k.text.strip()
                    val = v.text.strip()
                    if key == "Location":
                        location = val
                    else:
                        product_info.append({key: val})

            breadcrumbs = soup.select(
                'div a.breadcrumb.dark[href*="b"]'
            )
            cat_ids = []
            for el in breadcrumbs:
                m = re.search(r"b(\d+)", el["href"])
                if m:
                    cat_ids.append(int(m.group(1)))

            await Product.objects.filter(id=product_id).aupdate(
                seller_id=seller_id,
                price=price,
                product_location=location or "",
                description=description,
                last_updated=last_updated,
                status="SCRAPED",
            )

            if cat_ids:
                existing = set(
                    row[0] async for row in
                    ProductCategory.objects.filter(product_id=product_id)
                    .values_list("category_id")
                )
                new_cats = set(cat_ids) - existing
                for cid in new_cats:
                    if await Category.objects.filter(id=cid).aexists():
                        await ProductCategory.objects.aupdate_or_create(
                            product_id=product_id, category_id=cid
                        )

            await ProductImage.objects.filter(product_id=product_id).adelete()
            for img_url in images:
                await ProductImage.objects.acreate(
                    product_id=product_id, image_url=img_url
                )

            await ProductInfo.objects.filter(product_id=product_id).adelete()
            for info_item in product_info:
                for k, v in info_item.items():
                    await ProductInfo.objects.acreate(
                        product_id=product_id, info_key=k, info_value=v
                    )

            logger.info("Scraped: %s", name)

        except Exception:
            logger.exception("Error processing %s", url)
            await Product.objects.filter(id=product_id).aupdate(
                status="ERROR", error_message="Exception during scrape"
            )


async def sync_product_details(client: httpx.AsyncClient):
    sem = asyncio.Semaphore(DETAIL_SEMAPHORE_LIMIT)
    products = [
        p async for p in
        Product.objects.filter(status="NOT_SCRAPED").values("id", "listing_id", "name", "url")
    ]
    total = len(products)
    logger.info("Found %d products needing detail scrape.", total)
    for i in range(0, total, BATCH_SIZE):
        batch = products[i : i + BATCH_SIZE]
        tasks = [
            fetch_product_detail(
                client, sem, p["id"], p["listing_id"], p["name"], p["url"]
            )
            for p in batch
        ]
        await asyncio.gather(*tasks)
        logger.info(
            "Detail batch %d/%d done.",
            i // BATCH_SIZE + 1, (total + BATCH_SIZE - 1) // BATCH_SIZE,
        )
        await asyncio.sleep(1)


async def update_stale_products(client: httpx.AsyncClient):
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    stale = [
        p async for p in
        Product.objects.filter(
            status="SCRAPED", updated_at__lt=cutoff
        ).values("id", "listing_id", "name", "url", "price", "description")
    ]
    total = len(stale)
    logger.info("Found %d stale products to update.", total)
    sem = asyncio.Semaphore(DETAIL_SEMAPHORE_LIMIT)
    for i in range(0, total, BATCH_SIZE):
        batch = stale[i : i + BATCH_SIZE]
        tasks = []
        for p in batch:
            tasks.append(
                fetch_product_detail(
                    client, sem, p["id"], p["listing_id"], p["name"], p["url"]
                )
            )
        await asyncio.gather(*tasks)
        logger.info(
            "Stale update batch %d/%d done.",
            i // BATCH_SIZE + 1, (total + BATCH_SIZE - 1) // BATCH_SIZE,
        )
        await asyncio.sleep(1)
    if total > 0:
        logger.info("Stale product update done. %d products refreshed.", total)


async def sync_all():
    logger.info("=== Starting ibay sync ===")
    async with httpx.AsyncClient() as client:
        logger.info("--- Syncing categories ---")
        await sync_categories(client)
        logger.info("--- Syncing product links ---")
        await sync_product_links(client)
        logger.info("--- Syncing product details ---")
        await sync_product_details(client)
        logger.info("--- Updating stale products ---")
        await update_stale_products(client)
    logger.info("=== ibay sync complete ===")


async def run_sync_loop():
    logger.info("Starting continuous ibay sync...")
    while True:
        try:
            await sync_all()
        except Exception:
            logger.exception("ibay sync cycle failed")
        logger.info("Sleeping for %ds before next ibay sync...", SYNC_INTERVAL_SECONDS)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start_sync_thread():
    def _run():
        asyncio.run(run_sync_loop())

    thread = threading.Thread(target=_run, daemon=True, name="ibay-sync")
    thread.start()
