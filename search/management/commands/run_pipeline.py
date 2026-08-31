"""Run the full content pipeline in one command.

Equivalent to running, in order:
  compilemessages
  sync_gazette [--full]
  fill_entity_translations
  retranslate_gazette --all --only-missing
  extract_attachments
  reindex --source gazette
  enrich_documents --source gazette --cold-pass
  translate_card_vocab --source gazette
  reindex --source gazette
  fill_bilingual --source gazette
  backfill_phones --source gazette
  dedupe_listings --source gazette
  sync_specs --source gazette --type job
  sync_specs --source gazette --type property
  sync_specs --source gazette --type news
  rebuild_suggest_terms

Each stage is a separate management command, run through call_command, so a
step that hits an error (a missing API key, a transient network failure)
prints its own message and returns rather than taking the rest of the run
down with it -- each underlying command already degrades this way on its
own (see retranslate_gazette's GEMINI_API_KEY guard), so this wrapper does
not add its own try/except around each step.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

SPEC_TYPES = ("job", "property", "news")


class Command(BaseCommand):
    help = "Run the full gazette pipeline: sync, translate, extract, index, enrich, reindex, and housekeeping."

    def add_arguments(self, parser):
        parser.add_argument("--full-sync", action="store_true",
                            help="Pass --full to sync_gazette (crawl every page).")
        parser.add_argument("--skip-sync", action="store_true")
        parser.add_argument("--skip-translate", action="store_true")
        parser.add_argument("--skip-attachments", action="store_true")
        parser.add_argument("--skip-enrich", action="store_true")
        parser.add_argument("--enrich-provider", default=None,
                            help="Override the enrichment provider for this run.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the steps that would run, without running them.")

    def handle(self, *args, **opts):
        steps: list[tuple[str, dict]] = [("compilemessages", {})]

        if not opts["skip_sync"]:
            steps.append(("sync_gazette", dict(full=opts["full_sync"])))

        if not opts["skip_translate"]:
            steps.append(("fill_entity_translations", {}))
            steps.append(("retranslate_gazette", dict(all=True, only_missing=True)))

        if not opts["skip_attachments"]:
            steps.append(("extract_attachments", {}))

        # First pass: publish sync/translate/attachment output before enrichment
        # reads it.
        steps.append(("reindex", dict(source="gazette")))

        if not opts["skip_enrich"]:
            enrich_kwargs: dict = dict(source="gazette", cold_pass=True)
            if opts["enrich_provider"]:
                enrich_kwargs["provider"] = opts["enrich_provider"]
            steps.append(("enrich_documents", enrich_kwargs))
            # Card-vocab labels resolve from TranslationCache at request time
            # (search.vocab.annotate_free_text), not from the card itself, so
            # this only needs to run before the next request -- but it does
            # need enrichment's fresh strings to exist first.
            steps.append(("translate_card_vocab", dict(source="gazette")))
            # Second pass: publish the enrichment cards.
            steps.append(("reindex", dict(source="gazette")))

        steps.append(("fill_bilingual", dict(source="gazette")))
        steps.append(("backfill_phones", dict(source="gazette")))
        steps.append(("dedupe_listings", dict(source="gazette")))
        for doc_type in SPEC_TYPES:
            steps.append(("sync_specs", dict(source="gazette", doc_type=doc_type)))
        steps.append(("rebuild_suggest_terms", {}))

        if opts["dry_run"]:
            for name, kwargs in steps:
                self.stdout.write(f"  {name} {kwargs}")
            return

        for name, kwargs in steps:
            self.stdout.write(self.style.MIGRATE_HEADING(f"--- {name} ---"))
            call_command(name, **kwargs)

        self.stdout.write(self.style.SUCCESS("pipeline complete"))
