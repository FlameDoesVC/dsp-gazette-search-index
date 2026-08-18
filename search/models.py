from django.db import models
from django.contrib.postgres.indexes import GinIndex
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


class QueryAlias(models.Model):
    """Curated synonym expansion. Spec 6.5.

    Seeded by hand and grown from the zero-result query list that P5's logging
    produces -- that list is the highest-signal input for this table.
    """

    term = models.CharField(max_length=128, unique=True)
    expands_to = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=256, blank=True)

    class Meta:
        verbose_name_plural = "query aliases"
        ordering = ["term"]

    def __str__(self):
        return self.term


class SuggestTerm(models.Model):
    """Autocomplete vocabulary. Spec 9.

    Derived and disposable, like SearchDocument: rebuilt from titles by
    `rebuild_suggest_terms`. Trigram-searched here rather than over the
    documents themselves because a substring match across 71,445 titles is a
    sequential scan on every keystroke.
    """

    term = models.CharField(max_length=64, unique=True)
    frequency = models.IntegerField(default=0)
    script = models.CharField(max_length=8)     # latin | thaana
    doc_type = models.CharField(max_length=32, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["-frequency"], name="suggest_freq"),
            GinIndex(fields=["term"], name="suggest_term_trgm",
                     opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return self.term


class QueryLog(models.Model):
    """Append-only search history. Spec 16.3.

    Month-partitioned with BRIN on created_at: this is the fastest-growing
    table in the system and it must not share vacuum behaviour with
    SearchDocument. Created by raw SQL, like SearchDocument -- Django tracks
    only the state.

    No user identity. session_hash is salted with a daily-rotating salt, which
    supports same-session analysis without building a durable per-person
    search history. Query text is the most sensitive data this system holds
    and a Dhivehi search log is a small-population, easily de-anonymised set.
    """

    q_raw = models.CharField(max_length=256)
    q_normalized = models.CharField(max_length=256, blank=True)
    detected_lang = models.CharField(max_length=8, blank=True)
    response_lang = models.CharField(max_length=8, blank=True)
    doc_type = models.CharField(max_length=32, blank=True)
    filters = models.JSONField(default=list, blank=True)
    result_count = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    session_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "search_querylog"
        managed = True


class ClickLog(models.Model):
    query = models.ForeignKey(QueryLog, on_delete=models.CASCADE,
                              related_name="clicks", db_constraint=False)
    # SearchDocument is partitioned, so a real FK constraint is not available.
    document = models.ForeignKey("search.SearchDocument", on_delete=models.DO_NOTHING,
                                 db_constraint=False, related_name="clicks")
    # Rank at click time. Impossible to reconstruct later; without it there is
    # no MRR, no nDCG and no usable ranking feature -- just a list of documents
    # someone once opened. Spec 16.3.
    position = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "search_clicklog"
        managed = True
