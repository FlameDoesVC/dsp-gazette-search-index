"""Seed the canonical taxonomy from the distinct source paths in the corpus.

This runs once as a seeding convenience and prints every node and mapping it
proposes. The derivation is a starting point, not the contract: `tier` and the
junk-leaf collapses are exactly the decisions a human must review afterwards in
the admin.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from search.models import Category, SearchDocument, SourceCategoryMap
from search.taxonomy import path_key

# Leaves that carry no information. 1,541 documents sit on these two, and a
# canonical junk node would be worse than a node one level too general.
JUNK_LEAF = re.compile(r"^(general\s*/?\s*other|other|others|misc|"
                       r"miscellaneous|general|other stuff)$", re.I)
SERVICE_HINT = re.compile(r"servic|repair|maintenance|maraamathu|installation|"
                          r"cleaning|tuition|moving|movers", re.I)


def infer_tier(path: list[str]) -> str:
    """Propose a tier from the shape of a source path.

    Two rules here are not obvious and were both established by running this
    against the live corpus rather than by reading the tree:

    - `path[1]` is the FAMILY segment and is excluded from the accessory test.
      A prior marketplace source named families after their contents, so
      `Mobile Phones & Accessories` ends in "Accessories" while its
      `Mobile Phones` child (507 documents) is the most important primary
      node in the corpus. Including path[1] classifies it as an accessory and
      breaks the one thing tier exists to decide.
    - Parts is tested BEFORE accessory, because `Mobile Phones & Accessories >
      Parts > Battery` matches both and is a part.

    A mid-level segment that merely ENDS with "Accessories" does count:
    `Laptop Accessories > Charger` and `Camera Accessories > Lenses` are
    accessories, and an exact-match test alone leaves 30 such nodes claiming to
    be primary products.
    """
    if len(path) == 1:
        return "family"
    mid = path[1:-1]
    below_family = path[2:-1]
    if "Parts" in mid or any(
            s == "Parts" or s.endswith(" Parts") or s.endswith("& Parts")
            for s in below_family):
        return "part"
    if "Accessories" in mid or path[-1].endswith("Accessories") or any(
            s.endswith("Accessories") for s in below_family):
        return "accessory"
    if path[0] == "Services" or SERVICE_HINT.search(path[-1] or ""):
        return "service"
    return "primary"


def category_key(path: list[str]) -> str:
    """Slug of the last two segments, so the two 'Charger' leaves differ."""
    tail = [p for p in path[-2:] if p]
    return slugify("-".join(tail))[:64] or "unmapped"


# Ancestor labels too generic to tell two nodes apart. 'Charger (Accessories)'
# says nothing; 'Charger (Mobile Phones & Accessories)' says everything.
GENERIC_ANCESTOR = {"accessories", "parts", "accessories & parts", "other",
                    "others", "general", "general / other", "services",
                    "other services", "other stuff", "misc", "miscellaneous"}


def category_label(path: list[str], colliding: set[str]) -> str:
    """The display label, qualified only when it would otherwise be ambiguous.

    `category_leaf` is a bare string, and both P9's category-aware ranking and
    spec 8.3's facet priority override group on it. Distinct keys are therefore
    not enough: measured on the live corpus, 14 labels are shared by two nodes,
    and the worst is `Charger` -- 400 phone chargers and 13 laptop chargers
    landing in one ranking bucket, which is the exact defect this taxonomy
    exists to fix.

    Qualifying every label would be noise, so only a colliding leaf gets one.
    """
    leaf = path[-1].strip()
    if leaf.lower() not in colliding or len(path) < 2:
        return leaf
    for segment in reversed(path[:-1]):
        candidate = segment.strip()
        if candidate and candidate.lower() not in GENERIC_ANCESTOR:
            return f"{leaf} ({candidate})"[:128]
    return leaf


class Command(BaseCommand):
    help = "Propose Category nodes and SourceCategoryMap rows from the corpus."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--apply", action="store_true",
                            help="Write rows. Without it, print proposals only.")

    def handle(self, *args, **opts):
        source = opts["source"]
        counts: dict[tuple, int] = {}
        qs = (SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
              .filter(source=source).only("attrs"))
        for doc in qs.iterator(chunk_size=500):
            path = tuple(str(p) for p in (doc.attrs.get("category_path") or []))
            if not path:
                continue
            counts[path] = counts.get(path, 0) + 1

        self.stdout.write(f"{len(counts)} distinct paths, "
                          f"{sum(counts.values())} documents")

        proposals = []
        for path, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            path = list(path)
            # A junk leaf maps to its parent's node, not to a junk node.
            effective = path[:-1] if (JUNK_LEAF.match(path[-1].strip())
                                      and len(path) > 1) else path
            proposals.append((path, effective, infer_tier(effective),
                              category_key(effective), n))

        # Which leaf labels two different nodes would both claim. Computed over
        # the whole proposal set before any label is assigned, because a label is
        # only ambiguous relative to its siblings in the finished tree.
        leaf_owners: dict[str, set[str]] = {}
        for _path, effective, _tier, key, _n in proposals:
            leaf_owners.setdefault(effective[-1].strip().lower(), set()).add(key)
        colliding = {leaf for leaf, keys in leaf_owners.items() if len(keys) > 1}

        for path, effective, tier, key, n in proposals:
            collapsed = " (collapsed to parent)" if effective != path else ""
            label = category_label(effective, colliding)
            qualified = "  <- disambiguated" if label != effective[-1].strip() else ""
            self.stdout.write(f"{n:6d}  {tier:9s}  {key:40s}  "
                              f"{' > '.join(path)}{collapsed}{qualified}")
        self.stdout.write(f"{len(colliding)} ambiguous leaf labels qualified")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING(
                "dry run; re-run with --apply to write"))
            return

        with transaction.atomic():
            # Families first, so parents exist before children reference them.
            nodes: dict[str, Category] = {}
            for path, effective, tier, key, n in proposals:
                parent = None
                if len(effective) > 1:
                    pkey = category_key(effective[:-1])
                    parent = nodes.get(pkey) or Category.objects.filter(
                        key=pkey).first()
                    if parent is None:
                        parent, _ = Category.objects.get_or_create(
                            key=pkey,
                            defaults={"label_en": effective[-2],
                                      "tier": "family"})
                        nodes[pkey] = parent
                node, _ = Category.objects.get_or_create(
                    key=key,
                    defaults={"label_en": category_label(effective, colliding),
                              "tier": tier, "parent": parent})
                nodes[key] = node
                SourceCategoryMap.objects.update_or_create(
                    source=source, path_key=path_key(source, path),
                    defaults={"path": path, "category": node,
                              "document_count": n},
                )

        self.stdout.write(self.style.SUCCESS(
            f"{Category.objects.count()} categories, "
            f"{SourceCategoryMap.objects.count()} mappings"))
