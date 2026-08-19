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
    # The leaf of attrs['category_path'] (e.g. "Mobile Phones"), denormalized
    # because ranking and faceting both aggregate it per request and a JSONB
    # array element is avoidable work. Spec 4.4.
    category_leaf = models.CharField(max_length=128, blank=True, db_index=True)

    # Set by `dedupe_listings`, not by the adapter. A separate flag rather than
    # reusing `is_active`: that one is derived from the source and would be
    # overwritten on the next reindex, so the two would fight.
    is_duplicate = models.BooleanField(default=False)
    # How many rows this one stands for, including itself.
    duplicate_count = models.IntegerField(default=1)
    dedupe_key = models.CharField(max_length=64, blank=True)

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


class DocumentReport(models.Model):
    """User-reported staleness. Spec 5.7.

    Inert data by design. A report must never trigger reprocessing on its own:
    the endpoint is public and transcription plus enrichment cost real money
    per document, so auto-reprocessing would let anyone loop the endpoint and
    spend the API budget. The admin queue sorts by report count so genuinely
    broken records surface first, and a human action is what re-queues.
    """

    REASONS = [("stale", "stale"), ("wrong_details", "wrong details"),
               ("dead_link", "dead link"), ("spam", "spam"), ("other", "other")]
    STATUSES = [("open", "open"), ("actioned", "actioned"), ("rejected", "rejected")]

    # SearchDocument is partitioned; a real FK constraint is unavailable.
    document = models.ForeignKey("search.SearchDocument", on_delete=models.DO_NOTHING,
                                 db_constraint=False, related_name="reports")
    reason = models.CharField(max_length=24, choices=REASONS)
    note = models.TextField(blank=True)
    reporter_ip_hash = models.CharField(max_length=64)   # rate limiting only
    status = models.CharField(max_length=16, choices=STATUSES, default="open")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "reason", "reporter_ip_hash"],
                name="uniq_report_per_reporter_reason",
            )
        ]
        indexes = [models.Index(fields=["status", "-created_at"],
                                name="report_status_created")]


class SpecKey(models.Model):
    """The curated facet registry. Spec 4.4.

    Extraction is open -- the unit extractor and the LLM may produce any
    key_raw -- but only a key promoted here with is_facetable=True becomes a
    filter. Everything else is stored, shown on the detail page, and queued for
    one-click promotion. That asymmetry is what keeps the attribute space from
    degenerating into thousands of junk facets while still letting a new
    product category arrive without a schema change.
    """

    DATATYPES = [("numeric", "numeric"), ("enum", "enum"), ("bool", "bool")]
    WIDGETS = [("range", "range"), ("checkbox", "checkbox"), ("toggle", "toggle")]

    key = models.CharField(max_length=64, unique=True)
    label_en = models.CharField(max_length=64)
    label_dv = models.CharField(max_length=64, blank=True)
    datatype = models.CharField(max_length=16, choices=DATATYPES)
    unit = models.CharField(max_length=16, blank=True)
    unit_aliases = models.JSONField(default=list, blank=True)
    value_aliases = models.JSONField(default=dict, blank=True)
    widget = models.CharField(max_length=16, choices=WIDGETS, default="checkbox")
    # Leaf categories where this key is meaningful. Empty means "anywhere",
    # which is right for `brand` and wrong for `Type`.
    categories = models.JSONField(default=list, blank=True)
    priority = models.IntegerField(default=100)
    is_facetable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "key"]

    def __str__(self):
        return self.key

    def resolve_value(self, raw: str) -> str:
        v = (raw or "").strip()
        return self.value_aliases.get(v, v)

    def matches_unit(self, u: str) -> bool:
        u = (u or "").strip().lower()
        if not u:
            return False
        if u == (self.unit or "").lower():
            return True
        return u in {a.lower() for a in self.unit_aliases}


class DocumentSpec(models.Model):
    """One row per extracted attribute. Spec 4.4.

    Relational rather than JSONB because facet discovery is an aggregation
    over the candidate set, and GROUP BY on indexed columns beats unnesting a
    JSONB array on every request. Volume is small: ~20,000 products times ~4
    specs, under 100,000 rows.
    """

    # SearchDocument is LIST-partitioned, so a real FK constraint is not
    # available (spec 12.2). A dangling row is inert; sync_specs prunes them.
    document = models.ForeignKey("search.SearchDocument", on_delete=models.DO_NOTHING,
                                 db_constraint=False, related_name="specs")
    key = models.ForeignKey(SpecKey, null=True, blank=True,
                            on_delete=models.SET_NULL, related_name="values")
    key_raw = models.CharField(max_length=64)
    value_num = models.FloatField(null=True, blank=True)
    value_text = models.CharField(max_length=128, blank=True)
    unit = models.CharField(max_length=16, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "key_raw", "value_num", "value_text"],
                name="uniq_documentspec_value",
                nulls_distinct=False,
            )
        ]
        indexes = [
            models.Index(fields=["document"], name="docspec_document"),
            models.Index(fields=["key", "value_text"], name="docspec_key_text"),
            models.Index(fields=["key", "value_num"], name="docspec_key_num"),
            models.Index(fields=["key_raw"], name="docspec_key_raw"),
        ]

    def __str__(self):
        return f"{self.key_raw}={self.value_num or self.value_text}{self.unit}"


class Category(models.Model):
    """The canonical taxonomy. Source-independent by design.

    iBay happens to publish a hierarchy with `Accessories` and `Parts` as
    literal path segments; gazette publishes none, and a future source may
    publish flat or wrong tags. Sources map INTO this tree (SourceCategoryMap)
    and query parsing reads only this.

    `tier` is curated per node rather than parsed from a path segment, because
    the segment exists in exactly one source's paths.
    """

    TIERS = [("family", "family"), ("primary", "primary product"),
             ("accessory", "accessory"), ("part", "part"),
             ("service", "service")]

    key = models.SlugField(max_length=64, unique=True)
    label_en = models.CharField(max_length=128)
    label_dv = models.CharField(max_length=128, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.PROTECT, related_name="children")
    tier = models.CharField(max_length=16, choices=TIERS)
    doc_type = models.CharField(max_length=32, blank=True)
    # Query words that should select this node but are absent from its label.
    # Measured in P10: 440 titles say "glass" and no label contains it.
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["key"]

    def __str__(self):
        return self.key


class SourceCategoryMap(models.Model):
    """One row per distinct source category path.

    Keyed on the full path, not the leaf: iBay spells `Charger` under two
    different families and `Car Accessories` under two more, so a leaf-keyed
    map merges categories that rank and facet differently.

    `category = NULL` is a legal, reviewed decision meaning "no canonical
    category for this path", and is not the same as an absent row, which means
    "not yet reviewed".
    """

    source = models.CharField(max_length=32)
    path = models.JSONField(default=list)
    path_key = models.CharField(max_length=64)
    category = models.ForeignKey(Category, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="source_paths")
    note = models.CharField(max_length=256, blank=True)
    document_count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "path_key"],
                                    name="uniq_sourcecategory_path")
        ]
        indexes = [models.Index(fields=["source", "-document_count"],
                                name="sourcecat_source_count")]

    def __str__(self):
        return f"{self.source}: {' > '.join(self.path)}"
