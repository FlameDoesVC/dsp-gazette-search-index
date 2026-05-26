import os
import sys

from django.apps import AppConfig


class GazetteConfig(AppConfig):
    name = 'gazette'

    def ready(self):
        if 'runserver' in sys.argv and not os.environ.get('RUN_MAIN'):
            return
        from gazette.sync_service import start_sync_thread
        start_sync_thread()
