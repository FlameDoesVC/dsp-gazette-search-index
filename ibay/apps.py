import os
import sys

from django.apps import AppConfig


class IbayConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ibay'

    def ready(self):
        if 'runserver' in sys.argv and not os.environ.get('RUN_MAIN'):
            return
        from ibay.sync_service import start_sync_thread
        start_sync_thread()
