from django.db import models


class TranslationCache(models.Model):
    source_hash = models.CharField(max_length=64, unique=True)
    translated_text = models.TextField()

    class Meta:
        verbose_name_plural = "translation cache entries"


class Office(models.Model):
    name = models.CharField(max_length=255, unique=True)
    translated_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.translated_name or self.name


class IulaanType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    translated_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.translated_name or self.name


class Iulaan(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    title = models.CharField(max_length=255)
    translated_title = models.CharField(max_length=255, blank=True)
    office = models.ForeignKey(Office, null=True, blank=True, on_delete=models.PROTECT)
    iulaan_type = models.ForeignKey(IulaanType, null=True, blank=True, on_delete=models.PROTECT)
    additional_info = models.JSONField()
    attachments = models.JSONField()
    body = models.TextField()
    translated_body = models.TextField(blank=True)

    def __str__(self):
        return self.title

    @property
    def url(self):
        return f"https://gazette.gov.mv/iulaan/{self.id}"
