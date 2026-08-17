from django.db import models


class Source(models.Model):
    """Provenance registry. Display metadata lives here so adding a source is
    an admin row plus an icon file; the adapter stays in code. Spec 4.3.3."""

    key = models.CharField(max_length=32, unique=True)
    label_en = models.CharField(max_length=64)
    label_dv = models.CharField(max_length=64, blank=True)
    site_url = models.URLField()
    icon = models.CharField(max_length=128, blank=True)
    icon_fallback_text = models.CharField(max_length=4, blank=True)
    accent = models.CharField(max_length=9, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.label_en or self.key
