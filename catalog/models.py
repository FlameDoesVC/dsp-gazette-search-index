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
