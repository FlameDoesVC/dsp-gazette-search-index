"""Fill the missing language side on every document. Spec 5.5, 10.

Short fields only -- titles, summaries, and the two attrs fields that are
genuinely per-document open prose (`role`, `qualifications`). Never bodies: a
gazette body averages 5,569 characters and there are 51,000 of them.

Three layers leave very little for this command:

  - language-neutral fields (compensation, dates, URLs) are never translated
  - closed vocabularies (position_type, job_category, ...) resolve through the
    gettext catalog in search/vocab.py -- translating them per document would
    reintroduce the spelling-drift problem
  - entity names (employer, office) are filled once in `fill_entity_translations`

Translation, not transliteration. See P5 Task 0C step 5 for the measured
comparison; feeding English orthography through the query-side transliterator
produces phonetic nonsense.

Deduplicated before dispatch: 20,442 iBay titles are 12,353 unique strings,
and TranslationCache absorbs the rest.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from search.indexing import _rebuild_vectors
from search.models import SearchDocument

logger = logging.getLogger(__name__)

# Below this a translator adds nothing: 'XL', 'A4', '2BR'.
MIN_CHARS = 3
MAX_CHARS = 512
PAIRS = [("title_en", "title_dv"), ("summary_en", "summary_dv")]
# Fields translated per document. Everything else is a closed vocabulary
# (search/vocab.py) or an entity name (fill_entity_translations) and must not
# be translated here -- doing so reintroduces the spelling-drift problem.
PROSE_ATTRS = ("role", "qualifications")


class Command(BaseCommand):
    help = "Ensure every document has both a Dhivehi and an English title/summary."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from core.translate import translate_dv_to_en_sync, translate_en_to_dv_sync

        qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
        if opts["source"]:
            qs = qs.filter(source=opts["source"])
        qs = qs.filter(
            (Q(title_dv="") & ~Q(title_en="")) | (Q(title_en="") & ~Q(title_dv=""))
            | (Q(summary_dv="") & ~Q(summary_en=""))
            | (Q(summary_en="") & ~Q(summary_dv=""))
            | ~Q(attrs={})
        ).only("id", "title_en", "title_dv", "summary_en", "summary_dv", "attrs")
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        if opts["dry_run"]:
            self.stdout.write(f"{qs.count()} documents are missing a language side")
            return

        # One call per unique string, not per row.
        memo: dict[tuple[str, str], str] = {}

        def convert(text: str, direction: str) -> str:
            key = (direction, text)
            if key in memo:
                return memo[key]
            if len(text.strip()) < MIN_CHARS:
                out = text                      # nothing to translate
            else:
                fn = (translate_en_to_dv_sync if direction == "dv"
                      else translate_dv_to_en_sync)
                try:
                    out = fn(text[:MAX_CHARS]) or text
                except Exception:
                    # Measured: a pure-brand title can stall for 127s and
                    # return None. Falling back to the source string keeps the
                    # field populated, which is the whole point of this task.
                    logger.warning("translation failed, keeping source", exc_info=True)
                    out = text
            memo[key] = out
            return out

        def fill_attrs(doc: SearchDocument) -> bool:
            """Translate open-prose attrs into their _dv siblings. Returns True
            when anything changed. Closed vocabularies and entity names are
            deliberately never touched here."""
            attrs = dict(doc.attrs or {})
            touched = False
            role = attrs.get("role")
            if role and not attrs.get("role_dv"):
                attrs["role_dv"] = convert(str(role)[:MAX_CHARS], "dv")
                touched = True
            quals = attrs.get("qualifications")
            if quals and not attrs.get("qualifications_dv"):
                attrs["qualifications_dv"] = [
                    convert(str(q)[:MAX_CHARS], "dv") for q in quals
                ]
                touched = True
            if touched:
                doc.attrs = attrs
            return touched

        written, batch = 0, []
        for doc in qs.iterator(chunk_size=opts["batch_size"]):
            touched = False
            for en_field, dv_field in PAIRS:
                en, dv = getattr(doc, en_field), getattr(doc, dv_field)
                if en and not dv:
                    setattr(doc, dv_field, convert(en, "dv")[:512])
                    touched = True
                elif dv and not en:
                    setattr(doc, en_field, convert(dv, "en")[:512])
                    touched = True
            touched = fill_attrs(doc) or touched
            if touched:
                batch.append(doc)
            if len(batch) >= opts["batch_size"]:
                written += self._flush(batch)
                batch = []
        if batch:
            written += self._flush(batch)

        self.stdout.write(self.style.SUCCESS(
            f"{written} documents filled, {len(memo)} unique strings converted"
        ))

    def _flush(self, batch) -> int:
        SearchDocument.objects.bulk_update(
            batch, ["title_en", "title_dv", "summary_en", "summary_dv", "attrs"],
            batch_size=200,
        )
        # P2 builds vector_dv with the dual/fili/skeleton expressions. Call the
        # shared builder rather than a plain SearchVector, or the rows this
        # command touches get the wrong analysis and Dhivehi search silently
        # degrades for exactly the documents it was meant to improve.
        _rebuild_vectors([_vector_input(d) for d in batch])
        return len(batch)


def _vector_input(doc: SearchDocument) -> SimpleNamespace:
    """_rebuild_vectors reads draft-shaped fields including the text_* that are
    never stored; body text is absent here, so it is empty."""
    return SimpleNamespace(
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
