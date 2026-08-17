from django.contrib import admin

from enrich.models import EnrichedRecord


@admin.register(EnrichedRecord)
class EnrichedRecordAdmin(admin.ModelAdmin):
    list_display = (
        "source", "source_key", "doc_type", "status",
        "doc_type_confidence", "model_name", "prompt_version", "updated_at",
    )
    list_filter = ("source", "doc_type", "status", "model_name", "prompt_version")
    search_fields = ("source_key", "canonical_title_en", "canonical_title_dv")
    readonly_fields = ("attrs", "validation", "keywords", "created_at", "updated_at")
    # needs_review first: those are the records where a scraped field and the
    # model disagreed, which is the queue a human is actually here to clear.
    ordering = ("status", "-updated_at")
