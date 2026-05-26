import asyncio

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand

from gazette.sync_service import sync_all as sync_gazette
from ibay.sync_service import sync_all as sync_ibay


class Command(BaseCommand):
    help = "Run a single sync cycle for gazette and ibay in parallel"

    def handle(self, *args, **options):
        async def _run():
            await asyncio.gather(sync_gazette(), sync_ibay())

        async_to_sync(_run)()
