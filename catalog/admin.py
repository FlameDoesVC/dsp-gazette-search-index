from django.contrib import admin

from catalog.models import Brand, Entity, EntityField, EntityLink, FieldProposal


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "aliases", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name", "aliases")


class EntityFieldInline(admin.TabularInline):
    model = EntityField
    extra = 0
    fields = ("key_raw", "value_num", "value_text", "unit", "provenance",
              "support_count", "is_winner")
    readonly_fields = ("provenance", "support_count")


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ("__str__", "kind", "brand", "service_type", "category",
                    "listing_count", "profile_status", "identity_confidence")
    list_filter = ("kind", "profile_status")
    search_fields = ("key", "title_en", "brand", "model_name", "provider_key")
    ordering = ("-listing_count",)
    inlines = [EntityFieldInline]


@admin.register(EntityLink)
class EntityLinkAdmin(admin.ModelAdmin):
    list_display = ("source", "source_key", "entity", "method", "confidence")
    list_filter = ("source", "method")
    search_fields = ("source_key",)


@admin.register(FieldProposal)
class FieldProposalAdmin(admin.ModelAdmin):
    """Conflicted first: those are the ones needing a human."""

    list_display = ("entity", "key_raw", "value_text", "value_num", "status",
                    "created_at")
    list_filter = ("status", "key_raw")
    list_editable = ("status",)
    ordering = ("status", "-created_at")
