from django.apps import AppConfig


class SearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "search"

    def ready(self):
        from search.adapters import base
        from search.adapters.ibay import IbayAdapter

        if "ibay" not in base._REGISTRY:
            base.register(IbayAdapter())
