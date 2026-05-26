from django.contrib import admin

from gazette.models import Iulaan, IulaanType, Office


class IulaanAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'translated_title', 'office_name', 'iulaan_type_name')
    search_fields = ('title', 'translated_title', 'body', 'translated_body',
                     'office__name', 'office__translated_name',
                     'iulaan_type__name', 'iulaan_type__translated_name')

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


admin.site.register(Iulaan, IulaanAdmin)
admin.site.register(Office, OfficeAdmin)
admin.site.register(IulaanType, IulaanTypeAdmin)
