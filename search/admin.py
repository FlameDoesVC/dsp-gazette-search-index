from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone

from search.models import (
    DocumentReport, DocumentSpec, QueryAlias, SearchDocument, Source, SpecKey,
)
from search.specs.project import candidate_keys


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


@admin.register(SpecKey)
class SpecKeyAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "datatype", "widget", "unit",
                    "is_facetable", "priority")
    list_filter = ("is_facetable", "datatype", "widget")
    list_editable = ("is_facetable", "priority")
    search_fields = ("key", "label_en", "label_dv")
    actions = ("promote", "demote")

    def get_urls(self):
        return [
            path("candidates/", self.admin_site.admin_view(self.candidates_view),
                 name="search_speckey_candidates"),
        ] + super().get_urls()

    def candidates_view(self, request):
        """The promotion queue. Spec 4.4.

        Extraction is open, so unpromoted key_raw values accumulate. Ranking
        them by document count turns an unbounded attribute space into a short
        list of one-click decisions.
        """
        if request.method == "POST" and request.POST.get("promote"):
            key_raw = request.POST["promote"]
            key = _promote(key_raw)
            self.message_user(
                request,
                f"Promoted {key.key} as {key.datatype}/{key.widget}. "
                f"Run `sync_specs` to relink historical rows.",
                messages.SUCCESS,
            )
            return HttpResponseRedirect(
                reverse("admin:search_speckey_candidates")
            )

        return render(request, "admin/search/speckey_candidates.html", {
            **self.admin_site.each_context(request),
            "title": "Spec key promotion queue",
            "rows": candidate_keys(limit=100),
        })

    @admin.action(description="Promote to a facet")
    def promote(self, request, queryset):
        queryset.update(is_facetable=True)

    @admin.action(description="Demote (stop faceting, keep the data)")
    def demote(self, request, queryset):
        # Never deletes DocumentSpec rows: the detail-page spec table still
        # shows them, and re-promoting must not require a re-sync.
        queryset.update(is_facetable=False)


def _promote(key_raw: str) -> SpecKey:
    rows = DocumentSpec.objects.filter(key_raw=key_raw, key__isnull=True)
    numeric = rows.filter(value_num__isnull=False).exists()
    unit = ""
    if numeric:
        first = rows.exclude(unit="").first()
        unit = first.unit if first else ""

    key, _ = SpecKey.objects.get_or_create(
        key=key_raw,
        defaults={
            "label_en": key_raw.replace("_", " ").title(),
            "datatype": "numeric" if numeric else "enum",
            "widget": "range" if numeric else "checkbox",
            "unit": unit,
            "is_facetable": True,
        },
    )
    rows.update(key=key)
    return key
