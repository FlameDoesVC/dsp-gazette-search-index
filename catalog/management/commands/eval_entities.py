"""Entity resolution precision against a hand-labelled set.

This number gates the backfill rather than being reported after it: a wrong
link puts wrong specs on a real listing, and there is no downstream stage that
can detect it.
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.models import EntityLink
from search.models import SearchDocument

GOLDEN = Path("catalog/eval/golden.yaml")


class Command(BaseCommand):
    help = "Sample listings for labelling, or score the labelled set."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=0,
                            help="Write N unlabelled rows for a human to fill.")
        parser.add_argument("--source", default="ibay")

    def handle(self, *args, **opts):
        if opts["sample"]:
            return self._sample(opts["source"], opts["sample"])
        return self._score()

    def _sample(self, source, n):
        rng = random.Random(20260819)          # fixed seed: a rerun resamples
                                               # the same listings
        ids = list(SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
                   .filter(source=source, doc_type="shopping")
                   .values_list("id", flat=True))
        picked = rng.sample(ids, min(n, len(ids)))
        rows = []
        for doc in SearchDocument.objects.filter(id__in=picked):
            link = EntityLink.objects.filter(
                source=doc.source, source_key=doc.source_key
            ).select_related("entity").first()
            entity = link.entity if link else None

            # The siblings are the whole point of this file. `title_en` on an
            # entity is empty until task 6 profiles it, so a reviewer given only
            # the entity key and brand cannot tell a correct grouping from a
            # wrong one -- the question "does this listing belong with those
            # listings" needs those listings shown.
            siblings: list[str] = []
            if entity is not None:
                keys = list(EntityLink.objects.filter(entity=entity)
                            .exclude(source_key=doc.source_key)
                            .values_list("source_key", flat=True)[:3])
                if keys:
                    siblings = list(
                        SearchDocument.objects
                        .filter(source=doc.source, source_key__in=keys)
                        .values_list("title_en", flat=True))

            rows.append({
                "source_key": doc.source_key,
                "title": doc.title_en,
                "resolved_entity": entity.key[:16] if entity else None,
                "kind": entity.kind if entity else None,
                "identity": (
                    f"{entity.brand} {entity.model_name}".strip()
                    or f"{entity.provider_key} / {entity.service_type}"
                ) if entity else None,
                "confidence": entity.identity_confidence if entity else None,
                "listing_count": entity.listing_count if entity else None,
                "siblings": siblings,
                # A human sets this to true or false. `null` means unreviewed
                # and is excluded from the score rather than counted as a pass.
                "correct": None,
            })
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False))
        self.stdout.write(self.style.SUCCESS(
            f"wrote {len(rows)} rows to {GOLDEN}; fill in `correct:` by hand"))

    def _score(self):
        if not GOLDEN.exists():
            self.stderr.write("no golden set; run with --sample 50 first")
            return
        rows = yaml.safe_load(GOLDEN.read_text()) or []
        labelled = [r for r in rows if r.get("correct") is not None]
        if not labelled:
            self.stderr.write(f"{len(rows)} rows, none labelled yet")
            return
        linked = [r for r in labelled if r.get("resolved_entity")]
        correct = [r for r in linked if r["correct"]]
        precision = len(correct) / len(linked) if linked else 0.0
        coverage = len(linked) / len(labelled)
        self.stdout.write(
            f"{len(labelled)} labelled, {len(linked)} linked\n"
            f"precision {precision:.2%}   coverage {coverage:.2%}")
        if precision < 0.90:
            self.stdout.write(self.style.ERROR(
                "precision below 90%: do not backfill profiles yet"))
