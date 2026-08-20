"""Entity resolution precision against a hand-labelled set.

This number gates the backfill rather than being reported after it: a wrong
link puts wrong specs on a real listing, and there is no downstream stage that
can detect it.

Two properties make the gate usable more than once, and neither came for free:

- The draw is a hash of the source_key, not a seeded `random.sample` over the
  id list. Seeding looks reproducible and is not: `sample` picks by index, so a
  corpus that gained 9,000 listings returns a different draw from the same seed,
  and two scores taken a week apart were never measuring the same listings.
- Continuity comes from the file, not from the draw. Whatever is already in it
  stays in it, and `--sample N` only tops up to N. That is what makes the score
  comparable across identity changes.
- A refresh keeps the labels it can. Re-running `--sample` used to overwrite the
  file, which meant every identity change cost a fresh 50 rows of hand
  labelling, so in practice the gate was scored once and then went stale.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.models import EntityLink
from search.models import SearchDocument

GOLDEN = Path("catalog/eval/golden.yaml")

# Fields recomputed on every refresh. Everything else in a row is the reviewer's.
_DERIVED = ("title", "resolved_entity", "kind", "identity", "confidence",
            "listing_count", "siblings")


class Command(BaseCommand):
    help = "Sample listings for labelling, or score the labelled set."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=0,
                            help="Write N rows for a human to label. Existing "
                                 "rows are kept and re-derived.")
        parser.add_argument("--source", default="ibay")
        parser.add_argument("--refresh", action="store_true",
                            help="Re-derive the existing rows against the "
                                 "current resolution without sampling more.")

    def handle(self, *args, **opts):
        if opts["sample"] or opts["refresh"]:
            return self._sample(opts["source"], opts["sample"])
        return self._score()

    # -- sampling ----------------------------------------------------------

    def _row(self, doc):
        link = EntityLink.objects.filter(
            source=doc.source, source_key=doc.source_key
        ).select_related("entity").first()
        entity = link.entity if link else None

        # The siblings are the whole point of this file. `title_en` on an
        # entity is empty until it is profiled, so a reviewer given only the
        # entity key and brand cannot tell a correct grouping from a wrong
        # one -- the question "does this listing belong with those listings"
        # needs those listings shown.
        #
        # Ordered, because the sibling list is part of the signature that
        # decides whether a label still applies. An unordered slice of three
        # would invalidate labels at random.
        siblings: list[str] = []
        if entity is not None:
            keys = list(EntityLink.objects.filter(entity=entity)
                        .exclude(source_key=doc.source_key)
                        .order_by("source_key")
                        .values_list("source_key", flat=True)[:3])
            if keys:
                found = dict(SearchDocument.objects
                             .filter(source=doc.source, source_key__in=keys)
                             .values_list("source_key", "title_en"))
                siblings = [found[k] for k in keys if k in found]

        return {
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
        }

    def _sample(self, source, n):
        existing = {}
        if GOLDEN.exists():
            for row in yaml.safe_load(GOLDEN.read_text()) or []:
                existing[str(row["source_key"])] = row

        wanted = set(existing)
        if n > len(wanted):
            keys = list(SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
                        .filter(source=source, doc_type="shopping")
                        .values_list("source_key", flat=True))
            # Rank by a salted hash of the key itself, so the same listing gets
            # the same rank however large the corpus is. Topping up 50 to 80
            # therefore adds 30 rows and disturbs none of the 50.
            keys.sort(key=lambda k: hashlib.blake2b(
                f"20260819:{k}".encode(), digest_size=8).digest())
            for key in keys:
                if len(wanted) >= n:
                    break
                wanted.add(key)

        docs = {d.source_key: d for d in SearchDocument.objects
                .filter(source=source, source_key__in=sorted(wanted))}

        rows, kept, invalidated, missing = [], 0, 0, 0
        for key in sorted(wanted):
            doc = docs.get(key)
            if doc is None:
                # The listing left the corpus. Dropping it silently would shrink
                # the denominator without saying so.
                missing += 1
                continue
            row = self._row(doc)
            previous = existing.get(key)
            if previous is None:
                row["correct"] = None
            elif all(previous.get(f) == row[f] for f in _DERIVED):
                row["correct"] = previous.get("correct")
                if previous.get("note"):
                    row["note"] = previous["note"]
                kept += 1
            else:
                # The grouping this label was about no longer exists. Carrying
                # the verdict over would score a judgement nobody made.
                row["correct"] = None
                row["was"] = {
                    "correct": previous.get("correct"),
                    "identity": previous.get("identity"),
                    "note": previous.get("note"),
                }
                invalidated += 1
            rows.append(row)

        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(yaml.safe_dump(rows, allow_unicode=True,
                                         sort_keys=False))
        unlabelled = sum(1 for r in rows if r.get("correct") is None)
        self.stdout.write(self.style.SUCCESS(
            f"{len(rows)} rows in {GOLDEN}: {kept} labels kept, "
            f"{invalidated} invalidated by a changed grouping, "
            f"{unlabelled} awaiting `correct:`"))
        if missing:
            self.stdout.write(self.style.WARNING(
                f"{missing} previously sampled listings are no longer in the "
                f"corpus and were dropped"))

    # -- scoring -----------------------------------------------------------

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
        unlabelled = len(rows) - len(labelled)
        if unlabelled:
            self.stdout.write(self.style.WARNING(
                f"{unlabelled} of {len(rows)} rows are unlabelled and are not "
                f"in this score"))
        if precision < 0.90:
            self.stdout.write(self.style.ERROR(
                "precision below 90%: do not backfill profiles yet"))
