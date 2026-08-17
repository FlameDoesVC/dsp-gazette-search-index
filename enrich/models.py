"""Persisted LLM output. Spec 4.2, 3.3.

This table is the most expensive artifact in the system: every row cost an API
call. It is keyed by a hash of the exact text that was fed to the model, so a
re-scrape that changed nothing re-uses it, and re-indexing never re-runs the
model. Reindexing and re-enriching are independent operations.

Not partitioned. It is one row per SearchDocument at most, it is read by exact
key, and it does not churn.
"""

from django.db import models

STATUS_CHOICES = [
    ("pending", "pending"),
    ("ok", "ok"),
    ("needs_review", "needs review"),
    ("failed", "failed"),
]


class EnrichedRecord(models.Model):
    # Same natural key as SearchDocument. Deliberately not a FK: SearchDocument
    # is partitioned, and enrichment must survive a full reindex that drops and
    # rebuilds those rows.
    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)

    content_hash = models.CharField(max_length=64)
    doc_type = models.CharField(max_length=32)
    doc_type_confidence = models.FloatField(default=0.0)

    canonical_title_en = models.CharField(max_length=512, blank=True)
    canonical_title_dv = models.CharField(max_length=512, blank=True)
    summary_en = models.CharField(max_length=240, blank=True)
    summary_dv = models.CharField(max_length=240, blank=True)

    attrs = models.JSONField(default=dict, blank=True)
    keywords = models.JSONField(default=list, blank=True)

    model_name = models.CharField(max_length=64, blank=True)
    prompt_version = models.IntegerField(default=0)
    validation = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_key"], name="uniq_enriched_source_key"
            )
        ]
        indexes = [
            models.Index(fields=["source", "status"], name="enriched_source_status"),
            models.Index(fields=["content_hash"], name="enriched_content_hash"),
        ]

    def __str__(self):
        return f"{self.source}:{self.source_key} [{self.status}]"
