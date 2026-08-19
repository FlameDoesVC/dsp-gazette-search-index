"""The entity layer. Spec section 6.

Nothing here has a FK to SearchDocument: that table is LIST-partitioned, and
links must survive a full reindex that drops and rebuilds its rows, so
EntityLink stores (source, source_key) exactly as EnrichedRecord does.
"""

from django.db import models


class Brand(models.Model):
    """The product-identity vocabulary.

    Seeded from the 35 brand values already in DocumentSpec and grown by hand.
    A vocabulary rather than 'the first token of the title' because a wrong
    brand produces a wrong entity, which puts wrong specs on a real listing.
    """

    name = models.CharField(max_length=64, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


PROVENANCE = [
    ("scraped", "scraped from the source"),
    ("correction", "crowdsourced correction"),
    ("consensus", "agreed across listings"),
    ("grounded", "found in listing text"),
    ("inferred", "model knowledge"),
]

ENTITY_KINDS = [("product", "product"), ("service", "service")]

PROFILE_STATUS = [("pending", "pending"), ("ok", "ok"),
                  ("needs_review", "needs review"), ("failed", "failed")]


class Entity(models.Model):
    """One real-world thing: a product model, or one provider's service.

    `key` is deterministic (catalog/identity.py), so re-resolution is a no-op
    and a reposted listing rejoins the entity it belongs to.
    """

    kind = models.CharField(max_length=16, choices=ENTITY_KINDS)
    key = models.CharField(max_length=64, unique=True)

    brand = models.CharField(max_length=64, blank=True)
    model_name = models.CharField(max_length=128, blank=True)
    variant = models.CharField(max_length=64, blank=True)
    service_type = models.CharField(max_length=64, blank=True)
    provider_key = models.CharField(max_length=64, blank=True, db_index=True)

    category = models.ForeignKey("search.Category", null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="entities")

    title_en = models.CharField(max_length=256, blank=True)
    title_dv = models.CharField(max_length=256, blank=True)
    summary_en = models.CharField(max_length=240, blank=True)
    summary_dv = models.CharField(max_length=240, blank=True)

    identity_confidence = models.FloatField(default=0.0)
    profile_status = models.CharField(max_length=16, choices=PROFILE_STATUS,
                                      default="pending")
    profile_prompt_version = models.IntegerField(default=0)
    profile_error = models.TextField(blank=True)
    listing_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "entities"
        indexes = [
            models.Index(fields=["kind", "profile_status"],
                         name="entity_kind_status"),
            models.Index(fields=["-listing_count"], name="entity_listing_count"),
        ]

    def __str__(self):
        return self.title_en or self.key[:12]


class EntityLink(models.Model):
    """Which documents an entity stands for.

    (source, source_key) rather than a document FK: SearchDocument is
    LIST-partitioned, and links must survive a reindex that drops and rebuilds
    its rows -- the same reasoning as EnrichedRecord.
    """

    METHODS = [("identity_match", "identity match"),
               ("seller_service", "seller and service"),
               ("manual", "manual")]

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE,
                               related_name="links")
    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)
    method = models.CharField(max_length=24, choices=METHODS)
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "source_key"],
                                    name="uniq_entitylink_document")
        ]
        indexes = [models.Index(fields=["entity"], name="entitylink_entity")]

    def __str__(self):
        return f"{self.source}:{self.source_key} -> {self.entity_id}"


class EntityField(models.Model):
    """One candidate value for one field, with where it came from.

    Every candidate is kept and the winner is flagged, so a correction beats an
    inference without destroying the evidence -- which is what makes a bad
    auto-apply diagnosable after the fact.
    """

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE,
                               related_name="fields")
    key_raw = models.CharField(max_length=64)
    key = models.ForeignKey("search.SpecKey", null=True, blank=True,
                            on_delete=models.SET_NULL, related_name="entity_values")
    value_num = models.FloatField(null=True, blank=True)
    value_text = models.CharField(max_length=128, blank=True)
    unit = models.CharField(max_length=16, blank=True)
    provenance = models.CharField(max_length=16, choices=PROVENANCE)
    confidence = models.FloatField(default=0.0)
    support_count = models.IntegerField(default=1)
    is_winner = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "key_raw", "provenance", "value_num",
                        "value_text"],
                name="uniq_entityfield_value", nulls_distinct=False,
            )
        ]
        indexes = [
            models.Index(fields=["entity", "is_winner"], name="entityfield_winner"),
            models.Index(fields=["key_raw"], name="entityfield_key_raw"),
        ]

    def __str__(self):
        return f"{self.key_raw}={self.value_num or self.value_text} [{self.provenance}]"


class FieldProposal(models.Model):
    """A crowdsourced correction. Auto-applies on agreement (spec section 10).

    Unique per (field, value, proposer) so one IP hash counts once. An empty
    value means 'this field is wrong, drop it'.
    """

    STATUSES = [("pending", "pending"), ("applied", "applied"),
                ("rejected", "rejected"), ("conflicted", "conflicted")]

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE,
                               related_name="proposals")
    key_raw = models.CharField(max_length=64)
    value_num = models.FloatField(null=True, blank=True)
    value_text = models.CharField(max_length=128, blank=True)
    unit = models.CharField(max_length=16, blank=True)
    proposer_ip_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=STATUSES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entity", "key_raw", "value_num", "value_text",
                        "proposer_ip_hash"],
                name="uniq_proposal_per_proposer", nulls_distinct=False,
            )
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"],
                         name="proposal_status_created"),
            models.Index(fields=["entity", "key_raw"], name="proposal_field"),
        ]
