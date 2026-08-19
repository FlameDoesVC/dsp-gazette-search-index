import asyncio
import logging
import os
import threading

import httpx
from bs4 import BeautifulSoup
from django.conf import settings

from gazette.models import Iulaan, Office, IulaanType
from gazette.scraper import (
    fetch_and_parse_announcement,
    fetch_index_links,
    get_max_page_number,
)

logger = logging.getLogger(__name__)

# Env-driven so a test sync can be scoped without editing code.
#
# Read the DEBUG default with suspicion: DJANGO_DEBUG defaults to 0 in this repo
# and tests_settings asserts that it does, so on any machine that has not opted
# in this resolves to 3500, not 2. A cap that only applies when a flag nobody
# sets is set is not a cap. The real guard against a runaway crawl is
# STOP_AFTER_SEEN_PAGES below, which needs no configuration to work.
MAX_INDEX_PAGES = int(
    os.getenv("GAZETTE_MAX_INDEX_PAGES", "2" if settings.DEBUG else "3500")
)
# Stop once this many CONSECUTIVE index pages contain nothing we have not already
# stored. The gazette index is newest-first, so a run of fully-seen pages means
# the crawl has reached material from a previous sync and everything beyond it is
# older still. 0 disables the check, and `sync_gazette --full` overrides it for a
# deliberate backfill.
STOP_AFTER_SEEN_PAGES = int(os.getenv("GAZETTE_STOP_AFTER_SEEN_PAGES", "3"))
MAX_CONCURRENT_REQUESTS = 3
REQUEST_DELAY = 0.5
SYNC_INTERVAL_SECONDS = 300


def seen_streak(page_results: dict[int, bool]) -> int:
    """Longest run of consecutive fully-seen pages in the completed prefix.

    Pages are handed to several workers at once, so they finish out of order and
    a naive "last N completions" counter would fire on pages 4, 9 and 12. Only
    the contiguous prefix starting at page 1 can be reasoned about, so this walks
    that and ignores anything with a gap before it.

    `page_results` holds a page only after it completed successfully: a page that
    raised is absent rather than False, so an HTTP error cannot masquerade as
    "nothing new here" and end the crawl early.
    """
    streak = best = 0
    page = 1
    while page in page_results:
        streak = streak + 1 if page_results[page] else 0
        best = max(best, streak)
        page += 1
    return best


def _strip_html(html_text):
    return BeautifulSoup(html_text, "html.parser").get_text()


async def sync_all(*, max_pages: int | None = None,
                   stop_after_seen: int | None = None):
    """One sync cycle.

    `max_pages` and `stop_after_seen` override the module defaults so the
    management command can expose them; None means "use the configured value".
    """
    if max_pages is None:
        max_pages = MAX_INDEX_PAGES
    if stop_after_seen is None:
        stop_after_seen = STOP_AFTER_SEEN_PAGES

    total_new = 0
    total_fetched = 0
    total_failed = 0

    async with httpx.AsyncClient() as client:
        max_page = await get_max_page_number(client)
        if max_pages:
            max_page = min(max_page, max_pages)
        logger.info("Found %d pages to check for announcements.", max_page)

        page_queue = asyncio.Queue()
        iulaan_id_queue = asyncio.Queue()

        type_new_count = 0
        type_skipped_count = 0
        type_fetched_count = 0
        type_failed_count = 0
        page_results: dict[int, bool] = {}      # page -> nothing new on it
        stop_early = asyncio.Event()

        async def page_worker():
            nonlocal type_new_count, type_skipped_count
            while True:
                page_num = await page_queue.get()
                if stop_early.is_set():
                    page_queue.task_done()
                    continue
                try:
                    iulaan_ids = await fetch_index_links(client, page_number=page_num)
                    logger.info("Page %d: found %d iulaan links.", page_num, len(iulaan_ids))
                    new_here = 0
                    for iulaan_id in iulaan_ids:
                        if not await Iulaan.objects.filter(id=iulaan_id).aexists():
                            await iulaan_id_queue.put(iulaan_id)
                            type_new_count += 1
                            new_here += 1
                        else:
                            type_skipped_count += 1
                    # Only a page that actually listed something can count as
                    # fully seen; an empty page means the fetch told us nothing.
                    page_results[page_num] = bool(iulaan_ids) and new_here == 0
                    if stop_after_seen and not stop_early.is_set():
                        streak = seen_streak(page_results)
                        if streak >= stop_after_seen:
                            logger.info(
                                "Stopping: %d consecutive pages held nothing new. "
                                "Pass --full to crawl every page anyway.", streak)
                            stop_early.set()
                    logger.info("Page %d done: %d new, %d skipped so far.",
                                page_num, type_new_count, type_skipped_count)
                except httpx.HTTPStatusError as e:
                    logger.error("HTTP error fetching page %d: %s", page_num, e)
                except Exception:
                    logger.exception("Unexpected error on page %d", page_num)
                finally:
                    page_queue.task_done()
                await asyncio.sleep(REQUEST_DELAY)

        async def iulaan_worker():
            nonlocal type_fetched_count, type_failed_count
            while True:
                iulaan_id = await iulaan_id_queue.get()
                logger.info("Processing iulaan %s...", iulaan_id)
                try:
                    data = await fetch_and_parse_announcement(client, iulaan_id)

                    office, _ = await Office.objects.aget_or_create(name=data.office_name)
                    itype, _ = await IulaanType.objects.aget_or_create(name=data.iulaan_type)

                    await Iulaan.objects.aupdate_or_create(
                        id=data.id,
                        defaults={
                            "title": data.title,
                            "office": office,
                            "iulaan_type": itype,
                            "additional_info": data.additional_info,
                            "attachments": data.attachments,
                            "body": data.body,
                        },
                    )
                    type_fetched_count += 1
                    logger.info("Scraped: %s", data.title[:60])
                except httpx.HTTPStatusError as e:
                    logger.error("HTTP error fetching iulaan %s: %s", iulaan_id, e)
                    type_failed_count += 1
                except Exception:
                    logger.exception("Unexpected error processing %s", iulaan_id)
                    type_failed_count += 1
                finally:
                    iulaan_id_queue.task_done()

        page_workers = [
            asyncio.create_task(page_worker())
            for _ in range(MAX_CONCURRENT_REQUESTS)
        ]
        iulaan_workers = [
            asyncio.create_task(iulaan_worker())
            for _ in range(MAX_CONCURRENT_REQUESTS)
        ]

        for page_num in range(1, max_page + 1):
            await page_queue.put(page_num)
        logger.info("Queued %d pages, waiting for page workers...", max_page)

        await page_queue.join()
        if stop_early.is_set():
            logger.info("Early stop after %d pages of %d queued.",
                        len(page_results), max_page)
        logger.info("All pages processed. %d new iulaan IDs queued.", type_new_count)
        logger.info("Waiting for iulaan workers to finish...")
        await iulaan_id_queue.join()
        logger.info("All iulaans processed. %d fetched, %d failed.",
                    type_fetched_count, type_failed_count)

        for worker in page_workers + iulaan_workers:
            worker.cancel()
        await asyncio.gather(
            *page_workers, *iulaan_workers, return_exceptions=True
        )

        total_new += type_new_count
        total_fetched += type_fetched_count
        total_failed += type_failed_count

        logger.info(
            "Found %d new announcements. Skipped %d existing ones.",
            type_new_count, type_skipped_count,
        )
        logger.info(
            "Successfully fetched %d new announcements. Failed: %d.",
            type_fetched_count, type_failed_count,
        )

    logger.info(
        "Sync summary: %d new, %d fetched, %d failed.",
        total_new, total_fetched, total_failed,
    )


async def run_sync_loop():
    logger.info("Starting continuous gazette sync...")
    while True:
        try:
            await sync_all()
        except BaseException:
            logger.exception("Sync cycle failed")
        logger.info("Sleeping for %ds before next sync...", SYNC_INTERVAL_SECONDS)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start_sync_thread():
    def _run():
        asyncio.run(run_sync_loop())

    thread = threading.Thread(target=_run, daemon=True, name="gazette-sync")
    thread.start()
