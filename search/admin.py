from django.contrib import admin
from django.db.models import Count
from django.utils import timezone

from search.models import DocumentReport, QueryAlias, SearchDocument, Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "label_dv", "is_active")
    list_editable = ("is_active",)


@admin.register(QueryAlias)
class QueryAliasAdmin(admin.ModelAdmin):
    list_display = ("term", "expands_to", "is_active")
    search_fields = ("term",)
    list_editable = ("is_active",)


@admin.register(DocumentReport)
class DocumentReportAdmin(admin.ModelAdmin):
    list_display = ("document_id", "reason", "status", "created_at")
    list_filter = ("status", "reason")
    actions = ("mark_stale_and_action", "reject")

    def get_queryset(self, request):
        # Sorted by report count so genuinely broken records surface first.
        qs = super().get_queryset(request)
        return qs.annotate(
            sibling_count=Count("document__reports")
        ).order_by("status", "-sibling_count", "-created_at")

    @admin.action(description="Mark document stale and action report (SPENDS MONEY)")
    def mark_stale_and_action(self, request, queryset):
        ids = list(queryset.values_list("document_id", flat=True))
        SearchDocument.objects.filter(id__in=ids).update(
            stale_marked_at=timezone.now()
        )
        queryset.update(status="actioned")
        self.message_user(
            request,
            f"{len(ids)} documents marked stale. Run extract_attachments --stale, "
            f"enrich_documents --stale, then reindex --stale.",
        )

    @admin.action(description="Reject")
    def reject(self, request, queryset):
        queryset.update(status="rejected")
