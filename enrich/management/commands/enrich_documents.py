"""manage.py enrich_documents

    --source ibay --type job --limit N --provider deepseek --force --stale
    --dry-run

Reports the count it is about to process before spending anything, because a
WHERE clause can mark 51,000 rows as easily as one (spec 5.7).
"""

from __future__ import annotations

import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from enrich.pipeline import COLD_PASS_ORDER, run_pass, select_keys
from enrich.prompts import PROMPT_VERSION
from search.adapters import base as adapters


class Command(BaseCommand):
    help = "Run the enrichment pass over one source."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--type", dest="doc_type", default=None,
                            help="Only documents currently of this doc_type.")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--provider", default=None,
                            help="Override ENRICH_PROVIDER for this run.")
        parser.add_argument("--concurrency", type=int, default=None)
        parser.add_argument("--force", action="store_true",
                            help="Ignore the content_hash and prompt_version gates.")
        parser.add_argument("--stale", action="store_true",
                            help="Only documents with stale_marked_at set (spec 5.7).")
        parser.add_argument("--cold-pass", action="store_true",
                            help="Run job, news, property, shopping in that order.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report the count and exit without calling a provider.")

    def handle(self, *args, **opts):
        source = opts["source"]
        try:
            adapters.get_adapter(source)
        except KeyError as exc:
            raise CommandError(f"no adapter registered for {source!r}") from exc

        if opts["provider"]:
            settings.ENRICH_PROVIDER = opts["provider"]

        types = list(COLD_PASS_ORDER) if opts["cold_pass"] else [opts["doc_type"]]

        for doc_type in types:
            keys = list(select_keys(
                source=source,
                prompt_version=PROMPT_VERSION,
                doc_type=doc_type,
                only_stale=opts["stale"],
                force=opts["force"],
                limit=opts["limit"],
            ))
            label = doc_type or "all types"
            self.stdout.write(f"{source} / {label}: {len(keys)} documents to enrich")

            if opts["dry_run"] or not keys:
                continue

            counts = asyncio.run(run_pass(keys, concurrency=opts["concurrency"]))
            self.stdout.write(self.style.SUCCESS(
                f"  ok={counts['ok']} needs_review={counts['needs_review']} "
                f"failed={counts['failed']} skipped={counts['skipped']}"
            ))
            usage = counts.get("usage") or {}
            if usage.get("calls"):
                per = usage["prompt_tokens"] / usage["calls"]
                hit = usage["cache_hit_tokens"]
                total_in = usage["prompt_tokens"] or 1
                self.stdout.write(
                    f"  {usage['calls']} calls, "
                    f"{usage['prompt_tokens']:,} input tokens "
                    f"({per:,.0f}/call, {100 * hit / total_in:.0f}% cache hit), "
                    f"{usage['completion_tokens']:,} output tokens")

        # Deliberately does NOT clear stale_marked_at: reindex is the last
        # stage and the only one that clears the work ticket (spec 5.7).
        self.stdout.write("Done. Run `manage.py reindex --stale` to publish.")
