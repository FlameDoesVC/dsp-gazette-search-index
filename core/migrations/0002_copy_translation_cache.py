from django.db import migrations


def copy_forward(apps, schema_editor):
    """Copy rows from gazette.TranslationCache before the gazette migration
    drops that table. 676 rows at time of writing -- small enough to do in
    one pass."""
    Old = apps.get_model("gazette", "TranslationCache")
    New = apps.get_model("core", "TranslationCache")
    New.objects.bulk_create(
        [
            New(source_hash=row.source_hash, translated_text=row.translated_text)
            for row in Old.objects.all().iterator(chunk_size=500)
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


def copy_back(apps, schema_editor):
    Old = apps.get_model("gazette", "TranslationCache")
    New = apps.get_model("core", "TranslationCache")
    Old.objects.bulk_create(
        [
            Old(source_hash=row.source_hash, translated_text=row.translated_text)
            for row in New.objects.all().iterator(chunk_size=500)
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("gazette", "0005_translationcache"),
    ]
    operations = [migrations.RunPython(copy_forward, copy_back)]
