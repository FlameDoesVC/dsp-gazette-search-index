from django.db import migrations

SOURCES = [
    dict(
        key="ibay",
        label_en="iBay",
        label_dv="އައިބޭ",
        site_url="https://ibay.com.mv",
        icon="/static/sources/ibay.svg",
        icon_fallback_text="iB",
        accent="#1f6feb",
    ),
    dict(
        key="gazette",
        label_en="Gazette",
        label_dv="ގެޒެޓް",
        site_url="https://gazette.gov.mv",
        icon="/static/sources/gazette.svg",
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
    dependencies = [("search", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
