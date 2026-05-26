import asyncio
import logging
import threading

import httpx
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from django.conf import settings

from gazette.models import Iulaan
from gazette.scraper import (
    fetch_and_parse_announcement,
    fetch_index_links,
    get_max_page_number,
)

logger = logging.getLogger(__name__)

MAX_INDEX_PAGES = 2 if settings.DEBUG else None
MAX_CONCURRENT_REQUESTS = 3
REQUEST_DELAY = 0.5
SYNC_INTERVAL_SECONDS = 300
TRANSLATE_CHUNK_SIZE = 4500


def _strip_html(html_text):
    return BeautifulSoup(html_text, "html.parser").get_text()


async def _translate_to_en(text):
    if not text or not text.strip():
        return ""
    try:
        result = await asyncio.to_thread(
            GoogleTranslator(source="dv", target="en").translate, text
        )
        return result or ""
    except Exception:
        logger.warning("Translation failed for: %s...", text[:80])
        return ""


async def _translate_body(html_body):
    text = _strip_html(html_body)
    if not text.strip():
        return ""
    results = []
    for i in range(0, len(text), TRANSLATE_CHUNK_SIZE):
        chunk = text[i : i + TRANSLATE_CHUNK_SIZE]
        translated = await _translate_to_en(chunk)
        if translated:
            results.append(translated)
    return " ".join(results)


async def sync_all():
    total_new = 0
    total_fetched = 0
    total_failed = 0

    async with httpx.AsyncClient() as client:
        max_page = await get_max_page_number(client)
        if MAX_INDEX_PAGES:
            max_page = min(max_page, MAX_INDEX_PAGES)
        logger.info("Found %d pages to check for announcements.", max_page)

        page_queue = asyncio.Queue()
        iulaan_id_queue = asyncio.Queue()

        type_new_count = 0
        type_skipped_count = 0
        type_fetched_count = 0
        type_failed_count = 0

        async def page_worker():
            nonlocal type_new_count, type_skipped_count
            while True:
                page_num = await page_queue.get()
                try:
                    iulaan_ids = await fetch_index_links(client, page_number=page_num)
                    for iulaan_id in iulaan_ids:
                        if not await Iulaan.objects.filter(id=iulaan_id).aexists():
                            await iulaan_id_queue.put(iulaan_id)
                            type_new_count += 1
                        else:
                            type_skipped_count += 1
                except httpx.HTTPStatusError as e:
                    logger.error("HTTP error fetching page %d: %s", page_num, e)
                finally:
                    page_queue.task_done()
                await asyncio.sleep(REQUEST_DELAY)

        async def iulaan_worker():
            nonlocal type_fetched_count, type_failed_count
            while True:
                iulaan_id = await iulaan_id_queue.get()
                try:
                    data = await fetch_and_parse_announcement(client, iulaan_id)

                    translated_title = await _translate_to_en(data.title)
                    translated_office = await _translate_to_en(data.office_name)
                    translated_body = await _translate_body(data.body)

                    attachments = data.attachments or {}
                    translated_attachments = {}
                    for key, url in attachments.items():
                        trans_key = await _translate_to_en(key)
                        translated_attachments[trans_key or key] = url

                    await Iulaan.objects.aupdate_or_create(
                        id=data.id,
                        defaults={
                            "title": data.title,
                            "translated_title": translated_title,
                            "office_name": data.office_name,
                            "translated_office_name": translated_office,
                            "iulaan_type": data.iulaan_type,
                            "additional_info": data.additional_info,
                            "attachments": translated_attachments,
                            "body": data.body,
                            "translated_body": translated_body,
                        },
                    )
                    type_fetched_count += 1
                    logger.info("Scraped + translated: %s", data.title[:60])
                except httpx.HTTPStatusError as e:
                    logger.error("HTTP error fetching iulaan %s: %s", iulaan_id, e)
                    type_failed_count += 1
                except Exception:
                    logger.exception("An error occurred processing %s", iulaan_id)
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

        await page_queue.join()
        await iulaan_id_queue.join()

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
