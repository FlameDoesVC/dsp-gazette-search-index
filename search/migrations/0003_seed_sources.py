from django.db import migrations

SOURCES = [
    dict(
        key="gazette",
        label_en="Gazette",
        label_dv="ގެޒެޓް",
        site_url="https://gazette.gov.mv",
        icon="/sources/gazette.png",
        icon_fallback_text="ގ",
        accent="#0f766e",
    ),
]


def seed(apps, schema_editor):
    Source = apps.get_model("search", "Source")
    for row in SOURCES:
        Source.objects.update_or_create(key=row["key"], defaults=row)


def unseed(apps, schema_editor):
    Source = apps.get_model("search", "Source")
    Source.objects.filter(key__in=[r["key"] for r in SOURCES]).delete()


class Migration(migrations.Migration):
    dependencies = [("search", "0002_searchdocument")]
    operations = [migrations.RunPython(seed, unseed)]
