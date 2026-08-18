"""Rebuild tsvectors in place from stored fields. Spec 7.

A helper for tests and for re-running after a `SEARCH_DV_INDEX_MODE` change
over an already-stored corpus. Body text is never stored (spec 12.1), so this
rebuilds from titles and summaries only -- `reindex` is the full rebuild that
also includes body text.

Runs on the default alias, unlike `reindex` (which streams on `direct`): this
command has no server-side cursor requirement.
"""

from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from search.indexing import _DV_EXPRS, _VECTOR_SQL, _vector_params
from search.models import SearchDocument


class Command(BaseCommand):
    help = "Rebuild tsvectors in place from stored title/summary fields."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None, help="Source key; all by default.")
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        qs = SearchDocument.objects.all().order_by("id")
        if options["source"]:
            qs = qs.filter(source=options["source"])

        mode = getattr(settings, "SEARCH_DV_INDEX_MODE", "dual")
        dv_expr = _DV_EXPRS.get(mode, _DV_EXPRS["dual"])
        batch_size = options["batch_size"]
        done = 0

        for doc in qs.iterator(chunk_size=batch_size):
            draft = SimpleNamespace(
                source=doc.source,
                source_key=doc.source_key,
                title_en=doc.title_en,
                text_en="",
                summary_en=doc.summary_en,
                title_dv=doc.title_dv,
                text_dv="",
                title_latin=doc.title_latin,
                text_latin="",
            )
            row = _vector_params(draft)
            placeholder = "(" + ", ".join(["%s"] * 11) + ")"
            sql = _VECTOR_SQL.format(
                dv_expr=dv_expr, values=placeholder
            )
            with connection.cursor() as cur:
                cur.execute(sql, list(row))
            done += 1
            if done % batch_size == 0:
                self.stdout.write(f"rebuilt {done} vectors...")

        self.stdout.write(self.style.SUCCESS(f"rebuilt vectors for {done} documents"))
