"""Re-resolve category and category_leaf in place.

Separate from reindex because a taxonomy edit is free and reindex is not: the
reprocess chain (P4) clears stale_marked_at, and running it to pick up an admin
click would strand the paid stages.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from search.models import SearchDocument
from search.taxonomy import map_path


class Command(BaseCommand):
    help = "Re-resolve SearchDocument.category from SourceCategoryMap."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
        if opts["source"]:
            qs = qs.filter(source=opts["source"])

        changed = unmapped = 0
        batch: list[SearchDocument] = []
        for doc in qs.only("id", "source", "attrs", "category",
                           "category_leaf").iterator(chunk_size=500):
            path = [str(p) for p in (doc.attrs.get("category_path") or [])]
            category = map_path(doc.source, path)
            leaf = category.label_en if category else (path[-1] if path else "")
            if category is None:
                unmapped += 1
            category_id = category.id if category else None
            if doc.category_id == category_id and doc.category_leaf == leaf:
                continue
            # category_id, NOT category: the document was streamed over the
            # `direct` alias and map_path resolved the Category over `default`,
            # so assigning the instance trips Django's allow_relation check and
            # raises ValueError. Tests never see it -- conftest points
            # STREAM_DB_ALIAS at `default`, which makes both objects same-alias.
            doc.category_id = category_id
            doc.category_leaf = leaf
            batch.append(doc)
            changed += 1
            if len(batch) >= 500 and not opts["dry_run"]:
                SearchDocument.objects.using(settings.STREAM_DB_ALIAS).bulk_update(
                    batch, ["category", "category_leaf"])
                batch.clear()

        if batch and not opts["dry_run"]:
            SearchDocument.objects.using(settings.STREAM_DB_ALIAS).bulk_update(
                batch, ["category", "category_leaf"])

        self.stdout.write(self.style.SUCCESS(
            f"{changed} updated, {unmapped} still unmapped"))
