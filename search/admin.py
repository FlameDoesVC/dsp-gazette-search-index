from django.contrib import admin
from search.models import Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "label_dv", "is_active")
    list_editable = ("is_active",)
