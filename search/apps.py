from django.apps import AppConfig


class SearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "search"

    def ready(self):
        from search.adapters import base
        from search.adapters.gazette import GazetteAdapter
        from search.adapters.ibay import IbayAdapter

        for adapter in (IbayAdapter(), GazetteAdapter()):
            if adapter.key not in base._REGISTRY:
                base.register(adapter)
