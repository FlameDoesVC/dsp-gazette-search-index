import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import httpx
from asgiref.sync import sync_to_async
from bs4 import BeautifulSoup

from ibay.models import Category, Product, ProductCategory, ProductImage, ProductInfo, Seller

_add_root = sync_to_async(lambda **kw: Category.add_root(**kw))
_add_child = sync_to_async(lambda parent, **kw: parent.add_child(**kw))

logger = logging.getLogger(__name__)

BASE_URL = "https://ibay.com.mv"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/58.0.3029.110 Safari/537.3"
)
STALE_DAYS = 1
DETAIL_SEMAPHORE_LIMIT = 12
LINK_SEMAPHORE_LIMIT = 10
BATCH_SIZE = 20
LINK_BATCH_CONCURRENCY = 3
MAX_PAGES_PER_CATEGORY = 5
SYNC_INTERVAL_SECONDS = 600
REQUEST_DELAY = 0.5


async def _upsert_category(cid, name, parent_id=None):
    existing = await Category.objects.filter(id=cid).afirst()
    if existing:
        if existing.name != name:
            existing.name = name
            await existing.asave(update_fields=["name"])
        return
    if parent_id is None:
        await _add_root(id=cid, name=name)
    else:
        parent = await Category.objects.filter(id=parent_id).afirst()
        if parent:
            await _add_child(parent, id=cid, name=name)
        else:
            await _add_root(id=cid, name=name)


async def sync_categories(client: httpx.AsyncClient):
    visited = set()

    async def scrape_category(category_id, parent_id=None, level=0):
        nonlocal visited
        if category_id in visited:
            return
        visited.add(category_id)
        url = f"{BASE_URL}/index.php?page=cat_ajax&id={category_id}"
        try:
            response = await client.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=15
            )
        except Exception:
            logger.warning("Failed to fetch category %d at level %d, skipping subtree", category_id, level)
            return
        if response.status_code != 200:
            logger.warning("Status %d for category %d, skipping subtree", response.status_code, category_id)
            return
        try:
            cats = response.json()
        except Exception:
            logger.warning("Non-JSON response for category %d, skipping subtree", category_id)
            return
        if not cats:
            return
        tasks = []
        for item in cats:
            cid, cname = list(item.items())[0]
            cid = int(cid)
            await _upsert_category(cid, cname, parent_id)
            logger.info("%s%s (ID: %d)", "  " * level, cname, cid)
            task = asyncio.create_task(scrape_category(cid, cid, level + 1))
            tasks.append(task)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                    logger.warning("Subcategory scrape raised: %s", r)
        await asyncio.sleep(REQUEST_DELAY)

    await scrape_category(0)


async def sync_product_links(client: httpx.AsyncClient):
    sem = asyncio.Semaphore(LINK_SEMAPHORE_LIMIT)
    parent_cats = [
        c async for c in Category.objects.filter(depth=1)
    ]
    logger.info("Found %d parent categories for product link sync.", len(parent_cats))

    async def scrape_category_links(category):
        pages_with_data = 0
        while pages_with_data < MAX_PAGES_PER_CATEGORY:
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
    for i in range(0, len(tasks), LINK_BATCH_CONCURRENCY):
        await asyncio.gather(*tasks[i : i + LINK_BATCH_CONCURRENCY])


async def ensure_category_hierarchy(client, cat_ids_with_names):
    parent_id = None
    for entry in cat_ids_with_names:
        cid = entry["id"]
        existing = await Category.objects.filter(id=cid).afirst()
        if existing:
            if existing.name != entry["name"]:
                existing.name = entry["name"]
                await existing.asave(update_fields=["name"])
        elif parent_id is None:
            await _add_root(id=cid, name=entry["name"])
            logger.info("Created root category %d: %s", cid, entry["name"])
        else:
            parent = await Category.objects.filter(id=parent_id).afirst()
            if parent:
                await _add_child(parent, id=cid, name=entry["name"])
                logger.info("Created category %d: %s (parent=%d)", cid, entry["name"], parent_id)
            else:
                await _add_root(id=cid, name=entry["name"])
                logger.info("Created root category %d: %s (parent %d missing)", cid, entry["name"], parent_id)
        parent_id = cid


async def fetch_seller_page(client, seller_id: int):
    """Fetch seller profile page to get additional info (phone may be absent)."""
    try:
        url = f"{BASE_URL}/index.php?page=profile&id={seller_id}"
        response = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if response.status_code != 200:
            return {}
        soup = BeautifulSoup(response.text, "lxml")
        info = {}

        # Location is shown on the profile page as a <b> tag near "Location"
        location_el = soup.find("b", string=re.compile(r"Male|Location"))
        if location_el:
            info["location"] = location_el.text.strip()

        # Member since / last login
        for b in soup.find_all("b"):
            txt = b.text.strip()
            if re.match(r"\d{2}-[A-Za-z]{3}-\d{4}", txt):
                if "member_since" not in info:
                    info["member_since"] = txt
                else:
                    info["last_login"] = txt

        return info
    except Exception:
        logger.exception("Error fetching seller page for %d", seller_id)
        return {}


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

            if soup.find("font", class_="pagetitle", string="Listing disabled") or \
               soup.find("p", class_="pagetitle", string="Listing not found"):
                await Product.objects.filter(id=product_id).aupdate(
                    status="ERROR", error_message="Listing disabled or not found"
                )
                logger.warning("Listing %d not available.", listing_id)
                return

            # ---- Seller ----
            seller_url_el = soup.select_one(".iw-user-name")
            seller_id = None
            if seller_url_el and seller_url_el.get("href"):
                seller_id = int(re.search(r"id=(\d+)", seller_url_el["href"]).group(1))
                seller_name_el = seller_url_el.select_one("b")
                seller_name = seller_name_el.text.strip() if seller_name_el else ""
                contact_el = soup.select_one(".i-detail-des-n")
                contact = contact_el.text.strip() if contact_el else ""

                profile_info = await fetch_seller_page(client, seller_id)
                _, created = await Seller.objects.aupdate_or_create(
                    id=seller_id,
                    defaults={
                        "name": seller_name,
                        "contact_number": contact,
                        "location": profile_info.get("location", ""),
                    },
                )
                if created:
                    logger.info(
                        "Created seller %d: %s (phone: %s)",
                        seller_id, seller_name, contact,
                    )

            # ---- Price ----
            price_el = soup.select_one(".details-page_product-info .price")
            price = (
                float(re.sub(r"[^\d.]+", "", price_el.text.strip()))
                if price_el else None
            )

            # ---- Description ----
            desc_el = soup.select_one(".iw-description-div")
            description = desc_el.get_text().strip() if desc_el else ""

            # ---- Images ----
            images = [
                img["src"] for img in soup.select("#fullscreen-viewer img")
            ]

            # ---- Last updated ----
            last_updated_el = soup.find("div", string=re.compile("Last Updated"))
            last_updated = None
            if last_updated_el:
                m = re.search(
                    r"(\d{1,2}-[A-Za-z]{3}-\d{4})", last_updated_el.text
                )
                if m:
                    last_updated = datetime.strptime(
                        m.group(1), "%d-%b-%Y"
                    ).date()

            # ---- Item info table (Location, Condition, etc.) ----
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

            # ---- Breadcrumb categories ----
            raw_cats = []
            seen_ids = set()
            breadcrumb_els = soup.select("div a.breadcrumb.dark")
            logger.debug("Found %d breadcrumb elements on %s", len(breadcrumb_els), url)
            for el in breadcrumb_els:
                m = re.search(r"b(\d+)", el["href"])
                if m:
                    cid = int(m.group(1))
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        raw_cats.append({"id": cid, "name": el.text.strip()})

            logger.debug("Parsed %d unique categories from breadcrumbs: %s", len(raw_cats), [c["id"] for c in raw_cats])

            if raw_cats:
                await ensure_category_hierarchy(client, raw_cats)

            # ---- Update product ----
            await Product.objects.filter(id=product_id).aupdate(
                seller_id=seller_id,
                price=price,
                product_location=location or "",
                description=description,
                last_updated=last_updated,
                status="SCRAPED",
            )

            # ---- Link categories ----
            for entry in raw_cats:
                await ProductCategory.objects.aupdate_or_create(
                    product_id=product_id, category_id=entry["id"]
                )

            # ---- Product images ----
            await ProductImage.objects.filter(product_id=product_id).adelete()
            for img_url in images:
                await ProductImage.objects.acreate(
                    product_id=product_id, image_url=img_url
                )

            # ---- Product info key-values ----
            await ProductInfo.objects.filter(product_id=product_id).adelete()
            for info_item in product_info:
                for k, v in info_item.items():
                    await ProductInfo.objects.acreate(
                        product_id=product_id, info_key=k, info_value=v
                    )

            logger.info("Scraped: %s (seller=%d, cats=%s)", name, seller_id or 0, [c["id"] for c in raw_cats])

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
    if not total:
        return
    logger.info("Found %d stale products to update.", total)
    sem = asyncio.Semaphore(DETAIL_SEMAPHORE_LIMIT)
    for i in range(0, total, BATCH_SIZE):
        batch = stale[i : i + BATCH_SIZE]
        tasks = [
            fetch_product_detail(
                client, sem, p["id"], p["listing_id"], p["name"], p["url"]
            )
            for p in batch
        ]
        await asyncio.gather(*tasks)
        logger.info(
            "Stale update batch %d/%d done.",
            i // BATCH_SIZE + 1, (total + BATCH_SIZE - 1) // BATCH_SIZE,
        )
        await asyncio.sleep(1)
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
