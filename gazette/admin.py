from django.contrib import admin

from gazette.models import Iulaan


class IulaanAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'translated_title', 'office_name', 'iulaan_type')
    search_fields = ('title', 'translated_title', 'office_name', 'body', 'translated_body')


admin.site.register(Iulaan, IulaanAdmin)
