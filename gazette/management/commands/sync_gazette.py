import asyncio

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand

from gazette.sync_service import sync_all


class Command(BaseCommand):
    help = "Run a single gazette sync cycle"

    def handle(self, *args, **options):
        async_to_sync(sync_all)()
