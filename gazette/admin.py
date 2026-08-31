from django.contrib import admin

from gazette.models import Attachment, Iulaan, IulaanType, Office


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0
    fields = ('label_raw', 'role', 'method', 'status', 'transcribed', 'page_count')
    readonly_fields = fields


class IulaanAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'translated_title', 'office_name', 'iulaan_type_name')
    search_fields = ('title', 'translated_title', 'body', 'translated_body',
                     'office__name', 'office__translated_name',
                     'iulaan_type__name', 'iulaan_type__translated_name')
    inlines = [AttachmentInline]

    def office_name(self, obj):
        return obj.office.translated_name or obj.office.name
    office_name.short_description = "Office"

    def iulaan_type_name(self, obj):
        return obj.iulaan_type.translated_name or obj.iulaan_type.name
    iulaan_type_name.short_description = "Type"


class OfficeAdmin(admin.ModelAdmin):
    list_display = ('name', 'translated_name')
    search_fields = ('name', 'translated_name')


class IulaanTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'translated_name')
    search_fields = ('name', 'translated_name')


class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'iulaan', 'label_raw', 'role', 'method', 'status',
                     'transcribed', 'page_count')
    list_filter = ('status', 'method', 'transcribed')
    search_fields = ('label_raw', 'iulaan__title', 'iulaan__translated_title')


admin.site.register(Iulaan, IulaanAdmin)
admin.site.register(Office, OfficeAdmin)
admin.site.register(IulaanType, IulaanTypeAdmin)
admin.site.register(Attachment, AttachmentAdmin)
