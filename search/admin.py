from django.contrib import admin
from search.models import Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "label_dv", "is_active")
    list_editable = ("is_active",)


from search.models import QueryAlias


@admin.register(QueryAlias)
class QueryAliasAdmin(admin.ModelAdmin):
    list_display = ("term", "expands_to", "is_active")
    search_fields = ("term",)
    list_editable = ("is_active",)
