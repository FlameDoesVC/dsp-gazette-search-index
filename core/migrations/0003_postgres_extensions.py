from django.contrib.postgres.operations import TrigramExtension, UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0002_copy_translation_cache")]
    operations = [TrigramExtension(), UnaccentExtension()]
