from django.db import models


class TranslationCache(models.Model):
    source_hash = models.CharField(max_length=64, unique=True)
    translated_text = models.TextField()

    class Meta:
        verbose_name_plural = "translation cache entries"

    def __str__(self):
        return self.source_hash[:12]
