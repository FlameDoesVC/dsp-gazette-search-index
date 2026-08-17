from django.db import models
from django.contrib.postgres.search import SearchVectorField


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


class SearchDocument(models.Model):
    """One row per searchable entity, fully denormalized. Spec 4.1.

    The table is LIST-partitioned by `source` and created by raw SQL in
    migration 0002; Django only tracks its state. `doc_type` is deliberately
    mutable and is NOT part of any unique constraint -- reclassification
    (spec 3.2) must be an in-place UPDATE.
    """

    # identity
    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)
    doc_type = models.CharField(max_length=32)
    url = models.URLField(max_length=512)

    # display, language-parallel
    title_en = models.CharField(max_length=512, blank=True)
    title_dv = models.CharField(max_length=512, blank=True)
    title_latin = models.CharField(max_length=512, blank=True)
    summary_en = models.TextField(blank=True)
    summary_dv = models.TextField(blank=True)

    # search vectors. Body text is never stored here -- spec 12.1.
    vector_en = SearchVectorField(null=True)
    vector_dv = SearchVectorField(null=True)
    vector_latin = SearchVectorField(null=True)

    # universal facets
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, default="MVR")
    location = models.CharField(max_length=128, blank=True)
    island = models.CharField(max_length=128, blank=True)
    atoll = models.CharField(max_length=64, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # type-specific data
    attrs = models.JSONField(default=dict, blank=True)
    card = models.JSONField(default=dict, blank=True)
    card_version = models.IntegerField(default=1)
    thumbnails = models.JSONField(default=list, blank=True)

    # ranking and bookkeeping
    quality = models.FloatField(default=0.0)
    content_hash = models.CharField(max_length=64, blank=True)
    indexed_at = models.DateTimeField(auto_now=True)
    stale_marked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "search_searchdocument"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_key"], name="uniq_source_source_key"
            )
        ]

    def __str__(self):
        return f"{self.source}:{self.source_key}"
