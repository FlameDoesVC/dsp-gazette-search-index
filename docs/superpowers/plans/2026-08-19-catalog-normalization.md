# Catalog Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn 16,608 seller-written iBay listings into normalized profiles carried by canonical entities, so one correction fixes every listing behind it instead of one.

**Architecture:** A canonical category taxonomy that no source writes into directly, then a `catalog.Entity` layer keyed deterministically from cleaned listing text, then a per-entity model call whose every output field is tagged with a provenance tier. The existing grounding validator takes on a second job: instead of deleting facts absent from the source text, it classifies them into `grounded` or `inferred`. Crowdsourced field proposals auto-apply on agreement and sit above everything the model can write.

**Tech Stack:** Django 6.0.5, PostgreSQL 18, pydantic 2.13, django-ninja 1.5, pytest + pytest-django. Existing `EnrichClient` provider chain reused as-is; no new external dependency.

**Spec:** `docs/superpowers/specs/2026-08-19-catalog-normalization-design.md`

**Depends on:** P1 (`DocumentDraft`, `apply_overlays`, `search/indexing.py::_row`), P4 (`EnrichClient`, `extract_candidates`, `enrich/validate.py` primitives, `enrich/overlay.py`), P5 (`api/ratelimit.py`, `FacetOut`, `ResultOut`), P7 (`SpecKey`, `DocumentSpec`, `search/specs/project.py`, `search/specs/discovery.py`), P9 task 4 (`category_leaf`).

**Absorbs:** P10 tasks 1 and 2. After this plan lands, P10 starts at its task 3 and reads `search.Category` as its gazetteer source.

---

## Global Constraints

- **No source writes a raw category path into an entity.** Sources map into `search.Category` through `SourceCategoryMap`; the taxonomy is the contract. Spec section 5.
- **The map is keyed on the full path, never the leaf.** iBay spells `Charger` under both `Mobile Phones & Accessories > Accessories` and `Computer, Tablets & Networking > Laptop Accessories`, and `Car Accessories` under two more. A leaf-keyed map merges them. Spec section 5.
- **Entity keys use the mapped category only, never the classified one.** `SourceCategoryMap` is deterministic and available before resolution; model classification is not. Admitting the classified category into the key would make the key depend on a model call. Spec section 7.1.
- **`enrich/validate.py` is not weakened.** Stage 1 keeps its grounding invariant exactly. Stage 2 reuses the same primitives to *classify* rather than to delete. Spec section 8.
- **The provenance ladder is `scraped > correction > consensus > grounded > inferred`.** `scraped` on top preserves the existing rule that a source's own structured field is never overwritten. Spec section 9.
- **An unresolvable same-tier tie produces no winner at all**, flags the entity `needs_review`, and writes no `DocumentSpec` row. Never pick a side by row order. Spec section 9.
- **Inferred specs are filterable and marked.** Facets carrying inferred values say so; results whose winning values include an inferred one say so. Spec section 9.
- **`POST .../propose` returns 202 unconditionally.** Reporting acceptance, deduplication or throttling is an oracle for probing the quorum. Spec section 11.1.
- **The model never invents a category.** It picks an existing `Category.key` or returns nothing. Same rule as never inventing a digit. Spec section 5.
- **FKs to `SearchDocument` need `db_constraint=False`**, and links store `(source, source_key)` rather than a document FK so they survive a reindex. It is LIST-partitioned. Spec 12.2, spec section 6.2.
- **Streaming uses `.iterator(chunk_size=500)` on the `settings.STREAM_DB_ALIAS` alias.**
- **Never assign a model INSTANCE to a FK on an object streamed over `STREAM_DB_ALIAS`. Assign the `_id`.** A `Category` resolved over `default` and a `SearchDocument` streamed over `direct` are different aliases, so `doc.category = category` raises
  `ValueError: the current database router prevents this relation`. Write
  `doc.category_id = category.id` instead, and pass
  `.using(settings.STREAM_DB_ALIAS)` to the matching `bulk_update`. **No normal
  test catches this**: `conftest.py` points `STREAM_DB_ALIAS` at `default`, which
  makes both objects same-alias. It cost a live command run to find in task 2.
  Tasks 5, 6 and 9 all stream and assign FKs, so it applies to `resolve.py`,
  `profile.py` and every backfill command. The one test that does reach it needs
  `@pytest.mark.django_db(transaction=True, databases=["default", "direct"])`.
- **Nothing time-dependent goes in `card`.** Raw values only.
- Version control is **jj**, not git.

---

## Measured evidence

From the live corpus on 2026-08-19, 20,445 documents, iBay only. Every number
below is quoted in the spec and repeated here because tasks act on them.

```
listings with a phone in title or summary   14,839 of 16,608   89.3%
Services listings   9,173  ->  1,495 (seller, leaf) groups     6.13:1
For Sale listings   7,105  ->  3,802 crude identity keys       1.87:1
For Sale listings with a scraped Brand      2,313
product groups of size 1                    2,824 of 3,802     74%
one advertiser phone (7438649)              1,680 listings, 10% of corpus
documents with an empty category_path         188
documents on an information-free leaf       1,541  ("General / Other" 1,250,
                                                    "Other" 291)
distinct leaves                               278
leaf labels ambiguous across families           9
path depth                                    1 to 5 (348 root-only)
```

Two consequences shape the tasks:

**74% of product groups are singletons**, so `consensus` reaches about a quarter
of products and `inferred` carries the rest. That is why task 7 makes inferred
values filterable rather than display-only.

**Nine ambiguous leaf labels are a live defect, not a future one.**
`category_leaf` is a bare string, and both P9's category-aware ranking and spec
8.3's facet priority override key on it, so phone chargers and laptop chargers
currently share one bucket. Task 1 fixes that whether or not the entity layer
ever ships.

---

## File structure

```
search/
  models.py                    MODIFIED: Category, SourceCategoryMap,
                               SearchDocument.category, .contact_phone
  migrations/0011_category_sourcecategorymap.py
  migrations/0012_searchdocument_category_contact_phone.py
  taxonomy.py                  NEW  path_key, map_path, family_of,
                               primary_sibling_of
  contacts.py                  NEW  PHONE_RE, primary_phone, strip_phones
  indexing.py                  MODIFIED: _row resolves category + phone
  admin.py                     MODIFIED: Category, SourceCategoryMap
  specs/project.py             MODIFIED: fourth input, provenance
  specs/discovery.py           MODIFIED: has_inferred on each facet
  management/commands/seed_taxonomy.py       NEW
  management/commands/map_categories.py      NEW
  management/commands/backfill_phones.py     NEW

catalog/                       NEW APP
  __init__.py  apps.py  admin.py
  models.py                    Brand, Entity, EntityLink, EntityField,
                               FieldProposal
  migrations/0001_initial.py
  identity.py                  clean_title, model_tokens, match_brand,
                               product_key, service_key
  resolve.py                   resolve_document, resolve_source
  schemas.py                   ProfileSpec, ProductProfile, ServiceProfile,
                               EntityProfileOutput
  prompts.py                   PROFILE_PROMPT_VERSION, build_profile_messages
  tiers.py                     classify_origin
  profile.py                   build_profile_input, profile_one, select_entities,
                               run_profile_pass
  merge.py                     PROVENANCE_ORDER, promote_consensus,
                               recompute_winners
  proposals.py                 propose, evaluate_field, apply_ready
  cards.py                     build_service_card
  overlay.py                   apply_entity  (registered in SEARCH_DRAFT_OVERLAYS)
  eval/golden.yaml             50-listing hand-labelled resolution set
  management/commands/seed_brands.py
  management/commands/resolve_entities.py
  management/commands/build_profiles.py
  management/commands/apply_proposals.py
  management/commands/eval_entities.py

api/
  routers/entities.py          NEW  GET /entities/{id}, POST .../propose
  schemas.py                   MODIFIED: EntityOut, EntityFieldOut, ProposalIn,
                               FacetOut.has_inferred, ResultOut.profile_tier
  ratelimit.py                 MODIFIED: proposal_quota_exceeded
  urls.py                      MODIFIED: register the router
  routers/documents.py         MODIFIED: entity_id + spec provenance

enrich/
  preextract.py                MODIFIED: import PHONE_RE from search.contacts

tests/
  search/test_taxonomy.py  test_category_mapping.py  test_contacts.py
  catalog/test_identity.py  test_resolve.py  test_tiers.py  test_profile.py
            test_merge.py  test_proposals.py  test_overlay.py
  api/test_entities.py
```

Why `catalog` is its own app: `enrich` is per-document model output keyed
`(source, source_key)`, and `search` is indexing and query. An entity is neither,
it owns four tables and a public write endpoint, and putting it in either
existing app would mean one of them growing a second unrelated responsibility.

---

## Task 1: The canonical taxonomy

**Files:**
- Modify: `search/models.py`, `search/admin.py`
- Create: `search/taxonomy.py`, `search/management/commands/seed_taxonomy.py`, `search/migrations/0011_category_sourcecategorymap.py`
- Test: `tests/search/test_taxonomy.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  ```python
  Category                       # key, label_en, label_dv, parent, tier,
                                 # doc_type, aliases, is_active
  SourceCategoryMap              # source, path, path_key, category, note
  TIERS = ("family", "primary", "accessory", "part", "service")
  path_key(source: str, path: list[str]) -> str          # 64 hex chars
  map_path(source: str, path: list[str]) -> Category | None
  family_of(category: Category) -> Category
  primary_sibling_of(category: Category) -> Category | None
  ```

- [ ] **Step 1: Write the failing test**

`tests/search/test_taxonomy.py`:

```python
import pytest

from search.models import Category, SourceCategoryMap
from search.taxonomy import (family_of, map_path, path_key,
                             primary_sibling_of)


@pytest.fixture
def taxonomy(db):
    phones = Category.objects.create(key="mobile_phones_family",
                                     label_en="Mobile Phones & Accessories",
                                     tier="family")
    primary = Category.objects.create(key="mobile_phones", label_en="Mobile Phones",
                                      parent=phones, tier="primary")
    charger = Category.objects.create(key="phone_charger", label_en="Charger",
                                      parent=phones, tier="accessory")
    laptops = Category.objects.create(key="laptop_family",
                                      label_en="Computer, Tablets & Networking",
                                      tier="family")
    lcharger = Category.objects.create(key="laptop_charger", label_en="Laptop Charger",
                                       parent=laptops, tier="accessory")
    return dict(family=phones, primary=primary, charger=charger,
                laptops=laptops, lcharger=lcharger)


@pytest.mark.django_db
def test_the_tier_is_curated_on_the_node_not_parsed_from_a_path(taxonomy):
    """iBay spells 'Accessories' in its path; another source will not."""
    assert taxonomy["charger"].tier == "accessory"


@pytest.mark.django_db
def test_every_node_resolves_to_its_family(taxonomy):
    assert family_of(taxonomy["charger"]) == taxonomy["family"]
    assert family_of(taxonomy["family"]) == taxonomy["family"]


@pytest.mark.django_db
def test_an_accessory_resolves_to_the_primary_sibling(taxonomy):
    assert primary_sibling_of(taxonomy["charger"]) == taxonomy["primary"]


@pytest.mark.django_db
def test_a_family_with_no_primary_child_returns_none(taxonomy):
    assert primary_sibling_of(taxonomy["lcharger"]) is None


@pytest.mark.django_db
def test_the_map_is_keyed_on_the_full_path_not_the_leaf(taxonomy):
    """The measured defect: two different 'Charger' leaves must not merge."""
    phone_path = ["For Sale", "Mobile Phones & Accessories", "Accessories",
                  "Charger"]
    laptop_path = ["For Sale", "Computer, Tablets & Networking",
                   "Laptop Accessories", "Charger"]
    SourceCategoryMap.objects.create(
        source="ibay", path=phone_path,
        path_key=path_key("ibay", phone_path), category=taxonomy["charger"])
    SourceCategoryMap.objects.create(
        source="ibay", path=laptop_path,
        path_key=path_key("ibay", laptop_path), category=taxonomy["lcharger"])

    assert map_path("ibay", phone_path) == taxonomy["charger"]
    assert map_path("ibay", laptop_path) == taxonomy["lcharger"]


@pytest.mark.django_db
def test_an_unmapped_path_maps_to_none(taxonomy):
    assert map_path("ibay", ["For Sale", "Nothing Like This"]) is None


@pytest.mark.django_db
def test_a_mapped_row_with_no_category_is_legal(taxonomy):
    """'No canonical category for this path' is a decision, not an error."""
    path = ["Services", "Other Services"]
    SourceCategoryMap.objects.create(source="ibay", path=path,
                                     path_key=path_key("ibay", path),
                                     category=None, note="deliberately unmapped")
    assert map_path("ibay", path) is None


@pytest.mark.django_db
def test_path_key_is_order_sensitive_and_stable():
    a = path_key("ibay", ["For Sale", "Games"])
    assert a == path_key("ibay", ["For Sale", "Games"])
    assert a != path_key("ibay", ["Games", "For Sale"])
    assert a != path_key("gazette", ["For Sale", "Games"])
    assert len(a) == 64
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/search/test_taxonomy.py -v`
Expected: FAIL, `ImportError: cannot import name 'Category' from 'search.models'`.

- [ ] **Step 3: Add the models**

In `search/models.py`:

```python
class Category(models.Model):
    """The canonical taxonomy. Source-independent by design.

    iBay happens to publish a hierarchy with `Accessories` and `Parts` as
    literal path segments; gazette publishes none, and a future source may
    publish flat or wrong tags. Sources map INTO this tree (SourceCategoryMap)
    and query parsing reads only this.

    `tier` is curated per node rather than parsed from a path segment, because
    the segment exists in exactly one source's paths.
    """

    TIERS = [("family", "family"), ("primary", "primary product"),
             ("accessory", "accessory"), ("part", "part"),
             ("service", "service")]

    key = models.SlugField(max_length=64, unique=True)
    label_en = models.CharField(max_length=128)
    label_dv = models.CharField(max_length=128, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.PROTECT, related_name="children")
    tier = models.CharField(max_length=16, choices=TIERS)
    doc_type = models.CharField(max_length=32, blank=True)
    # Query words that should select this node but are absent from its label.
    # Measured in P10: 440 titles say "glass" and no label contains it.
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["key"]

    def __str__(self):
        return self.key


class SourceCategoryMap(models.Model):
    """One row per distinct source category path.

    Keyed on the full path, not the leaf: iBay spells `Charger` under two
    different families and `Car Accessories` under two more, so a leaf-keyed
    map merges categories that rank and facet differently.

    `category = NULL` is a legal, reviewed decision meaning "no canonical
    category for this path", and is not the same as an absent row, which means
    "not yet reviewed".
    """

    source = models.CharField(max_length=32)
    path = models.JSONField(default=list)
    path_key = models.CharField(max_length=64)
    category = models.ForeignKey(Category, null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name="source_paths")
    note = models.CharField(max_length=256, blank=True)
    document_count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "path_key"],
                                    name="uniq_sourcecategory_path")
        ]
        indexes = [models.Index(fields=["source", "-document_count"],
                                name="sourcecat_source_count")]

    def __str__(self):
        return f"{self.source}: {' > '.join(self.path)}"
```

- [ ] **Step 4: Write `search/taxonomy.py`**

```python
"""Canonical taxonomy helpers. Spec section 5.

Nothing here reads a source path except through SourceCategoryMap. That is the
whole point of the module: one place decides what a source's category means.
"""

from __future__ import annotations

import hashlib

from django.db.models.signals import post_delete, post_save

from search.models import Category, SourceCategoryMap

TIERS = ("family", "primary", "accessory", "part", "service")

# (source, path_key) -> Category | None.
#
# `map_path` is called once per document from search/indexing.py::_row, and the
# question it asks has only ~306 distinct answers, so the uncached version issues
# one query per row: 20,445 on today's corpus and 5M at the size spec 12 projects.
# Same lifetime reasoning as _OVERLAY_CACHE in indexing.py, except this one is
# invalidated by signals, because a taxonomy edit in the admin must be visible to
# the web process without a restart and the tests build their taxonomy row by row.
_CACHE: dict[tuple[str, str], Category | None] = {}


def clear_cache(*args, **kwargs) -> None:
    """Drop the resolution cache. Connected to Category and SourceCategoryMap
    saves and deletes below; also safe to call directly."""
    _CACHE.clear()


post_save.connect(clear_cache, sender=Category,
                  dispatch_uid="taxonomy_cache_category_save")
post_delete.connect(clear_cache, sender=Category,
                    dispatch_uid="taxonomy_cache_category_delete")
post_save.connect(clear_cache, sender=SourceCategoryMap,
                  dispatch_uid="taxonomy_cache_map_save")
post_delete.connect(clear_cache, sender=SourceCategoryMap,
                    dispatch_uid="taxonomy_cache_map_delete")


def path_key(source: str, path: list[str]) -> str:
    """Stable, order-sensitive, source-scoped key for one category path."""
    joined = "\x1f".join([source, *[str(p).strip() for p in path]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def map_path(source: str, path: list[str]) -> Category | None:
    """The canonical node for a source path, or None.

    None covers three different situations on purpose -- no row, a reviewed row
    with no category, and an inactive category -- because every one of them
    means the same thing downstream: this document has no canonical category.

    A miss is cached as None too. An unmapped path is asked about once per
    document just like a mapped one, and there are only so many of them.
    """
    if not path:
        return None

    cache_key = (source, path_key(source, path))
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    row = (SourceCategoryMap.objects
           .filter(source=source, path_key=cache_key[1])
           .select_related("category")
           .first())
    if row is None or row.category is None or not row.category.is_active:
        _CACHE[cache_key] = None
    else:
        _CACHE[cache_key] = row.category
    return _CACHE[cache_key]


def family_of(category: Category) -> Category:
    node = category
    while node.parent_id is not None and node.tier != "family":
        node = node.parent
    return node


def primary_sibling_of(category: Category) -> Category | None:
    """'iphone' with no modifier wants this; 'iphone charger' wants the
    accessory. One relationship serves both (P10 task 3)."""
    family = family_of(category)
    return family.children.filter(tier="primary", is_active=True).first()
```

- [ ] **Step 5: Generate the migration and run the tests**

Run:
```bash
python manage.py makemigrations search --name category_sourcecategorymap
pytest tests/search/test_taxonomy.py -v
```
Expected: PASS, 8 tests.

- [ ] **Step 6: Write `seed_taxonomy`**

`search/management/commands/seed_taxonomy.py`:

```python
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
      iBay names families after their contents, so `Mobile Phones & Accessories`
      ends in "Accessories" while its `Mobile Phones` child (507 documents) is
      the most important primary node in the corpus. Including path[1] classifies
      it as an accessory and breaks the one thing tier exists to decide.
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

    Distinct KEYS are not sufficient, and this is the trap. `category_leaf` is a
    bare string, and both P9's category-aware ranking and spec 8.3's facet
    priority override group on it -- so two nodes with different keys and the
    same label still share one bucket. Measured on the live corpus: 14 labels
    were shared by two nodes, the worst being `Charger`, with 400 phone chargers
    and 13 laptop chargers landing together, which is the exact defect this
    taxonomy exists to fix.

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
        parser.add_argument("--source", default="ibay")
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
```

- [ ] **Step 7: Register both models in the admin**

In `search/admin.py`:

```python
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "label_dv", "tier", "parent",
                    "doc_type", "is_active")
    list_editable = ("tier", "doc_type", "is_active")
    list_filter = ("tier", "is_active")
    search_fields = ("key", "label_en", "label_dv")
    ordering = ("key",)


@admin.register(SourceCategoryMap)
class SourceCategoryMapAdmin(admin.ModelAdmin):
    """Sorted by document_count so the paths that matter are reviewed first."""

    list_display = ("__str__", "source", "category", "document_count", "note")
    list_editable = ("category", "note")
    list_filter = ("source", ("category", admin.EmptyFieldListFilter))
    search_fields = ("path",)
    ordering = ("-document_count",)
```

- [ ] **Step 7b: Pin `infer_tier` with tests**

The heuristic above is the part of this task most likely to be quietly wrong,
and the eight model tests do not touch it. Append to
`tests/search/test_taxonomy.py` a parametrized `test_infer_tier` covering, at
minimum, these cases, which are the ones two earlier versions of the rule got
wrong:

| path | expected | why it is a trap |
|---|---|---|
| `For Sale > Mobile Phones & Accessories > Mobile Phones` | `primary` | family named after its contents |
| `... > Accessories > Charger` | `accessory` | exact segment |
| `... > Parts > Battery` | `part` | matches both rules; Parts must win |
| `... > Laptop Accessories > Charger` | `accessory` | mid-level segment only ends with the word |
| `For Sale > Clothing & Accessories > Watches` | `primary` | family again |
| `For Sale > Home & Garden > Aircon Servicing & Repair` | `service` | service by leaf name, not by root |

Plus a `JUNK_LEAF` test asserting it matches `General / Other`, `Other` and
`Other Stuff` but not `Mobile Phones`, `Charger` or `Other Accessories`.

- [ ] **Step 8: Seed against the real corpus and review**

Run:
```bash
python manage.py seed_taxonomy --source ibay        # dry run, read the output
python manage.py seed_taxonomy --source ibay --apply
```
Measured on 2026-08-19: **306 distinct paths, 20,257 documents**, and the tier
split comes out `primary 163, service 70, accessory 42, part 25, family 6`. If
`primary` lands near 192 instead, `infer_tier` is the exact-match version and 30
accessory nodes are claiming to be products.

Confirm the two `Charger` paths resolve to two different keys
(`accessories-charger` and `laptop-accessories-charger`):

```bash
python manage.py shell -c "
from search.models import SourceCategoryMap as M
rows = M.objects.filter(path__contains=['Charger'])
for r in rows: print(r.category.key, '<-', ' > '.join(r.path))"
```

- [ ] **Step 9: Commit**

```bash
jj commit -m "catalog task 1: canonical taxonomy and path-keyed source mapping"
```

---

## Task 2: Documents into the taxonomy

**Files:**
- Modify: `search/models.py`, `search/indexing.py`
- Create: `search/management/commands/map_categories.py`, `search/migrations/0012_searchdocument_category_contact_phone.py`
- Test: `tests/search/test_category_mapping.py`

**Interfaces:**
- Consumes: `map_path`, `Category`, `SourceCategoryMap` from task 1.
- Produces:
  ```python
  SearchDocument.category        # FK, null=True, db_constraint=False
  SearchDocument.contact_phone   # CharField(16), indexed  (filled in task 3)
  # search/indexing.py::_row now sets category_id and derives category_leaf
  # from the canonical label when the path is mapped.
  ```

Both columns are added in one migration because they touch the same table and a
partitioned table's ALTER is the expensive part, not the column count.

- [ ] **Step 1: Write the failing test**

`tests/search/test_category_mapping.py`:

```python
import pytest

from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import Category, SearchDocument, SourceCategoryMap
from search.taxonomy import path_key


def draft(path, **kw):
    return DocumentDraft(
        source="ibay", source_key=kw.pop("source_key", "1"), doc_type="shopping",
        url="https://ibay.com.mv/x", title_en=kw.pop("title", "A charger"),
        attrs={"category_path": path}, **kw)


@pytest.fixture
def mapped(db):
    family = Category.objects.create(key="mobile", label_en="Mobile Phones & Accessories",
                                     tier="family")
    node = Category.objects.create(key="accessories-charger", label_en="Phone Charger",
                                   parent=family, tier="accessory")
    path = ["For Sale", "Mobile Phones & Accessories", "Accessories", "Charger"]
    SourceCategoryMap.objects.create(source="ibay", path=path,
                                     path_key=path_key("ibay", path), category=node)
    return node, path


@pytest.mark.django_db
def test_a_mapped_document_gets_the_canonical_category(mapped):
    node, path = mapped
    upsert_drafts([draft(path)])
    doc = SearchDocument.objects.get(source="ibay", source_key="1")
    assert doc.category_id == node.id


@pytest.mark.django_db
def test_category_leaf_comes_from_the_canonical_label_not_the_source_path(mapped):
    """The source leaf is 'Charger'; the canonical label is 'Phone Charger'.
    Ranking and facet priority key on category_leaf, so it must be the
    unambiguous one."""
    node, path = mapped
    upsert_drafts([draft(path)])
    doc = SearchDocument.objects.get(source="ibay", source_key="1")
    assert doc.category_leaf == "Phone Charger"


@pytest.mark.django_db
def test_an_unmapped_path_keeps_the_raw_leaf_and_no_category(mapped):
    """Nothing regresses for a path nobody has reviewed yet."""
    upsert_drafts([draft(["For Sale", "Unreviewed Thing"], source_key="2")])
    doc = SearchDocument.objects.get(source="ibay", source_key="2")
    assert doc.category_id is None
    assert doc.category_leaf == "Unreviewed Thing"


@pytest.mark.django_db
def test_a_document_with_no_path_is_valid(mapped):
    upsert_drafts([draft([], source_key="3")])
    doc = SearchDocument.objects.get(source="ibay", source_key="3")
    assert doc.category_id is None
    assert doc.category_leaf == ""


@pytest.mark.django_db
def test_map_categories_updates_in_place_without_a_reindex(mapped):
    """188 documents have no path and 278 paths get reviewed over time; a
    taxonomy edit must not require re-running the paid pipeline."""
    from django.core.management import call_command

    node, path = mapped
    upsert_drafts([draft(path, source_key="4")])
    SearchDocument.objects.filter(source_key="4").update(category=None,
                                                         category_leaf="Charger")
    call_command("map_categories", "--source", "ibay")
    doc = SearchDocument.objects.get(source="ibay", source_key="4")
    assert doc.category_id == node.id
    assert doc.category_leaf == "Phone Charger"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/search/test_category_mapping.py -v`
Expected: FAIL, `SearchDocument() got unexpected keyword arguments: 'category'`
or a missing-column error.

- [ ] **Step 3: Add both columns**

In `search/models.py`, on `SearchDocument`:

```python
    # Partitioned table, so no FK constraint (spec 12.2). Null is normal: most
    # gazette documents have no category, and search must not require one.
    category = models.ForeignKey("search.Category", null=True, blank=True,
                                 on_delete=models.SET_NULL, db_constraint=False,
                                 related_name="documents")
    # The primary advertiser phone, extracted deterministically (task 3). A
    # column rather than a card key because one number covers 1,680 listings,
    # so "same advertiser" has to be groupable in SQL.
    contact_phone = models.CharField(max_length=16, blank=True, db_index=True)
```

- [ ] **Step 4: Resolve the category in `_row`**

In `search/indexing.py::_row`, replace the two `category_leaf` lines with:

```python
    # The canonical taxonomy decides the bucket, not the source's own path.
    # Measured: 9 iBay leaf labels are ambiguous across families, and both P9's
    # category-aware ranking and spec 8.3's facet override key on category_leaf.
    from search.taxonomy import map_path
    path = draft.attrs.get("category_path") or []
    category = map_path(draft.source, [str(p) for p in path])
    category_leaf = category.label_en if category else (str(path[-1]) if path else "")
```

then add `category=category` to the `SearchDocument(...)` construction, and add
`"category"` and `"contact_phone"` to `_UPDATE_FIELDS`.

- [ ] **Step 5: Write `map_categories`**

`search/management/commands/map_categories.py`:

```python
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
```

- [ ] **Step 6: Migrate and run the tests**

Run:
```bash
python manage.py makemigrations search --name searchdocument_category_contact_phone
python manage.py migrate
pytest tests/search/test_category_mapping.py tests/search/test_taxonomy.py -v
```
Expected: PASS.

- [ ] **Step 6b: Pin the resolution cache and its invalidation**

`map_path` runs once per document in `_row`. Append to
`tests/search/test_category_mapping.py` tests asserting that:

- 50 repeat lookups of one path issue **zero** queries after the first
  (`CaptureQueriesContext`), and an unmapped path is cached as `None` the same way
- saving a `Category` makes the new `label_en` visible immediately
- deactivating a `Category` makes `map_path` return `None` immediately
- repointing a `SourceCategoryMap` row returns the new node immediately

The last three are the ones that matter. A cache without them turns every admin
click into a change that only takes effect after a restart, which is worse than
the queries it saves.

- [ ] **Step 7: Run against the real corpus and record coverage**

Run:
```bash
python manage.py map_categories --source ibay
```
Expected: about 20,257 updated and about 188 still unmapped (the empty-path
documents). Record both numbers; the unmapped count is the input to task 6's
classification fallback.

- [ ] **Step 8: Commit**

```bash
jj commit -m "catalog task 2: documents resolve to canonical categories in place"
```

---

## Task 3: Deterministic phone extraction

**Files:**
- Create: `search/contacts.py`, `search/management/commands/backfill_phones.py`
- Modify: `search/indexing.py`, `enrich/preextract.py`
- Test: `tests/search/test_contacts.py`

**Interfaces:**
- Consumes: `SearchDocument.contact_phone` from task 2.
- Produces:
  ```python
  PHONE_RE                                   # the single Maldivian phone regex
  primary_phone(*texts: str) -> str          # "" when none
  strip_phones(text: str) -> str
  # _row sets contact_phone and card["phone"], and strips phones from
  # card["title"] only.
  ```

`enrich/preextract.py` stops defining its own pattern and imports `PHONE_RE`.
The import direction is already established: `preextract` imports
`search.extract.dates.parse_dv_month`.

- [ ] **Step 1: Write the failing test**

`tests/search/test_contacts.py`:

```python
import pytest

from search.contacts import PHONE_RE, primary_phone, strip_phones


def test_the_title_wins_over_the_description():
    """89.3% of listings carry a phone, usually in both places, and the title
    number is the one the seller wants called."""
    assert primary_phone("Fridge repair 7438649", "call 9663178") == "7438649"


def test_falls_back_to_the_description():
    assert primary_phone("Fridge repair", "call 9663178") == "9663178"


def test_no_phone_is_empty_string_not_none():
    assert primary_phone("Fridge repair", "") == ""


@pytest.mark.parametrize("text,expected", [
    ("Call 7438649", "7438649"),
    ("Tel: 7989696", "7989696"),
    ("+960 7438649", "7438649"),
    ("+9607438649", "7438649"),
    ("9445252 , 9519132 , 9654041", "9445252"),
    ("landline 3325555", "3325555"),
    ("6621234", "6621234"),
])
def test_real_corpus_forms(text, expected):
    assert primary_phone(text) == expected


@pytest.mark.parametrize("text", [
    "Model RL-S07100C",          # not a phone
    "12345678",                  # eight digits
    "5551234",                   # leading 5 is not a Maldivian prefix
    "Price 1450",
])
def test_things_that_are_not_phones(text):
    assert primary_phone(text) == ""


def test_a_longer_digit_run_is_not_a_phone():
    """The '445' tail of a longer run must not match."""
    assert primary_phone("serial 79386491234") == ""


def test_strip_phones_leaves_a_readable_title():
    title = "Refrigerator, Aircon Repair & Service. Home Service Call.. 7438649"
    out = strip_phones(title)
    assert "7438649" not in out
    assert out == "Refrigerator, Aircon Repair & Service. Home Service Call.."


def test_strip_phones_collapses_the_separator_it_leaves_behind():
    """The trailing label punctuation goes too: 'Tel:' with nothing after it
    reads as broken markup, not as a label."""
    assert strip_phones("Green Lion Charger | Tel: 7989696") == \
        "Green Lion Charger | Tel"


def test_preextract_uses_the_same_pattern():
    """One regex, or the entity provider key and the candidate list disagree."""
    from enrich.preextract import _PHONE
    assert _PHONE is PHONE_RE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/search/test_contacts.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'search.contacts'`.

- [ ] **Step 3: Write `search/contacts.py`**

```python
"""Deterministic contact extraction. Spec section 12.

Maldivian numbers are seven digits: mobile starts 7 or 9, landline 3 or 6. The
+960 prefix is optional and the number is frequently welded to the end of a
title with no separator, hence explicit boundary guards rather than \\b, which
would happily match the tail of a longer run of digits.

This module owns the pattern. `enrich/preextract.py` imports it so the candidate
list the model sees and the provider key the entity layer computes can never
disagree about what a phone number is.
"""

from __future__ import annotations

import re

PHONE_RE = re.compile(
    r"(?<![\d])(?:\+?960[\s\-]?)?([79]\d{6}|[36]\d{6})(?![\d])")

_LEFTOVER_SPACE = re.compile(r"[ \t]{2,}")


def primary_phone(*texts: str) -> str:
    """The number to call, or "".

    Ordered: the caller passes title before description, because a seller who
    puts one number in the title and another in the body means the first.
    """
    for text in texts:
        if not text:
            continue
        m = PHONE_RE.search(text)
        if m:
            return m.group(1)
    return ""


def all_phones(*texts: str) -> list[str]:
    out: list[str] = []
    for text in texts:
        for m in PHONE_RE.finditer(text or ""):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def strip_phones(text: str) -> str:
    """Remove phone numbers from a string meant for display.

    Display only. The number stays in title_en and therefore in the search
    vector, because searching a corpus where one advertiser holds 1,680 listings
    by their phone number is a legitimate query.
    """
    out = PHONE_RE.sub("", text or "")
    out = _LEFTOVER_SPACE.sub(" ", out)
    # '&' is in the strip set because sellers write 'FREE DELIVERY | &7776828',
    # which otherwise renders as a title ending in '| &'. '.' is deliberately
    # NOT: 'Aircon Repair & Service.' ends in a sentence, not in debris.
    return out.strip(" \t-,|/:&").strip()
```

- [ ] **Step 4: Point `preextract` at the shared pattern**

In `enrich/preextract.py`, replace the `_PHONE = re.compile(...)` definition and
its comment with:

```python
from search.contacts import PHONE_RE

# One definition, shared with the entity layer's provider key (spec section 7.2).
_PHONE = PHONE_RE
```

- [ ] **Step 5: Fill the column and the card in `_row`**

In `search/indexing.py::_row`, after the `card` dict is copied:

```python
    # Deterministic, every source, no model call. Measured 89.3% coverage.
    from search.contacts import primary_phone, strip_phones
    phone = primary_phone(draft.title_en, draft.title_dv,
                          draft.summary_en, draft.summary_dv, draft.text_en)
    if phone:
        card["phone"] = phone
        # Display only: title_en keeps the number so the vector keeps it too.
        if card.get("title"):
            card["title"] = strip_phones(card["title"])
```

and pass `contact_phone=phone` to the `SearchDocument(...)` construction.

- [ ] **Step 6: Write `backfill_phones`**

`search/management/commands/backfill_phones.py`:

```python
"""Fill contact_phone and card['phone'] without a reindex.

Same reasoning as map_categories: this is free and reindex is not.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from search.contacts import primary_phone, strip_phones
from search.models import SearchDocument


class Command(BaseCommand):
    help = "Backfill contact_phone and card['phone'] from existing text."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
        if opts["source"]:
            qs = qs.filter(source=opts["source"])

        found = 0
        batch: list[SearchDocument] = []
        for doc in qs.only("id", "title_en", "title_dv", "summary_en",
                           "summary_dv", "card", "contact_phone").iterator(
                               chunk_size=500):
            phone = primary_phone(doc.title_en, doc.title_dv,
                                  doc.summary_en, doc.summary_dv)
            if not phone:
                continue
            found += 1
            doc.contact_phone = phone
            card = dict(doc.card or {})
            card["phone"] = phone
            if card.get("title"):
                card["title"] = strip_phones(card["title"])
            doc.card = card
            batch.append(doc)
            if len(batch) >= 500 and not opts["dry_run"]:
                SearchDocument.objects.bulk_update(
                    batch, ["contact_phone", "card"])
                batch.clear()

        if batch and not opts["dry_run"]:
            SearchDocument.objects.bulk_update(batch, ["contact_phone", "card"])

        self.stdout.write(self.style.SUCCESS(f"{found} documents with a phone"))
```

- [ ] **Step 7: Run the tests, including the existing enrichment suite**

Run:
```bash
pytest tests/search/test_contacts.py tests/enrich/test_preextract.py -v
```
Expected: PASS. The preextract suite must stay green - it is the proof that
sharing the pattern changed no behaviour.

- [ ] **Step 8: Backfill and check the measured figure**

Run:
```bash
python manage.py backfill_phones --source ibay
```
Measured 2026-08-19, and note the command covers EVERY doc_type while the
spec's 89.3% figure was shopping only:

| doc_type | with a phone | total | share |
|---|---|---|---|
| shopping | 14,848 | 16,608 | 89.4% |
| property | 2,967 | 3,497 | 84.8% |
| job | 120 | 335 | 35.8% |
| **all ibay** | **17,938** | **20,445** | **87.7%** |

1,601 distinct numbers, and the top advertiser (`7438649`) holds 1,680 listings.
A materially lower shopping share means the pattern or the field order
regressed. Jobs are low because an employer ad carries an email, not a mobile.

- [ ] **Step 9: Commit**

```bash
jj commit -m "catalog task 3: deterministic phone extraction, one shared pattern"
```

---

## Task 4: Identity extraction

**Files:**
- Create: `catalog/__init__.py`, `catalog/apps.py`, `catalog/models.py`, `catalog/identity.py`, `catalog/migrations/__init__.py`, `catalog/management/commands/seed_brands.py`
- Modify: `beynunehcheh/settings.py` (INSTALLED_APPS)
- Test: `tests/catalog/__init__.py`, `tests/catalog/test_identity.py`

**Interfaces:**
- Consumes: `strip_phones`, `all_phones` from task 3; `map_path` from task 1.
- Produces:
  ```python
  Brand                                       # name, aliases, is_active
  clean_title(text: str) -> str
  model_tokens(text: str) -> list[str]
  match_brand(text: str, vocabulary: dict[str, str]) -> str
  brand_vocabulary() -> dict[str, str]        # lowercased alias -> canonical
  product_key(brand: str, tokens: list[str], category_key: str) -> str
  service_key(provider_key: str, service_type: str) -> str
  ```

`Brand` ships in `catalog/models.py` together with the four entity tables in
task 5, in one migration. Only `Brand` is used here.

- [ ] **Step 1: Write the failing test**

`tests/catalog/test_identity.py`:

```python
import pytest

from catalog.identity import (brand_vocabulary, clean_title, match_brand,
                              model_tokens, product_key, service_key)
from catalog.models import Brand


def test_clean_title_strips_the_phone_and_the_marketing():
    """A real corpus title. Everything after the product is noise."""
    raw = "ROSY Light 100W LED Flood Light RL-S07100C | NEW FREE DELIVERY:9445252"
    assert clean_title(raw) == "ROSY Light 100W LED Flood Light RL-S07100C"


def test_clean_title_handles_the_call_suffix_form():
    raw = "Electricain Room Light Board installation. Repair & Services Call. 7438649"
    out = clean_title(raw)
    assert "7438649" not in out
    assert "Call" not in out
    assert out.startswith("Electricain Room Light Board installation")


def test_clean_title_keeps_a_model_number_that_looks_like_noise():
    """RL-S07100C must survive; it is the whole identity."""
    assert "RL-S07100C" in clean_title("ROSY RL-S07100C free delivery 9445252")


def test_model_tokens_require_a_digit():
    tokens = model_tokens("Green Lion 200W PD Multi Ports 10 Charging Station")
    assert "200W" in tokens
    assert "Station" not in tokens


def test_model_tokens_keep_hyphenated_part_numbers():
    assert "RL-S07100C" in model_tokens("ROSY Light RL-S07100C")


def test_model_tokens_are_sorted_and_deduplicated():
    """The key must not depend on word order, or a reposted listing with the
    words rearranged becomes a second entity."""
    assert model_tokens("A15 128GB A15") == model_tokens("128GB A15")


def test_model_tokens_drop_a_bare_year():
    assert model_tokens("Model year 2019 aircon") == []


@pytest.mark.django_db
def test_match_brand_uses_the_vocabulary_not_the_first_token():
    Brand.objects.create(name="Samsung")
    Brand.objects.create(name="Green Lion", aliases=["greenlion"])
    vocab = brand_vocabulary()
    assert match_brand("Brand New Samsung Galaxy A15", vocab) == "Samsung"
    assert match_brand("GreenLion 200W charger", vocab) == "Green Lion"


@pytest.mark.django_db
def test_match_brand_prefers_the_longest_alias():
    Brand.objects.create(name="Lion")
    Brand.objects.create(name="Green Lion")
    vocab = brand_vocabulary()
    assert match_brand("Green Lion charger", vocab) == "Green Lion"


@pytest.mark.django_db
def test_an_unknown_brand_is_empty_not_the_first_word():
    """The prototype's 0% miss rate came from first-token-as-brand. An honest
    miss is required here, because a wrong brand makes a wrong entity."""
    Brand.objects.create(name="Samsung")
    assert match_brand("Excellent condition thing for sale", brand_vocabulary()) == ""


def test_product_key_is_stable_and_order_independent():
    a = product_key("Samsung", ["A15", "128GB"], "mobile-phones")
    b = product_key("samsung", ["128GB", "A15"], "mobile-phones")
    assert a == b
    assert len(a) == 64


def test_product_key_separates_the_same_model_in_different_categories():
    assert product_key("Samsung", ["A15"], "mobile-phones") != \
        product_key("Samsung", ["A15"], "phone-cases")


def test_product_key_tolerates_an_unmapped_category():
    """An unmapped path contributes the empty string, never the classified
    category: the key must not depend on a model call."""
    assert product_key("Samsung", ["A15"], "") != ""


def test_service_key_is_provider_scoped():
    assert service_key("7438649", "electrical-wiring") != \
        service_key("9663178", "electrical-wiring")
    assert service_key("7438649", "electrical-wiring") == \
        service_key("7438649", "electrical-wiring")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/catalog/test_identity.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'catalog'`.

- [ ] **Step 3: Create the app and register it**

```bash
python manage.py startapp catalog
mkdir -p tests/catalog && touch tests/catalog/__init__.py
```

`catalog/apps.py`:

```python
from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
```

In `beynunehcheh/settings.py`, add `'catalog',` to `INSTALLED_APPS` after
`'enrich',`. Add `catalog` to `testpaths` in `pytest.ini`.

- [ ] **Step 4: Add the `Brand` model**

In `catalog/models.py`:

```python
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
```

- [ ] **Step 5: Write `catalog/identity.py`**

```python
"""Deterministic identity extraction. Spec section 7.

No model call anywhere in this module. Everything here has to be reproducible,
because the entity key is computed from it and a key that moves between runs
splits one entity into several on every pass.
"""

from __future__ import annotations

import hashlib
import re

from search.contacts import strip_phones

# Marketing vocabulary measured in the corpus. These words appear in titles as
# selling copy, never as identity, so they are removed before tokenizing.
_MARKETING = re.compile(
    r"\b(free|delivery|delivary|call|whatsapp|viber|tel|telephone|contact|"
    r"sms|order|now|available|stock|instock|best|price|offer|sale|discount|"
    r"cheap|new|brand\s+new|used|original|genuine|quality|shop|visit|cash|"
    r"bml|transfer|urgent|limited|hot|deal)\b", re.I)
_SEPARATORS = re.compile(r"[|:;,\.\(\)\[\]♦♥*#]+")
_WS = re.compile(r"\s+")

# A model token carries a digit: RL-S07100C, A15, 128GB, 200W.
_MODEL_TOKEN = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-/\.]{1,23}$")
_BARE_YEAR = re.compile(r"^20\d{2}$")


def clean_title(text: str) -> str:
    out = strip_phones(text or "")
    out = _MARKETING.sub(" ", out)
    out = _SEPARATORS.sub(" ", out)
    return _WS.sub(" ", out).strip(" -_")


def model_tokens(text: str, limit: int = 4) -> list[str]:
    """Sorted, uppercased, deduplicated. Sorted because a reposted listing with
    the words rearranged must land on the same entity."""
    seen: set[str] = set()
    for word in clean_title(text).split():
        token = word.strip("-/.").upper()
        if not _MODEL_TOKEN.match(token) or _BARE_YEAR.match(token):
            continue
        if token.isdigit():          # a bare quantity is not a model
            continue
        seen.add(token)
    return sorted(seen)[:limit]


def brand_vocabulary() -> dict[str, str]:
    """Lowercased alias -> canonical brand name."""
    from catalog.models import Brand

    vocab: dict[str, str] = {}
    for brand in Brand.objects.filter(is_active=True).only("name", "aliases"):
        vocab[brand.name.lower()] = brand.name
        for alias in brand.aliases or []:
            vocab[str(alias).lower()] = brand.name
    return vocab


def match_brand(text: str, vocabulary: dict[str, str]) -> str:
    """Longest alias wins, so 'Green Lion' beats 'Lion'. Empty when unknown:
    an honest miss, never a guess from the first token."""
    haystack = f" {clean_title(text).lower()} "
    best = ""
    for alias in vocabulary:
        if f" {alias} " in haystack and len(alias) > len(best):
            best = alias
    return vocabulary[best] if best else ""


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def product_key(brand: str, tokens: list[str], category_key: str) -> str:
    """category_key is the MAPPED canonical key only, empty when unmapped.
    Never the classified one -- that arrives from a model call, and a key that
    depends on a model call is not reproducible (spec section 7.1)."""
    return _key("product", (brand or "").strip().lower(),
                "|".join(sorted(t.upper() for t in tokens)),
                (category_key or "").strip().lower())


def service_key(provider_key: str, service_type: str) -> str:
    return _key("service", (provider_key or "").strip().lower(),
                (service_type or "").strip().lower())
```

- [ ] **Step 6: Write `seed_brands`**

`catalog/management/commands/seed_brands.py`:

```python
"""Seed Brand from the brand values already in DocumentSpec.

P7's scraped-ProductInfo projection already holds 35 distinct brands on 2,313
listings. Paying a model to re-derive a vocabulary the source gave us would be
the mistake spec 4.4 was written to avoid.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Brand
from search.models import DocumentSpec


class Command(BaseCommand):
    help = "Seed the Brand vocabulary from DocumentSpec brand values."

    def handle(self, *args, **opts):
        rows = (DocumentSpec.objects.filter(key_raw="brand")
                .exclude(value_text="")
                .values("value_text")
                .annotate(n=Count("id")).order_by("-n"))
        created = 0
        for row in rows:
            name = row["value_text"].strip()
            if not name or len(name) > 64:
                continue
            # 'Apple (iPhone)' and 'Apple' are the same brand; the parenthetical
            # becomes an alias rather than a second brand.
            base = name.split("(")[0].strip() or name
            brand, was_created = Brand.objects.get_or_create(name=base)
            created += int(was_created)
            if base != name and name not in (brand.aliases or []):
                brand.aliases = [*(brand.aliases or []), name]
                brand.save(update_fields=["aliases"])
            self.stdout.write(f"{row['n']:5d}  {base}")
        self.stdout.write(self.style.SUCCESS(
            f"{created} brands created, {Brand.objects.count()} total"))
```

- [ ] **Step 7: Run the tests**

Run: `pytest tests/catalog/test_identity.py -v`
Expected: PASS, 13 tests. The migration for `Brand` is generated in task 5 with
the rest of the models; until then these tests run against
`pytest --create-db` state built from the model definitions.

Actually generate it now so the suite is runnable standalone:

```bash
python manage.py makemigrations catalog --name initial
python manage.py migrate
pytest tests/catalog/test_identity.py -v
```

- [ ] **Step 8: Seed brands and read the miss rate**

Run:
```bash
python manage.py seed_brands
python manage.py shell -c "
from django.conf import settings
from catalog.identity import brand_vocabulary, match_brand, model_tokens
from search.models import SearchDocument
vocab = brand_vocabulary()
qs = SearchDocument.objects.using(settings.STREAM_DB_ALIAS).filter(
    source='ibay', doc_type='shopping')
miss = total = 0
for d in qs.only('title_en','attrs').iterator(chunk_size=500):
    if (d.attrs.get('category_path') or [''])[0] != 'For Sale':
        continue
    total += 1
    if not match_brand(d.title_en, vocab) and not model_tokens(d.title_en):
        miss += 1
print(f'{miss}/{total} = {100*miss/total:.1f}% no usable identity')"
```
Expected: a miss rate in the 15-30% band the spec predicted. If it exceeds 35%,
stop and grow the brand vocabulary before task 5 - resolution quality is bounded
by this number and no later task can recover it.

- [ ] **Step 9: Commit**

```bash
jj commit -m "catalog task 4: deterministic identity extraction and brand vocabulary"
```

---

## Task 5: Entities, links and resolution

**Files:**
- Modify: `catalog/models.py`, `catalog/admin.py`
- Create: `catalog/resolve.py`, `catalog/management/commands/resolve_entities.py`, `catalog/management/commands/eval_entities.py`, `catalog/eval/golden.yaml`
- Test: `tests/catalog/test_resolve.py`

**Interfaces:**
- Consumes: everything from task 4; `map_path` from task 1; `contact_phone` from task 3.
- Produces:
  ```python
  Entity          # kind, key, brand, model_name, variant, service_type,
                  # provider_key, category, title_en/dv, summary_en/dv,
                  # identity_confidence, profile_status, listing_count
  EntityLink      # entity, source, source_key, method, confidence
  EntityField     # entity, key_raw, key, value_num, value_text, unit,
                  # provenance, confidence, support_count, is_winner
  FieldProposal   # entity, key_raw, value_*, proposer_ip_hash, status
  PROVENANCE = ("scraped", "correction", "consensus", "grounded", "inferred")
  resolve_document(doc: SearchDocument) -> Entity | None
  resolve_source(source: str, *, limit=None, dry_run=False) -> dict
  ```

- [ ] **Step 1: Write the failing test**

`tests/catalog/test_resolve.py`:

```python
import pytest

from catalog.models import Brand, Entity, EntityLink
from catalog.resolve import resolve_document, resolve_source
from search.models import Category, SearchDocument, SourceCategoryMap
from search.taxonomy import path_key


@pytest.fixture
def fixtures(db):
    Brand.objects.create(name="Samsung")
    family = Category.objects.create(key="mobile", label_en="Mobile", tier="family")
    phones = Category.objects.create(key="mobile-phones", label_en="Mobile Phones",
                                     parent=family, tier="primary")
    wiring = Category.objects.create(key="electrical-wiring",
                                     label_en="Electrical & Wiring",
                                     parent=family, tier="service")
    for path, node in [
        (["For Sale", "Mobile Phones & Accessories", "Mobile Phones"], phones),
        (["Services", "Repairs, Maintenance & Household Work",
          "Electrical & Wiring"], wiring),
    ]:
        SourceCategoryMap.objects.create(source="ibay", path=path,
                                         path_key=path_key("ibay", path),
                                         category=node)
    return {"phones": phones, "wiring": wiring}


def make_doc(source_key, title, path, **kw):
    return SearchDocument.objects.create(
        source="ibay", source_key=source_key, doc_type="shopping",
        url=f"https://ibay.com.mv/{source_key}", title_en=title,
        attrs={"category_path": path}, card=kw.pop("card", {}), **kw)


@pytest.mark.django_db
def test_two_listings_of_the_same_product_share_one_entity(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    a = make_doc("1", "Samsung Galaxy A15 128GB brand new 7438649", path)
    b = make_doc("2", "SAMSUNG GALAXY A15 128GB free delivery 9663178", path)
    ea, eb = resolve_document(a), resolve_document(b)
    assert ea is not None and ea.pk == eb.pk
    assert ea.kind == "product"
    assert Entity.objects.count() == 1


@pytest.mark.django_db
def test_a_listing_with_no_identity_gets_no_entity(fixtures):
    doc = make_doc("3", "Excellent condition item for sale",
                   ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"])
    assert resolve_document(doc) is None
    assert Entity.objects.count() == 0


@pytest.mark.django_db
def test_service_listings_group_by_phone_not_by_seller(fixtures):
    """Measured: 781 distinct phones against 946 seller accounts, so one
    operator posts under several accounts and the phone merges them."""
    path = ["Services", "Repairs, Maintenance & Household Work",
            "Electrical & Wiring"]
    a = make_doc("4", "Electrician wiring repair 7438649", path,
                 contact_phone="7438649", card={"seller_name": "Miabulbul"})
    b = make_doc("5", "Room light board installation 7438649", path,
                 contact_phone="7438649", card={"seller_name": "OtherAccount"})
    ea, eb = resolve_document(a), resolve_document(b)
    assert ea.pk == eb.pk
    assert ea.kind == "service"
    assert ea.provider_key == "7438649"


@pytest.mark.django_db
def test_a_service_with_no_phone_falls_back_to_the_seller(fixtures):
    path = ["Services", "Repairs, Maintenance & Household Work",
            "Electrical & Wiring"]
    doc = make_doc("6", "Wiring work", path, card={"seller_name": "Markspencer"})
    entity = resolve_document(doc)
    assert entity is not None
    assert entity.provider_key == "seller:Markspencer"


@pytest.mark.django_db
def test_resolution_is_idempotent(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("7", "Samsung Galaxy A15 128GB", path)
    first = resolve_document(doc)
    second = resolve_document(doc)
    assert first.pk == second.pk
    assert EntityLink.objects.filter(source="ibay", source_key="7").count() == 1


@pytest.mark.django_db
def test_a_document_links_to_at_most_one_entity(fixtures):
    """Re-resolving after the title changes moves the link, never duplicates."""
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    doc = make_doc("8", "Samsung Galaxy A15 128GB", path)
    resolve_document(doc)
    doc.title_en = "Samsung Galaxy A25 256GB"
    doc.save(update_fields=["title_en"])
    resolve_document(doc)
    assert EntityLink.objects.filter(source="ibay", source_key="8").count() == 1
    assert Entity.objects.count() == 2


@pytest.mark.django_db
def test_listing_count_is_maintained(fixtures):
    path = ["For Sale", "Mobile Phones & Accessories", "Mobile Phones"]
    make_doc("9", "Samsung Galaxy A15 128GB", path)
    make_doc("10", "Samsung Galaxy A15 128GB used", path)
    counts = resolve_source("ibay")
    entity = Entity.objects.get(kind="product")
    assert entity.listing_count == 2
    assert counts["linked"] == 2


@pytest.mark.django_db
def test_an_unmapped_category_still_resolves(fixtures):
    """The mapped key contributes the empty string; identity carries the key."""
    doc = make_doc("11", "Samsung Galaxy A15 128GB",
                   ["For Sale", "Never Reviewed"])
    assert resolve_document(doc) is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/catalog/test_resolve.py -v`
Expected: FAIL, `ImportError: cannot import name 'Entity' from 'catalog.models'`.

- [ ] **Step 3: Add the four models**

Append to `catalog/models.py`:

```python
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
```

- [ ] **Step 4: Write `catalog/resolve.py`**

```python
"""Document -> entity. Deterministic, no model call. Spec section 7."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from catalog.identity import (brand_vocabulary, clean_title, match_brand,
                              model_tokens, product_key, service_key)
from catalog.models import Entity, EntityLink
from search.contacts import primary_phone
from search.models import SearchDocument
from search.taxonomy import map_path

logger = logging.getLogger(__name__)

SERVICE_ROOTS = {"Services"}


def _mapped_key(doc: SearchDocument) -> str:
    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    category = map_path(doc.source, path)
    return category.key if category else ""


def _is_service(doc: SearchDocument) -> bool:
    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    if path and path[0] in SERVICE_ROOTS:
        return True
    category = map_path(doc.source, path)
    return bool(category and category.tier == "service")


def _provider_key(doc: SearchDocument) -> str:
    phone = doc.contact_phone or primary_phone(doc.title_en, doc.summary_en)
    if phone:
        return phone
    seller = (doc.card or {}).get("seller_name") or ""
    return f"seller:{seller}" if seller else ""


def resolve_document(doc: SearchDocument, *, vocabulary=None) -> Entity | None:
    """The entity this document belongs to, creating it if new.

    Returns None when the document carries no usable identity. That is a
    deliberate miss: an entity built on a guessed brand puts wrong specs on a
    real listing, which is worse than no profile at all.
    """
    mapped = _mapped_key(doc)

    if _is_service(doc):
        provider = _provider_key(doc)
        if not provider and not mapped:
            return None
        key = service_key(provider, mapped)
        defaults = {
            "kind": "service",
            "provider_key": provider,
            "service_type": mapped,
            "identity_confidence": 0.9 if provider.isdigit() else 0.6,
        }
        method = "seller_service"
    else:
        vocabulary = brand_vocabulary() if vocabulary is None else vocabulary
        brand = match_brand(doc.title_en, vocabulary)
        tokens = model_tokens(doc.title_en)
        if not brand and not tokens:
            return None
        key = product_key(brand, tokens, mapped)
        defaults = {
            "kind": "product",
            "brand": brand,
            "model_name": " ".join(tokens)[:128],
            # Both signals present is the only high-confidence case; the
            # inferred-spec filter floor reads this (spec section 9).
            "identity_confidence": 0.9 if (brand and tokens) else 0.5,
        }
        method = "identity_match"

    path = [str(p) for p in (doc.attrs.get("category_path") or [])]
    category = map_path(doc.source, path)

    with transaction.atomic():
        entity, _ = Entity.objects.get_or_create(
            key=key, defaults={**defaults, "category": category})
        # update_or_create, not create: a document links to at most one entity,
        # so a re-resolution after the title changed must MOVE the link.
        EntityLink.objects.update_or_create(
            source=doc.source, source_key=doc.source_key,
            defaults={"entity": entity, "method": method,
                      "confidence": entity.identity_confidence},
        )
    return entity


def recount(entity_ids=None) -> int:
    """Refresh listing_count. Cheap, and wrong counts are user-visible."""
    from django.db.models import Count

    qs = Entity.objects.all()
    if entity_ids is not None:
        qs = qs.filter(id__in=entity_ids)
    updated = 0
    for entity in qs.annotate(n=Count("links")).only("id", "listing_count"):
        if entity.listing_count != entity.n:
            Entity.objects.filter(id=entity.id).update(listing_count=entity.n)
            updated += 1
    return updated


def resolve_source(source: str, *, limit=None, dry_run=False) -> dict:
    counts = {"seen": 0, "linked": 0, "missed": 0}
    vocabulary = brand_vocabulary()
    qs = (SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
          .filter(source=source)
          .only("id", "source", "source_key", "title_en", "attrs", "card",
                "contact_phone"))
    for doc in qs.iterator(chunk_size=500):
        if limit is not None and counts["seen"] >= limit:
            break
        counts["seen"] += 1
        if dry_run:
            continue
        entity = resolve_document(doc, vocabulary=vocabulary)
        counts["linked" if entity else "missed"] += 1
    recount()
    return counts
```

- [ ] **Step 5: Write the command and the admin**

`catalog/management/commands/resolve_entities.py`:

```python
from django.core.management.base import BaseCommand

from catalog.resolve import resolve_source


class Command(BaseCommand):
    help = "Group documents into canonical entities. Deterministic, free."

    def add_arguments(self, parser):
        parser.add_argument("--source", default="ibay")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        counts = resolve_source(opts["source"], limit=opts["limit"],
                                dry_run=opts["dry_run"])
        rate = (100 * counts["missed"] / counts["seen"]) if counts["seen"] else 0
        self.stdout.write(self.style.SUCCESS(
            f"{counts['seen']} seen, {counts['linked']} linked, "
            f"{counts['missed']} missed ({rate:.1f}%)"))
```

`catalog/admin.py`:

```python
from django.contrib import admin

from catalog.models import Brand, Entity, EntityField, EntityLink, FieldProposal


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "aliases", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name", "aliases")


class EntityFieldInline(admin.TabularInline):
    model = EntityField
    extra = 0
    fields = ("key_raw", "value_num", "value_text", "unit", "provenance",
              "support_count", "is_winner")
    readonly_fields = ("provenance", "support_count")


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ("__str__", "kind", "brand", "service_type", "category",
                    "listing_count", "profile_status", "identity_confidence")
    list_filter = ("kind", "profile_status")
    search_fields = ("key", "title_en", "brand", "model_name", "provider_key")
    ordering = ("-listing_count",)
    inlines = [EntityFieldInline]


@admin.register(EntityLink)
class EntityLinkAdmin(admin.ModelAdmin):
    list_display = ("source", "source_key", "entity", "method", "confidence")
    list_filter = ("source", "method")
    search_fields = ("source_key",)


@admin.register(FieldProposal)
class FieldProposalAdmin(admin.ModelAdmin):
    """Conflicted first: those are the ones needing a human."""

    list_display = ("entity", "key_raw", "value_text", "value_num", "status",
                    "created_at")
    list_filter = ("status", "key_raw")
    list_editable = ("status",)
    ordering = ("status", "-created_at")
```

- [ ] **Step 6: Migrate and run the tests**

Run:
```bash
python manage.py makemigrations catalog
python manage.py migrate
pytest tests/catalog -v
```
Expected: PASS.

- [ ] **Step 7: Build the golden set**

`catalog/management/commands/eval_entities.py`:

```python
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
            rows.append({
                "source_key": doc.source_key,
                "title": doc.title_en,
                "resolved_entity": link.entity.key[:16] if link else None,
                "resolved_title": link.entity.title_en if link else None,
                "resolved_brand": link.entity.brand if link else None,
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
```

Add `PyYAML` is already in requirements (6.0.3), so no dependency change.

- [ ] **Step 8: Resolve the real corpus and label**

Run:
```bash
python manage.py resolve_entities --source ibay
python manage.py eval_entities --sample 50
# fill in `correct:` for all 50 rows by hand, then:
python manage.py eval_entities
```
Expected: about 3,800 product entities and about 1,500 service entities. Do not
proceed to task 6 with precision below 90% - fix the brand vocabulary or the
token rules first.

- [ ] **Step 9: Commit**

```bash
jj commit -m "catalog task 5: entity resolution, links, and the precision gate"
```

---

## Task 6: Stage-2 entity profiles

**Files:**
- Create: `catalog/schemas.py`, `catalog/prompts.py`, `catalog/tiers.py`, `catalog/profile.py`, `catalog/management/commands/build_profiles.py`
- Modify: `beynunehcheh/settings.py`
- Test: `tests/catalog/test_tiers.py`, `tests/catalog/test_profile.py`

**Interfaces:**
- Consumes: `Entity`, `EntityLink`, `EntityField` from task 5; `EnrichClient`, `extract_candidates`, `normalize_for_match`, `token_overlap`, `STRING_OVERLAP_FLOOR` from P4.
- Produces:
  ```python
  PROFILE_PROMPT_VERSION: int
  ProfileSpec        # key_raw, value_num, value_text, unit, origin
  ProductProfile / ServiceProfile / EntityProfileOutput
  classify_origin(*, claimed, value_num, value_text, union_text, candidates) -> str
  build_profile_input(entity) -> ProfileInput | None
  profile_one(inp, client) -> Entity
  select_entity_ids(*, kind=None, force=False, limit=None) -> list[int]
  run_profile_pass(entity_ids, *, concurrency=None) -> dict
  ```

- [ ] **Step 1: Write the failing test for the tier classifier**

`tests/catalog/test_tiers.py`:

```python
from catalog.tiers import classify_origin
from enrich.preextract import extract_candidates

UNION = ("Samsung Galaxy A15 128GB blue. 6.5 inch display. "
         "Free delivery Male' Hulhumale'.")
CAND = extract_candidates(UNION)


def test_a_string_present_in_the_listings_is_grounded():
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="blue", union_text=UNION,
                           candidates=CAND) == "grounded"


def test_a_number_present_in_the_candidate_set_is_grounded():
    assert classify_origin(claimed="from_listings", value_num=128,
                           value_text="", union_text=UNION,
                           candidates=CAND) == "grounded"


def test_a_claim_the_text_does_not_support_is_demoted_not_dropped():
    """The behaviour the whole coverage argument rests on: the validator
    classifies instead of deleting."""
    assert classify_origin(claimed="from_listings", value_num=5000,
                           value_text="", union_text=UNION,
                           candidates=CAND) == "inferred"


def test_a_knowledge_claim_is_inferred_even_when_the_text_agrees():
    """Honesty is rewarded but not upgraded: the model said it came from
    knowledge, so it is inferred."""
    assert classify_origin(claimed="from_knowledge", value_num=128,
                           value_text="", union_text=UNION,
                           candidates=CAND) == "inferred"


def test_a_very_short_string_cannot_be_grounded_by_substring_luck():
    """Two characters match almost anything; enrich/validate.py sets the same
    floor for the same reason."""
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="A1", union_text=UNION,
                           candidates=CAND) == "inferred"


def test_token_overlap_grounds_a_reordered_phrase():
    assert classify_origin(claimed="from_listings", value_num=None,
                           value_text="Galaxy A15 Samsung", union_text=UNION,
                           candidates=CAND) == "grounded"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/catalog/test_tiers.py -v`
Expected: FAIL, no module `catalog.tiers`.

- [ ] **Step 3: Write `catalog/tiers.py`**

```python
"""The origin classifier. Spec section 8.

The model tags every spec `from_listings` or `from_knowledge`, and this module
checks the tag instead of trusting it. A failed `from_listings` claim is demoted
to `inferred`, never dropped -- which is the difference between this stage and
stage 1, where an unsupported value is deleted (enrich/validate.py).

The primitives are imported from enrich/validate.py rather than reimplemented,
so "grounded" means exactly the same thing in both stages.
"""

from __future__ import annotations

from enrich.preextract import Candidates
from enrich.validate import (MIN_GROUNDED_LEN, STRING_OVERLAP_FLOOR,
                             normalize_for_match, token_overlap)


def classify_origin(*, claimed: str, value_num, value_text: str,
                    union_text: str, candidates: Candidates) -> str:
    if claimed != "from_listings":
        return "inferred"

    if value_num is not None:
        formatted = (str(int(value_num)) if float(value_num).is_integer()
                     else str(value_num))
        return ("grounded" if formatted in candidates.all_numeric_strings()
                else "inferred")

    value = (value_text or "").strip()
    if len(value) < MIN_GROUNDED_LEN:
        return "inferred"
    haystack = normalize_for_match(union_text)
    if normalize_for_match(value) in haystack:
        return "grounded"
    if token_overlap(value, union_text) >= STRING_OVERLAP_FLOOR:
        return "grounded"
    return "inferred"
```

- [ ] **Step 4: Run the tier tests**

Run: `pytest tests/catalog/test_tiers.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Write the profile schemas**

`catalog/schemas.py`:

```python
"""Stage-2 output schemas. Spec sections 8.1 and 8.2.

Products get a spec sheet; services get a shape of their own, because a spec
sheet is the wrong model for 'this person will come and fix your aircon'.
Everything is optional: an omitted field is correct behaviour, exactly as in
enrich/schemas.py.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ORIGINS = ("from_listings", "from_knowledge")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ProfileSpec(_Base):
    key_raw: str = ""
    value_num: float | None = None
    value_text: str = ""
    unit: str = ""
    # Checked by catalog/tiers.py, never trusted.
    origin: Literal["from_listings", "from_knowledge"] = "from_knowledge"


class ProductProfile(_Base):
    brand: str = ""
    model_name: str = ""
    variant: str = ""
    specs: list[ProfileSpec] = Field(default_factory=list)


class ServiceProfile(_Base):
    service_type: str = ""
    services_offered: list[str] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    call_out: bool | None = None
    shop_visit: bool | None = None
    rate_basis: Literal["per_job", "per_hour", "per_visit",
                        "quote_only"] = "quote_only"
    availability: str = ""


class EntityProfileOutput(_Base):
    title_en: str = ""
    title_dv: str = ""
    summary_en: str = ""
    summary_dv: str = ""
    # A key from the registry, or empty. The model never invents one.
    category_key: str = ""
    product: ProductProfile | None = None
    service: ServiceProfile | None = None


_SCHEMA_CACHE: dict[str, str] = {}


def schema_text(kind: str) -> str:
    """Byte-identical per kind, so the provider's context cache keeps hitting
    (spec 5.1)."""
    if kind not in _SCHEMA_CACHE:
        model = ProductProfile if kind == "product" else ServiceProfile
        _SCHEMA_CACHE[kind] = json.dumps(model.model_json_schema(),
                                         sort_keys=True, ensure_ascii=False)
    return _SCHEMA_CACHE[kind]
```

- [ ] **Step 6: Write the prompt**

`catalog/prompts.py`:

```python
"""Stage-2 prompt. Spec section 8.

Same two rules as enrich/prompts.py: the system prompt is byte-identical on
every call so the context cache hits, and the instructions restate in the
imperative what tiers.py enforces anyway.

The one new instruction is the origin tag. The model is told plainly that
tagging a fact `from_listings` when it is not there does not get the fact
accepted -- it gets it demoted -- so there is no incentive to mislabel.
"""

from __future__ import annotations

import json

from catalog.schemas import schema_text

PROFILE_PROMPT_VERSION = 1

SYSTEM_PROMPT = f"""\
You normalize Maldivian classified listings into one profile per real-world \
thing. Several listings describing the same product or the same service \
provider are given together. You return JSON and nothing else.

Rules, in order of importance:

1. Tag every spec with `origin`. Use `from_listings` when the fact is stated in \
the LISTINGS block. Use `from_knowledge` when you know it about this product but \
the listings do not say it. Mislabelling gains you nothing: a `from_listings` \
claim that the text does not support is stored as knowledge anyway, and \
knowledge that turns out to be wrong is what users correct.
2. `category_key` must be one of the keys in the CATEGORIES block, or empty. \
Never invent a category.
3. Write `title_en` as the product or service a person would search for, with no \
phone number, no price, no delivery terms and no shouting. Keep the brand and \
the model number.
4. `summary_en` is one useful sentence of at most 240 characters. Say what the \
thing is, not what kind of listing it is.
5. Prefer omission over a guess. Every field is optional.
6. For a service, `services_offered` is the union of the work the listings \
describe, and `coverage` is the places they say they serve. Copy those from the \
text; they are `from_listings` facts.
7. Never do arithmetic and never invent a phone number, a price or a date.

Return an object with exactly these keys:
  title_en, title_dv, summary_en, summary_dv, category_key, product, service

Use `product` for a product entity and `service` for a service entity; leave the \
other null. Their schemas:

PRODUCT: {schema_text("product")}
SERVICE: {schema_text("service")}
"""


def build_profile_messages(*, kind: str, identity: dict, categories: list[str],
                           listings: list[str], repair_error=None) -> list[dict]:
    parts = [
        f"KIND: {kind}",
        f"IDENTITY: {json.dumps(identity, ensure_ascii=False, sort_keys=True)}",
        f"CATEGORIES: {json.dumps(sorted(categories), ensure_ascii=False)}",
        f"LISTINGS ({len(listings)}):",
        *[f"- {t}" for t in listings],
    ]
    if repair_error:
        parts.append("\nYour previous response could not be used. Fix exactly "
                     f"this and return the corrected JSON object:\n{repair_error}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)}]
```

- [ ] **Step 7: Write the failing test for the profile pass**

`tests/catalog/test_profile.py`:

```python
import pytest

from catalog.models import Entity, EntityField, EntityLink
from catalog.profile import build_profile_input, profile_one, select_entity_ids
from search.models import Category, SearchDocument


class FakeClient:
    """Stands in for EnrichClient. The provider chain is P4's and already
    tested; what needs testing here is what we do with the answer."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def run_chain(self, messages, *, rebuild=None):
        self.calls += 1
        return self.payload, "fake-model"

    async def aclose(self):
        pass


@pytest.fixture
def entity_with_listings(db):
    Category.objects.create(key="mobile-phones", label_en="Mobile Phones",
                            tier="primary")
    entity = Entity.objects.create(kind="product", key="k1", brand="Samsung",
                                   model_name="A15 128GB")
    for i, title in enumerate([
        "Samsung Galaxy A15 128GB blue 6.5 inch 7438649",
        "SAMSUNG A15 128GB free delivery 9663178",
    ]):
        SearchDocument.objects.create(
            source="ibay", source_key=str(i), doc_type="shopping",
            url=f"https://x/{i}", title_en=title,
            attrs={"specs_raw": {"Item Condition": "New"}})
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=str(i), method="identity_match")
    return entity


@pytest.mark.django_db
def test_build_profile_input_unions_the_listings(entity_with_listings):
    inp = build_profile_input(entity_with_listings)
    assert len(inp.listings) == 2
    assert "6.5 inch" in inp.union_text
    assert "mobile-phones" in inp.categories


@pytest.mark.django_db
def test_build_profile_input_returns_none_for_an_entity_with_no_links(db):
    orphan = Entity.objects.create(kind="product", key="k2")
    assert build_profile_input(orphan) is None


@pytest.mark.django_db(transaction=True)
def test_a_grounded_spec_is_stored_grounded(entity_with_listings):
    import asyncio

    client = FakeClient({
        "title_en": "Samsung Galaxy A15 128GB",
        "summary_en": "Samsung Galaxy A15 with 128GB storage.",
        "category_key": "mobile-phones",
        "product": {"brand": "Samsung", "model_name": "Galaxy A15",
                    "specs": [{"key_raw": "storage_gb", "value_num": 128,
                               "unit": "GB", "origin": "from_listings"}]},
    })
    inp = build_profile_input(entity_with_listings)
    asyncio.run(profile_one(inp, client))

    field = EntityField.objects.get(key_raw="storage_gb")
    assert field.provenance == "grounded"
    assert field.value_num == 128


@pytest.mark.django_db(transaction=True)
def test_an_unsupported_from_listings_claim_lands_as_inferred(entity_with_listings):
    import asyncio

    client = FakeClient({
        "title_en": "Samsung Galaxy A15",
        "category_key": "mobile-phones",
        "product": {"specs": [{"key_raw": "battery_mah", "value_num": 5000,
                               "unit": "mAh", "origin": "from_listings"}]},
    })
    asyncio.run(profile_one(build_profile_input(entity_with_listings), client))
    assert EntityField.objects.get(key_raw="battery_mah").provenance == "inferred"


@pytest.mark.django_db(transaction=True)
def test_an_invented_category_key_is_ignored(entity_with_listings):
    import asyncio

    client = FakeClient({"title_en": "X", "category_key": "not-a-real-key",
                         "product": {"specs": []}})
    asyncio.run(profile_one(build_profile_input(entity_with_listings), client))
    entity_with_listings.refresh_from_db()
    assert entity_with_listings.category_id is None
    assert entity_with_listings.profile_status == "ok"


@pytest.mark.django_db(transaction=True)
def test_a_provider_failure_is_a_stored_status_not_an_exception(entity_with_listings):
    import asyncio

    from enrich.client import ProviderError

    class Failing(FakeClient):
        async def run_chain(self, messages, *, rebuild=None):
            raise ProviderError("all stages failed")

    asyncio.run(profile_one(build_profile_input(entity_with_listings),
                            Failing(None)))
    entity_with_listings.refresh_from_db()
    assert entity_with_listings.profile_status == "failed"
    assert entity_with_listings.profile_error


@pytest.mark.django_db
def test_select_skips_an_entity_already_profiled_at_this_version(entity_with_listings):
    from catalog.prompts import PROFILE_PROMPT_VERSION

    entity_with_listings.profile_status = "ok"
    entity_with_listings.profile_prompt_version = PROFILE_PROMPT_VERSION
    entity_with_listings.save()
    assert select_entity_ids() == []
    assert select_entity_ids(force=True) == [entity_with_listings.id]
```

- [ ] **Step 8: Run it to verify it fails**

Run: `pytest tests/catalog/test_profile.py -v`
Expected: FAIL, no module `catalog.profile`.

- [ ] **Step 9: Write `catalog/profile.py`**

```python
"""Stage 2: one model call per entity. Spec section 8.

Per entity, not per document: the collapse ratios measured in the spec (6.13:1
for services, 1.87:1 for products) turn 16,608 document calls into about 5,300
entity calls, and an entity is also the only scope at which a spec sheet is
meaningful.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from asgiref.sync import sync_to_async
from django.conf import settings

from catalog.models import Entity, EntityField, EntityLink
from catalog.prompts import PROFILE_PROMPT_VERSION, build_profile_messages
from catalog.schemas import EntityProfileOutput
from catalog.tiers import classify_origin
from enrich.client import EnrichClient, ProviderError
from enrich.preextract import extract_candidates
from search.models import Category, SearchDocument, SpecKey
from search.specs.project import slugify_key

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProfileInput:
    entity_id: int
    kind: str
    identity: dict
    categories: list[str]
    listings: list[str] = field(default_factory=list)
    union_text: str = ""


def build_profile_input(entity: Entity) -> ProfileInput | None:
    keys = list(EntityLink.objects.filter(entity=entity)
                .values_list("source", "source_key"))
    if not keys:
        return None

    listings: list[str] = []
    for source, source_key in keys[: settings.CATALOG_PROFILE_MAX_LISTINGS]:
        doc = (SearchDocument.objects
               .filter(source=source, source_key=source_key)
               .only("title_en", "summary_en", "attrs").first())
        if doc is None:
            continue
        scraped = " ".join(f"{k}: {v}" for k, v in
                           (doc.attrs.get("specs_raw") or {}).items())
        listings.append(" ".join(p for p in (doc.title_en, doc.summary_en,
                                             scraped) if p)[:600])

    if not listings:
        return None

    union = "\n".join(listings)[: settings.ENRICH_MAX_INPUT_CHARS]
    identity = {"brand": entity.brand, "model_name": entity.model_name,
                "service_type": entity.service_type,
                "provider_listings": len(keys)}
    # The closed registry the model must choose from (spec section 5).
    categories = list(Category.objects.filter(is_active=True)
                      .values_list("key", flat=True))
    return ProfileInput(entity_id=entity.id, kind=entity.kind,
                        identity=identity, categories=categories,
                        listings=listings, union_text=union)


def _store_fields(entity: Entity, out: EntityProfileOutput,
                  inp: ProfileInput) -> int:
    """Write EntityField rows for this pass, replacing only the tiers stage 2
    owns. `scraped` and `correction` rows are never touched here."""
    candidates = extract_candidates(inp.union_text)
    registry = {k.key: k for k in SpecKey.objects.all()}

    EntityField.objects.filter(
        entity=entity, provenance__in=("grounded", "inferred")).delete()

    rows: list[EntityField] = []
    seen: set[tuple] = set()

    def push(key_raw, *, claimed, value_num=None, value_text="", unit=""):
        key_raw = slugify_key(key_raw)
        if not key_raw:
            return
        ident = (key_raw, value_num, value_text)
        if ident in seen:
            return
        seen.add(ident)
        provenance = classify_origin(claimed=claimed, value_num=value_num,
                                     value_text=value_text,
                                     union_text=inp.union_text,
                                     candidates=candidates)
        rows.append(EntityField(
            entity=entity, key_raw=key_raw,
            key=registry.get(key_raw), value_num=value_num,
            value_text=value_text[:128], unit=unit[:16],
            provenance=provenance,
            confidence=entity.identity_confidence))

    if out.product is not None:
        if out.product.brand:
            push("brand", claimed="from_listings", value_text=out.product.brand)
        for spec in out.product.specs:
            push(spec.key_raw, claimed=spec.origin, value_num=spec.value_num,
                 value_text=spec.value_text, unit=spec.unit)

    if out.service is not None:
        s = out.service
        for value in s.services_offered:
            push("service_offered", claimed="from_listings", value_text=value)
        for value in s.coverage:
            push("coverage", claimed="from_listings", value_text=value)
        if s.call_out is not None:
            push("call_out", claimed="from_listings",
                 value_text="yes" if s.call_out else "no")
        if s.shop_visit is not None:
            push("shop_visit", claimed="from_listings",
                 value_text="yes" if s.shop_visit else "no")
        if s.rate_basis:
            push("rate_basis", claimed="from_listings", value_text=s.rate_basis)

    EntityField.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)
    return len(rows)


async def profile_one(inp: ProfileInput, client) -> Entity:
    """One entity, one profile. Never raises: a failure is a stored status."""

    def _messages(repair_error=None):
        return build_profile_messages(
            kind=inp.kind, identity=inp.identity, categories=inp.categories,
            listings=inp.listings, repair_error=repair_error)

    entity = await sync_to_async(Entity.objects.get)(id=inp.entity_id)
    entity.profile_prompt_version = PROFILE_PROMPT_VERSION

    try:
        payload, model_name = await client.run_chain(
            _messages(), rebuild=lambda err: _messages(repair_error=err))
    except ProviderError as exc:
        entity.profile_status = "failed"
        entity.profile_error = str(exc)[:2000]
        await sync_to_async(entity.save)()
        return entity

    out = (EntityProfileOutput(**payload) if isinstance(payload, dict)
           else EntityProfileOutput())

    entity.title_en = out.title_en[:256]
    entity.title_dv = out.title_dv[:256]
    entity.summary_en = out.summary_en[:240]
    entity.summary_dv = out.summary_dv[:240]

    # The model picks from the registry or gets ignored. It never creates one.
    if out.category_key:
        category = await sync_to_async(
            lambda: Category.objects.filter(key=out.category_key,
                                            is_active=True).first())()
        if category is not None:
            entity.category = category

    if out.product is not None:
        entity.brand = out.product.brand[:64] or entity.brand
        entity.model_name = out.product.model_name[:128] or entity.model_name
        entity.variant = out.product.variant[:64]
    if out.service is not None and out.service.service_type:
        entity.service_type = out.service.service_type[:64] or entity.service_type

    entity.profile_status = "ok"
    entity.profile_error = ""
    await sync_to_async(entity.save)()
    await sync_to_async(_store_fields)(entity, out, inp)
    return entity


def select_entity_ids(*, kind=None, force=False, limit=None) -> list[int]:
    qs = Entity.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    if not force:
        qs = qs.exclude(profile_status="ok",
                        profile_prompt_version__gte=PROFILE_PROMPT_VERSION)
    qs = qs.order_by("-listing_count")
    ids = list(qs.values_list("id", flat=True))
    return ids[:limit] if limit else ids


async def run_profile_pass(entity_ids: list[int], *, concurrency=None) -> dict:
    sem = asyncio.Semaphore(concurrency or settings.ENRICH_CONCURRENCY)
    client = EnrichClient()
    counts = {"ok": 0, "failed": 0, "skipped": 0}

    async def _one(entity_id: int):
        async with sem:
            entity = await sync_to_async(Entity.objects.get)(id=entity_id)
            inp = await sync_to_async(build_profile_input)(entity)
            if inp is None:
                counts["skipped"] += 1
                return
            result = await profile_one(inp, client)
            counts[result.profile_status] = counts.get(
                result.profile_status, 0) + 1

    try:
        await asyncio.gather(*(_one(i) for i in entity_ids))
    finally:
        await client.aclose()
    return counts
```

- [ ] **Step 10: Add the settings and the command**

In `beynunehcheh/settings.py`, after the enrichment block:

```python
# --- catalog (entity layer, spec section 19) ---
# How many linked listings feed one profile call. The union is what makes
# consensus possible; past a few dozen it is repetition paid for by the token.
CATALOG_PROFILE_MAX_LISTINGS = int(os.getenv("CATALOG_PROFILE_MAX_LISTINGS", "12"))
CATALOG_CONSENSUS_MIN_SELLERS = int(os.getenv("CATALOG_CONSENSUS_MIN_SELLERS", "2"))
CATALOG_INFERRED_MIN_CONFIDENCE = float(
    os.getenv("CATALOG_INFERRED_MIN_CONFIDENCE", "0.7"))
CATALOG_PROPOSAL_QUORUM = int(os.getenv("CATALOG_PROPOSAL_QUORUM", "3"))
CATALOG_PROPOSAL_MARGIN = int(os.getenv("CATALOG_PROPOSAL_MARGIN", "2"))
CATALOG_PROPOSAL_RATE_LIMIT = int(os.getenv("CATALOG_PROPOSAL_RATE_LIMIT", "20"))
CATALOG_PROPOSAL_RATE_WINDOW = int(os.getenv("CATALOG_PROPOSAL_RATE_WINDOW", "3600"))
```

`catalog/management/commands/build_profiles.py`:

```python
"""Stage 2 over entities. Costs money: reports the count before spending."""

from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand

from catalog.profile import run_profile_pass, select_entity_ids


class Command(BaseCommand):
    help = "Build entity profiles with the model. Costs one call per entity."

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=["product", "service"], default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--concurrency", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        ids = select_entity_ids(kind=opts["kind"], force=opts["force"],
                                limit=opts["limit"])
        self.stdout.write(f"{len(ids)} entities selected")
        if opts["dry_run"] or not ids:
            return
        counts = asyncio.run(run_profile_pass(
            ids, concurrency=opts["concurrency"]))
        self.stdout.write(self.style.SUCCESS(str(counts)))
```

- [ ] **Step 11: Run the tests**

Run: `pytest tests/catalog -v`
Expected: PASS.

- [ ] **Step 12: Validate on 5 real entities before spending**

Run:
```bash
python manage.py build_profiles --limit 5
python manage.py shell -c "
from catalog.models import Entity, EntityField
for e in Entity.objects.exclude(profile_status='pending')[:5]:
    print(e.kind, '|', e.title_en, '|', e.category)
    for f in e.fields.all():
        print('   ', f.key_raw, f.value_num or f.value_text, f.provenance)"
```
Read all five by hand. Check specifically that a spec you can see in the listing
text came back `grounded` and one you cannot came back `inferred`. If everything
is `inferred`, the union text is not reaching the classifier and the coverage
argument is broken - fix before the full pass.

- [ ] **Step 13: Commit**

```bash
jj commit -m "catalog task 6: per-entity profiles, origin tags checked not trusted"
```

---

## Task 7: The ladder, the projection, and the marked facets

**Files:**
- Create: `catalog/merge.py`, `catalog/cards.py`, `catalog/overlay.py`
- Modify: `search/specs/project.py`, `search/specs/discovery.py`, `search/models.py`, `api/schemas.py`, `api/routers/documents.py`, `beynunehcheh/settings.py`
- Test: `tests/catalog/test_merge.py`, `tests/catalog/test_overlay.py`

**Interfaces:**
- Consumes: `EntityField`, `Entity` from tasks 5-6.
- Produces:
  ```python
  PROVENANCE_ORDER = ("scraped", "correction", "consensus", "grounded", "inferred")
  promote_consensus(entity) -> int
  recompute_winners(entity) -> dict          # {"winners": n, "unresolved": n}
  winning_fields(entity) -> list[EntityField]
  build_service_card(entity, base: dict) -> dict
  apply_entity(draft: DocumentDraft) -> DocumentDraft   # SEARCH_DRAFT_OVERLAYS
  DocumentSpec.provenance                     # new column
  ```

- [ ] **Step 1: Write the failing test for the ladder**

`tests/catalog/test_merge.py`:

```python
import pytest

from catalog.merge import promote_consensus, recompute_winners, winning_fields
from catalog.models import Entity, EntityField, EntityLink
from search.models import SearchDocument


@pytest.fixture
def entity(db):
    return Entity.objects.create(kind="product", key="k", brand="Samsung")


def add(entity, provenance, value_text="x", key_raw="colour", **kw):
    return EntityField.objects.create(entity=entity, key_raw=key_raw,
                                      value_text=value_text,
                                      provenance=provenance, **kw)


@pytest.mark.django_db
def test_scraped_beats_correction(entity):
    """A source's own structured field is never overwritten (spec 5.2)."""
    add(entity, "scraped", "New", key_raw="item_condition")
    add(entity, "correction", "Used", key_raw="item_condition")
    recompute_winners(entity)
    winners = {f.key_raw: f for f in winning_fields(entity)}
    assert winners["item_condition"].value_text == "New"


@pytest.mark.django_db
def test_correction_beats_consensus_grounded_and_inferred(entity):
    add(entity, "inferred", "black")
    add(entity, "grounded", "blue")
    add(entity, "consensus", "green")
    add(entity, "correction", "red")
    recompute_winners(entity)
    assert winning_fields(entity)[0].value_text == "red"


@pytest.mark.django_db
def test_a_same_tier_tie_produces_no_winner_and_flags_the_entity(entity):
    """Never pick a side by row order. Spec section 9."""
    add(entity, "inferred", "black", support_count=1)
    add(entity, "inferred", "white", support_count=1)
    result = recompute_winners(entity)
    entity.refresh_from_db()
    assert result["unresolved"] == 1
    assert winning_fields(entity) == []
    assert entity.profile_status == "needs_review"


@pytest.mark.django_db
def test_support_count_breaks_a_same_tier_tie(entity):
    add(entity, "inferred", "black", support_count=3)
    add(entity, "inferred", "white", support_count=1)
    recompute_winners(entity)
    assert winning_fields(entity)[0].value_text == "black"


@pytest.mark.django_db
def test_consensus_needs_two_different_sellers(entity):
    """One seller repeating themselves is not agreement."""
    for i, seller in enumerate(["Miabulbul", "Miabulbul"]):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url=f"https://x/{i}",
                                      card={"seller_name": seller})
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=str(i), method="identity_match")
    add(entity, "grounded", "blue")
    assert promote_consensus(entity) == 0
    assert EntityField.objects.filter(entity=entity,
                                      provenance="consensus").count() == 0


@pytest.mark.django_db
def test_two_different_sellers_promote_to_consensus(entity):
    for i, seller in enumerate(["Miabulbul", "ExpartTechnician"]):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url=f"https://x/{i}",
                                      card={"seller_name": seller})
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=str(i), method="identity_match")
    add(entity, "grounded", "blue")
    assert promote_consensus(entity) == 1
    row = EntityField.objects.get(entity=entity, provenance="consensus")
    assert row.value_text == "blue"
    assert row.support_count == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/catalog/test_merge.py -v`
Expected: FAIL, no module `catalog.merge`.

- [ ] **Step 3: Write `catalog/merge.py`**

```python
"""The provenance ladder. Spec section 9.

scraped > correction > consensus > grounded > inferred

Two rules that are not generic precedence logic and exist for named reasons:

- `scraped` outranks a correction, because a source's own structured field is
  ground truth in this system (spec 5.2 rule 3) and letting a crowd overwrite it
  would make the most reliable data the easiest to vandalise.
- An unresolvable same-tier tie produces NO winner. Picking by row order would
  make the displayed value depend on insertion order, which is neither
  reproducible nor defensible to the user who reported it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from catalog.models import Entity, EntityField, EntityLink

PROVENANCE_ORDER = ("scraped", "correction", "consensus", "grounded", "inferred")
_RANK = {p: i for i, p in enumerate(PROVENANCE_ORDER)}


def winning_fields(entity: Entity) -> list[EntityField]:
    return list(EntityField.objects.filter(entity=entity, is_winner=True)
                .select_related("key").order_by("key_raw"))


def _sellers_for(entity: Entity) -> dict[tuple[str, str], str]:
    """(source, source_key) -> seller name, for the entity's linked documents.

    Keyed on the pair, not the bare source_key: source_key is only unique
    within a source, and iBay listing ids will collide with another source's
    keys the moment a second source has entities.
    """
    from search.models import SearchDocument

    pairs = list(EntityLink.objects.filter(entity=entity)
                 .values_list("source", "source_key"))
    out: dict[tuple[str, str], str] = {}
    for source, source_key in pairs:
        doc = (SearchDocument.objects
               .filter(source=source, source_key=source_key)
               .only("card").first())
        if doc is not None:
            out[(source, source_key)] = (doc.card or {}).get("seller_name") or ""
    return out


def promote_consensus(entity: Entity) -> int:
    """Copy a grounded value to `consensus` when independent sellers agree.

    Independence is the whole point: 2,971 of the corpus's listings come from
    one advertiser, so 'appears twice' means nothing and 'appears for two
    sellers' means something.
    """
    sellers = {s for s in _sellers_for(entity).values() if s}
    if len(sellers) < settings.CATALOG_CONSENSUS_MIN_SELLERS:
        return 0

    promoted = 0
    grounded = EntityField.objects.filter(entity=entity, provenance="grounded")
    for row in grounded:
        _, created = EntityField.objects.update_or_create(
            entity=entity, key_raw=row.key_raw, provenance="consensus",
            value_num=row.value_num, value_text=row.value_text,
            defaults={"key": row.key, "unit": row.unit,
                      "support_count": len(sellers),
                      "confidence": row.confidence},
        )
        promoted += int(created)
    return promoted


def recompute_winners(entity: Entity) -> dict:
    """Mark exactly one winner per key_raw, or none when it cannot be decided."""
    rows = list(EntityField.objects.filter(entity=entity))
    by_key: dict[str, list[EntityField]] = {}
    for row in rows:
        by_key.setdefault(row.key_raw, []).append(row)

    winners = unresolved = 0
    with transaction.atomic():
        EntityField.objects.filter(entity=entity, is_winner=True).update(
            is_winner=False)
        for key_raw, candidates in by_key.items():
            candidates.sort(key=lambda r: (_RANK[r.provenance],
                                           -r.support_count))
            best = candidates[0]
            tied = [c for c in candidates
                    if c.provenance == best.provenance
                    and c.support_count == best.support_count]
            if len(tied) > 1:
                unresolved += 1
                continue
            EntityField.objects.filter(id=best.id).update(is_winner=True)
            winners += 1

        # Any status except `failed` becomes needs_review: an entity whose
        # profile has not run yet can still have conflicting scraped and
        # corrected values, and that also needs a human.
        if unresolved and entity.profile_status != "failed":
            Entity.objects.filter(id=entity.id).update(
                profile_status="needs_review")

    return {"winners": winners, "unresolved": unresolved}


def recompute_all(*, kind=None) -> dict:
    totals = {"entities": 0, "winners": 0, "unresolved": 0, "consensus": 0}
    qs = Entity.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    for entity in qs.iterator(chunk_size=200):
        totals["consensus"] += promote_consensus(entity)
        result = recompute_winners(entity)
        totals["winners"] += result["winners"]
        totals["unresolved"] += result["unresolved"]
        totals["entities"] += 1
    return totals
```

- [ ] **Step 4: Run the ladder tests**

Run: `pytest tests/catalog/test_merge.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Write the failing test for the overlay and the projection**

`tests/catalog/test_overlay.py`:

```python
import pytest

from catalog.models import Entity, EntityField, EntityLink
from catalog.overlay import apply_entity
from search.adapters.base import DocumentDraft
from search.models import Category, DocumentSpec, SearchDocument, SpecKey
from search.specs.project import sync_document_specs


@pytest.fixture
def linked(db):
    node = Category.objects.create(key="mobile-phones", label_en="Mobile Phones",
                                   tier="primary")
    entity = Entity.objects.create(
        kind="product", key="k", brand="Samsung", model_name="Galaxy A15",
        title_en="Samsung Galaxy A15 128GB", summary_en="A phone.",
        category=node, profile_status="ok", identity_confidence=0.9)
    EntityLink.objects.create(entity=entity, source="ibay", source_key="1",
                              method="identity_match")
    SpecKey.objects.create(key="storage_gb", label_en="Storage",
                           datatype="numeric", unit="GB", is_facetable=True)
    return entity


def draft():
    return DocumentDraft(source="ibay", source_key="1", doc_type="shopping",
                         url="https://x/1", title_en="SAMSUNG A15 128GB 7438649",
                         card={"title": "SAMSUNG A15 128GB 7438649"})


@pytest.mark.django_db
def test_the_entity_title_replaces_the_seller_title_for_display(linked):
    out = apply_entity(draft())
    assert out.card["title"] == "Samsung Galaxy A15 128GB"
    assert out.attrs["entity_id"] == linked.id


@pytest.mark.django_db
def test_an_unlinked_document_passes_through_untouched(linked):
    d = DocumentDraft(source="ibay", source_key="999", doc_type="shopping",
                      url="https://x/999", title_en="Untouched")
    assert apply_entity(d).title_en == "Untouched"


@pytest.mark.django_db
def test_winning_entity_specs_reach_documentspec_with_provenance(linked):
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=128, unit="GB", provenance="inferred",
                               is_winner=True)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        title_en="SAMSUNG A15 128GB", attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    row = DocumentSpec.objects.get(document_id=doc.id, key_raw="storage_gb")
    assert row.value_num == 128
    assert row.provenance == "inferred"


@pytest.mark.django_db
def test_a_non_winning_field_is_not_projected(linked):
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=64, unit="GB", provenance="inferred",
                               is_winner=False)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    assert not DocumentSpec.objects.filter(document_id=doc.id,
                                           key_raw="storage_gb").exists()


@pytest.mark.django_db
def test_low_identity_confidence_keeps_inferred_specs_out_of_the_substrate(linked):
    """Filterable, but only above the confidence floor (spec section 18)."""
    linked.identity_confidence = 0.4
    linked.save()
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=128, unit="GB", provenance="inferred",
                               is_winner=True)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    assert not DocumentSpec.objects.filter(document_id=doc.id,
                                           key_raw="storage_gb").exists()


@pytest.mark.django_db
def test_a_grounded_spec_ignores_the_confidence_floor(linked):
    linked.identity_confidence = 0.4
    linked.save()
    EntityField.objects.create(entity=linked, key_raw="storage_gb",
                               value_num=128, unit="GB", provenance="grounded",
                               is_winner=True)
    doc = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping", url="https://x/1",
        attrs={"entity_id": linked.id})
    sync_document_specs(doc)
    assert DocumentSpec.objects.filter(document_id=doc.id,
                                       key_raw="storage_gb").exists()


@pytest.mark.django_db
def test_a_service_entity_gets_the_service_card(db):
    entity = Entity.objects.create(kind="service", key="s1",
                                   provider_key="7438649",
                                   service_type="electrical-wiring",
                                   title_en="Electrical wiring and repair",
                                   profile_status="ok")
    EntityLink.objects.create(entity=entity, source="ibay", source_key="2",
                              method="seller_service")
    EntityField.objects.create(entity=entity, key_raw="coverage",
                               value_text="Male'", provenance="grounded",
                               is_winner=True)
    d = DocumentDraft(source="ibay", source_key="2", doc_type="shopping",
                      url="https://x/2", title_en="wiring 7438649",
                      card={"title": "wiring 7438649", "phone": "7438649"})
    card = apply_entity(d).card
    assert card["kind"] == "service"
    assert card["coverage"] == ["Male'"]
    assert card["phone"] == "7438649"
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/catalog/test_overlay.py -v`
Expected: FAIL, no module `catalog.overlay`.

- [ ] **Step 7: Add the `provenance` column**

In `search/models.py`, on `DocumentSpec`:

```python
    # Where the value came from (catalog spec section 9). Empty for rows
    # written before the entity layer and for the deterministic extractor,
    # which is grounded by construction.
    provenance = models.CharField(max_length=16, blank=True, db_index=True)
```

- [ ] **Step 8: Write `catalog/cards.py` and `catalog/overlay.py`**

`catalog/cards.py`:

```python
"""Card payloads for entity-backed documents. Spec section 11.

Nothing time-dependent goes in here, same rule as enrich/cards.py: the card is
written at index time and read for months.
"""

from __future__ import annotations


def _values(fields, key_raw: str) -> list[str]:
    return [f.value_text for f in fields
            if f.key_raw == key_raw and f.value_text]


def build_service_card(entity, fields, base: dict) -> dict:
    """A service is not a product: no spec chips, no condition, no brand.
    What a caller wants is who does what, where, and the number to call."""
    return {
        **base,
        "kind": "service",
        "title": entity.title_en or base.get("title", ""),
        "summary": entity.summary_en or base.get("summary", ""),
        "services_offered": _values(fields, "service_offered")[:6],
        "coverage": _values(fields, "coverage")[:6],
        "rate_basis": next(iter(_values(fields, "rate_basis")), ""),
        "call_out": next(iter(_values(fields, "call_out")), ""),
        "listing_count": entity.listing_count,
    }


def spec_chips(fields, limit: int = 3) -> list[str]:
    out: list[str] = []
    for f in fields:
        if f.value_num is None or not f.unit:
            continue
        n = int(f.value_num) if float(f.value_num).is_integer() else f.value_num
        out.append(f"{n}{f.unit}")
        if len(out) >= limit:
            break
    return out
```

`catalog/overlay.py`:

```python
"""Fold the entity profile into a DocumentDraft.

Registered after enrich.overlay.apply_enrichment in SEARCH_DRAFT_OVERLAYS, so
the entity layer sees the enriched draft and wins over it: the entity profile is
built from every listing of the thing, and a per-document extraction is built
from one.

`profile_tier` on the card is what the frontend renders the caveat from. It is
the lowest tier among the winning fields, because a profile is only as
trustworthy as its weakest displayed value.
"""

from __future__ import annotations

import logging

from catalog.cards import build_service_card, spec_chips
from catalog.merge import PROVENANCE_ORDER, winning_fields
from catalog.models import Entity, EntityLink
from search.adapters.base import DocumentDraft
from search.contacts import strip_phones

logger = logging.getLogger(__name__)

_USABLE = ("ok", "needs_review")


def apply_entity(draft: DocumentDraft) -> DocumentDraft:
    link = (EntityLink.objects
            .filter(source=draft.source, source_key=draft.source_key)
            .select_related("entity", "entity__category").first())
    if link is None:
        return draft
    entity: Entity = link.entity
    if entity.profile_status not in _USABLE:
        return draft

    fields = winning_fields(entity)
    tiers = [f.provenance for f in fields]
    lowest = max(tiers, key=PROVENANCE_ORDER.index) if tiers else ""

    if entity.title_en:
        draft.title_en = entity.title_en
    if entity.title_dv:
        draft.title_dv = entity.title_dv
    if entity.summary_en:
        draft.summary_en = entity.summary_en
    if entity.summary_dv:
        draft.summary_dv = entity.summary_dv

    draft.attrs = {
        **draft.attrs,
        "entity_id": entity.id,
        "entity_kind": entity.kind,
        "profile_tier": lowest,
        "identity_confidence": entity.identity_confidence,
    }

    card = dict(draft.card)
    card["entity_id"] = entity.id
    card["profile_tier"] = lowest
    card["listing_count"] = entity.listing_count
    if entity.category_id:
        card["category_leaf"] = entity.category.label_en

    if entity.kind == "service":
        draft.card = build_service_card(entity, fields, card)
    else:
        card["kind"] = "product"
        card["title"] = strip_phones(entity.title_en or card.get("title", ""))
        if entity.brand:
            card["brand"] = entity.brand
        chips = spec_chips(fields)
        if chips:
            card["spec_chips"] = chips
        draft.card = card

    return draft
```

In `beynunehcheh/settings.py`:

```python
SEARCH_DRAFT_OVERLAYS = [
    "enrich.overlay.apply_enrichment",
    # After enrichment: an entity profile is built from every listing of the
    # thing and must win over a single listing's extraction.
    "catalog.overlay.apply_entity",
]
```

- [ ] **Step 9: Add the fourth input to the projection**

In `search/specs/project.py`, add to `specs_for_document` after source 3, and
carry `provenance` through `push`:

```python
    # 4. winning entity fields (catalog spec section 11). Inferred values are
    # filterable, which is the point, but only above the identity-confidence
    # floor: a filter built on a guessed identity narrows to the wrong thing.
    entity_id = doc.attrs.get("entity_id")
    if entity_id:
        from catalog.merge import winning_fields
        from catalog.models import Entity

        entity = Entity.objects.filter(id=entity_id).first()
        if entity is not None:
            floor = settings.CATALOG_INFERRED_MIN_CONFIDENCE
            for f in winning_fields(entity):
                if (f.provenance == "inferred"
                        and entity.identity_confidence < floor):
                    continue
                if f.value_num is not None:
                    push(f.key_raw, value_num=f.value_num, unit=f.unit,
                         provenance=f.provenance)
                elif f.value_text:
                    push(f.key_raw, value_text=f.value_text,
                         provenance=f.provenance)
```

Update `push` to accept and store it:

```python
    def push(key_raw, *, value_num=None, value_text="", unit="",
             provenance=""):
        ...
        rows.append({
            "key_id": spec_key.id if spec_key else None,
            "key_raw": key_raw,
            "value_num": value_num,
            "value_text": value_text,
            "unit": unit,
            "provenance": provenance,
        })
```

and add `"attrs"` is already in the `sync_specs` `.only()` list, so no change
there.

- [ ] **Step 10: Mark the facets**

In `search/specs/discovery.py::_build`, add to the returned dict:

```python
        # A facet built partly on model knowledge says so, so the frontend can
        # disclose it on the result set rather than only on the detail page
        # (catalog spec section 9).
        "has_inferred": _has_inferred(cur, cte, params, key),
```

and the helper:

```python
def _has_inferred(cur, cte, params, key: SpecKey) -> bool:
    cur.execute(
        f"{cte} SELECT EXISTS (SELECT 1 FROM search_documentspec s "
        f"JOIN candidates c ON c.id = s.document_id "
        f"WHERE s.key_id = %(spec_key_id)s AND s.provenance = 'inferred')",
        {**params, "spec_key_id": key.id})
    return bool(cur.fetchone()[0])
```

In `api/schemas.py`, add to `FacetOut`:

```python
    has_inferred: bool = False
```

and to `ResultOut`:

```python
    profile_tier: str = ""
```

In `api/routers/documents.py::detail`, add to the response:

```python
        "entity_id": doc.attrs.get("entity_id"),
        "profile_tier": doc.attrs.get("profile_tier", ""),
```

and include provenance in the spec items:

```python
    specs = [
        {"key_raw": s.get("key_raw", ""), "value_num": s.get("value_num"),
         "value_text": s.get("value_text", ""), "unit": s.get("unit", ""),
         "provenance": s.get("provenance", "")}
        for s in (doc.attrs.get("specs") or [])
    ]
```

- [ ] **Step 11: Migrate and run the whole suite**

Run:
```bash
python manage.py makemigrations search --name documentspec_provenance
python manage.py migrate
pytest -q
```
Expected: PASS across all apps. The existing `tests/search/specs` and
`tests/api` suites must stay green - this task changes their data shape and a
break there is a real regression, not a stale test.

- [ ] **Step 12: Commit**

```bash
jj commit -m "catalog task 7: provenance ladder, spec projection, marked facets"
```

---

## Task 8: Crowdsourced proposals

**Files:**
- Create: `catalog/proposals.py`, `api/routers/entities.py`, `catalog/management/commands/apply_proposals.py`
- Modify: `api/schemas.py`, `api/ratelimit.py`, `api/urls.py`
- Test: `tests/catalog/test_proposals.py`, `tests/api/test_entities.py`

**Interfaces:**
- Consumes: `FieldProposal`, `EntityField` from task 5; `recompute_winners` from task 7.
- Produces:
  ```python
  propose(entity, key_raw, *, value_num=None, value_text="", unit="",
          ip_hash) -> None
  evaluate_field(entity, key_raw) -> str      # "pending"|"applied"|"conflicted"
  apply_ready(*, limit=None) -> dict
  proposal_quota_exceeded(ip_hash: str) -> bool
  ```

- [ ] **Step 1: Write the failing test for the policy**

`tests/catalog/test_proposals.py`:

```python
import pytest
from django.test import override_settings

from catalog.models import Entity, EntityField, FieldProposal
from catalog.proposals import apply_ready, evaluate_field, propose


@pytest.fixture
def entity(db):
    e = Entity.objects.create(kind="product", key="k", profile_status="ok")
    EntityField.objects.create(entity=e, key_raw="brand", value_text="Samsang",
                               provenance="inferred", is_winner=True)
    return e


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_one_proposal_changes_nothing(entity):
    propose(entity, "brand", value_text="Samsung", ip_hash="a")
    assert evaluate_field(entity, "brand") == "pending"
    assert not EntityField.objects.filter(entity=entity,
                                          provenance="correction").exists()


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_the_same_ip_cannot_reach_quorum_alone(entity):
    for _ in range(5):
        propose(entity, "brand", value_text="Samsung", ip_hash="a")
    assert FieldProposal.objects.count() == 1
    assert evaluate_field(entity, "brand") == "pending"


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_quorum_applies_the_correction(entity):
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
    assert evaluate_field(entity, "brand") == "applied"
    row = EntityField.objects.get(entity=entity, provenance="correction")
    assert row.value_text == "Samsung"
    assert row.support_count == 3
    assert row.is_winner is True


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_two_competing_values_conflict_and_nothing_applies(entity):
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
        propose(entity, "brand", value_text="Sony", ip_hash=ip + "2")
    assert evaluate_field(entity, "brand") == "conflicted"
    assert not EntityField.objects.filter(entity=entity,
                                          provenance="correction").exists()
    assert FieldProposal.objects.filter(status="conflicted").count() == 6


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_a_clear_lead_over_a_competitor_still_applies(entity):
    for ip in ("a", "b", "c", "d", "e"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
    propose(entity, "brand", value_text="Sony", ip_hash="z")
    assert evaluate_field(entity, "brand") == "applied"


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_an_empty_value_means_the_field_is_wrong(entity):
    """Applying it removes the winner rather than storing an empty string."""
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="", ip_hash=ip)
    assert evaluate_field(entity, "brand") == "applied"
    assert not EntityField.objects.filter(entity=entity,
                                          is_winner=True).exists()


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_a_correction_cannot_overwrite_a_scraped_value(entity):
    """The ladder still governs after a correction applies."""
    EntityField.objects.create(entity=entity, key_raw="item_condition",
                               value_text="New", provenance="scraped",
                               is_winner=True)
    for ip in ("a", "b", "c"):
        propose(entity, "item_condition", value_text="Used", ip_hash=ip)
    evaluate_field(entity, "item_condition")
    winner = EntityField.objects.get(entity=entity, key_raw="item_condition",
                                     is_winner=True)
    assert winner.provenance == "scraped"


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_QUORUM=3, CATALOG_PROPOSAL_MARGIN=2)
def test_apply_ready_sweeps_every_pending_field(entity):
    for ip in ("a", "b", "c"):
        propose(entity, "brand", value_text="Samsung", ip_hash=ip)
    FieldProposal.objects.update(status="pending")
    assert apply_ready()["applied"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/catalog/test_proposals.py -v`
Expected: FAIL, no module `catalog.proposals`.

- [ ] **Step 3: Write `catalog/proposals.py`**

```python
"""Crowdsourced field corrections. Spec section 10.

Auto-apply on agreement, chosen over an approval queue deliberately: the corpus
has 16,608 listings behind about 5,300 entities and no reviewer is going to
clear that queue, so a correction that needs a human is a correction that never
lands.

The risk this accepts, stated where the code is: a quorum over IP hashes is
defeatable by anyone with a phone hotspot and patience. The mitigations are the
retained EntityField audit trail, revertibility, and the conflicted queue.
Prevention is not among them.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.merge import recompute_winners
from catalog.models import Entity, EntityField, FieldProposal


def _value_key(proposal) -> tuple:
    return (proposal.value_num, (proposal.value_text or "").strip().lower())


def propose(entity: Entity, key_raw: str, *, value_num=None, value_text="",
            unit="", ip_hash: str) -> None:
    """Record one proposal. Duplicates from one IP hash are silently dropped."""
    try:
        with transaction.atomic():
            FieldProposal.objects.create(
                entity=entity, key_raw=key_raw, value_num=value_num,
                value_text=(value_text or "")[:128], unit=(unit or "")[:16],
                proposer_ip_hash=ip_hash)
    except IntegrityError:
        return                    # already counted; the caller learns nothing


def evaluate_field(entity: Entity, key_raw: str) -> str:
    """Apply, conflict, or leave pending. Returns what happened."""
    quorum = settings.CATALOG_PROPOSAL_QUORUM
    margin = settings.CATALOG_PROPOSAL_MARGIN

    pending = list(FieldProposal.objects.filter(
        entity=entity, key_raw=key_raw, status="pending"))
    if not pending:
        return "pending"

    votes: dict[tuple, set[str]] = defaultdict(set)
    for proposal in pending:
        votes[_value_key(proposal)].add(proposal.proposer_ip_hash)

    ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
    top_value, top_voters = ranked[0]
    runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0

    if len(top_voters) < quorum:
        return "pending"

    if runner_up and len(top_voters) - runner_up < margin:
        # Genuine disagreement. Nothing applies, the field falls back to the
        # next tier, and a human sees it.
        FieldProposal.objects.filter(
            entity=entity, key_raw=key_raw, status="pending").update(
                status="conflicted")
        return "conflicted"

    value_num, value_text = top_value
    with transaction.atomic():
        EntityField.objects.filter(
            entity=entity, key_raw=key_raw, provenance="correction").delete()
        if value_num is not None or value_text:
            sample = next(p for p in pending if _value_key(p) == top_value)
            EntityField.objects.create(
                entity=entity, key_raw=key_raw, value_num=value_num,
                value_text=sample.value_text, unit=sample.unit,
                provenance="correction", support_count=len(top_voters),
                confidence=1.0)
        # An empty proposed value means "this field is wrong": no correction row
        # is written, and the losing tiers below it are removed so nothing shows.
        else:
            EntityField.objects.filter(
                entity=entity, key_raw=key_raw,
                provenance__in=("consensus", "grounded", "inferred")).delete()

        FieldProposal.objects.filter(
            entity=entity, key_raw=key_raw, status="pending").update(
                status="applied")

    recompute_winners(entity)
    return "applied"


def apply_ready(*, limit=None) -> dict:
    """Sweep every field with pending proposals. Idempotent."""
    fields = (FieldProposal.objects.filter(status="pending")
              .values_list("entity_id", "key_raw").distinct())
    if limit:
        fields = fields[:limit]

    counts = {"applied": 0, "conflicted": 0, "pending": 0}
    for entity_id, key_raw in list(fields):
        entity = Entity.objects.filter(id=entity_id).first()
        if entity is None:
            continue
        counts[evaluate_field(entity, key_raw)] += 1
    return counts


def stale_conflicts(days: int = 30):
    """Conflicted fields nobody has resolved. The admin queue's real backlog."""
    cutoff = timezone.now() - dt.timedelta(days=days)
    return (FieldProposal.objects.filter(status="conflicted",
                                         created_at__lt=cutoff)
            .values("entity_id", "key_raw").distinct())
```

- [ ] **Step 4: Run the policy tests**

Run: `pytest tests/catalog/test_proposals.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Write the failing API test**

`tests/api/test_entities.py`:

```python
import pytest
from django.test import override_settings

from catalog.models import Entity, EntityField, FieldProposal

# The `api` fixture is tests/api/conftest.py's Client, and the mount prefix is
# /api/v1/ (beynunehcheh/urls.py). Both match the existing report tests.


@pytest.fixture
def entity(db):
    e = Entity.objects.create(kind="product", key="k", title_en="Galaxy A15",
                              profile_status="ok", listing_count=3)
    EntityField.objects.create(entity=e, key_raw="storage_gb", value_num=128,
                               unit="GB", provenance="inferred", is_winner=True)
    return e


@pytest.mark.django_db
def test_get_entity_returns_the_profile_with_provenance(api, entity):
    r = api.get(f"/api/v1/entities/{entity.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title_en"] == "Galaxy A15"
    assert body["listing_count"] == 3
    assert body["fields"][0]["provenance"] == "inferred"


@pytest.mark.django_db
def test_get_a_missing_entity_is_404(api, db):
    assert api.get("/api/v1/entities/999999").status_code == 404


@pytest.mark.django_db
def test_propose_is_always_202(api, entity):
    r = api.post(f"/api/v1/entities/{entity.id}/propose",
                 {"key_raw": "storage_gb", "value_num": 256},
                 content_type="application/json")
    assert r.status_code == 202
    assert FieldProposal.objects.count() == 1


@pytest.mark.django_db
def test_proposing_on_a_missing_entity_is_also_202(api, db):
    """The endpoint must not confirm what exists. Same rule as /report."""
    r = api.post("/api/v1/entities/999999/propose",
                 {"key_raw": "brand", "value_text": "Sony"},
                 content_type="application/json")
    assert r.status_code == 202
    assert FieldProposal.objects.count() == 0


@pytest.mark.django_db
def test_a_duplicate_from_one_caller_is_202_and_counted_once(api, entity):
    """session_hash is derived from IP plus user agent, so every request from
    the test client shares one hash -- which is exactly the real duplicate
    case."""
    for _ in range(3):
        api.post(f"/api/v1/entities/{entity.id}/propose",
                 {"key_raw": "brand", "value_text": "Sony"},
                 content_type="application/json")
    assert FieldProposal.objects.count() == 1


@pytest.mark.django_db
@override_settings(CATALOG_PROPOSAL_RATE_LIMIT=2)
def test_over_the_rate_limit_is_still_202_and_stores_nothing(api, entity):
    for i in range(5):
        r = api.post(f"/api/v1/entities/{entity.id}/propose",
                     {"key_raw": f"key_{i}", "value_text": "x"},
                     content_type="application/json")
        assert r.status_code == 202
    assert FieldProposal.objects.count() == 2
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/api/test_entities.py -v`
Expected: FAIL with 404s, since the router does not exist.

- [ ] **Step 7: Write the endpoint**

In `api/ratelimit.py`:

```python
def proposal_quota_exceeded(ip_hash: str) -> bool:
    """Counted over the proposals table, like reports: three gunicorn workers
    make an in-process limiter grant three times the budget, and Redis for one
    counter is not worth a service."""
    from catalog.models import FieldProposal

    window = timezone.now() - dt.timedelta(
        seconds=settings.CATALOG_PROPOSAL_RATE_WINDOW)
    used = FieldProposal.objects.filter(
        proposer_ip_hash=ip_hash, created_at__gte=window).count()
    return used >= settings.CATALOG_PROPOSAL_RATE_LIMIT
```

In `api/schemas.py`:

```python
class EntityFieldOut(Schema):
    key_raw: str
    value_num: float | None = None
    value_text: str = ""
    unit: str = ""
    provenance: str
    support_count: int


class EntityOut(Schema):
    id: int
    kind: str
    title_en: str
    title_dv: str = ""
    summary_en: str = ""
    summary_dv: str = ""
    brand: str = ""
    model_name: str = ""
    service_type: str = ""
    category_key: str | None = None
    identity_confidence: float
    profile_tier: str = ""
    listing_count: int
    fields: list[EntityFieldOut] = []


class ProposalIn(Schema):
    key_raw: str
    value_num: float | None = None
    value_text: str = ""
    unit: str = ""
```

`api/routers/entities.py`:

```python
"""Entity profiles and crowdsourced corrections. Spec section 11.1."""

from __future__ import annotations

from ninja import Router
from ninja.errors import HttpError

from api.logging import session_hash
from api.ratelimit import proposal_quota_exceeded
from api.schemas import AcceptedOut, EntityOut, ProposalIn
from catalog.merge import PROVENANCE_ORDER, winning_fields
from catalog.models import Entity
from catalog.proposals import evaluate_field, propose

router = Router()

MAX_KEY = 64


@router.get("/entities/{int:entity_id}", response=EntityOut)
def entity_detail(request, entity_id: int):
    entity = (Entity.objects.filter(id=entity_id)
              .select_related("category").first())
    if entity is None or entity.profile_status == "failed":
        raise HttpError(404, "not found")

    fields = winning_fields(entity)
    tiers = [f.provenance for f in fields]
    return {
        "id": entity.id,
        "kind": entity.kind,
        "title_en": entity.title_en,
        "title_dv": entity.title_dv,
        "summary_en": entity.summary_en,
        "summary_dv": entity.summary_dv,
        "brand": entity.brand,
        "model_name": entity.model_name,
        "service_type": entity.service_type,
        "category_key": entity.category.key if entity.category_id else None,
        "identity_confidence": entity.identity_confidence,
        "profile_tier": (max(tiers, key=PROVENANCE_ORDER.index)
                         if tiers else ""),
        "listing_count": entity.listing_count,
        "fields": [
            {"key_raw": f.key_raw, "value_num": f.value_num,
             "value_text": f.value_text, "unit": f.unit,
             "provenance": f.provenance, "support_count": f.support_count}
            for f in fields
        ],
    }


@router.post("/entities/{int:entity_id}/propose", response={202: AcceptedOut})
def propose_correction(request, entity_id: int, payload: ProposalIn):
    """Always 202. Spec section 11.1: reporting acceptance, deduplication or
    throttling would be an oracle for probing the quorum, and the caller has no
    legitimate use for the difference."""
    ip_hash = session_hash(request)

    if proposal_quota_exceeded(ip_hash):
        return 202, {"status": "accepted"}

    key_raw = (payload.key_raw or "").strip()[:MAX_KEY]
    if not key_raw:
        return 202, {"status": "accepted"}

    entity = Entity.objects.filter(id=entity_id).first()
    if entity is None:
        return 202, {"status": "accepted"}

    propose(entity, key_raw, value_num=payload.value_num,
            value_text=payload.value_text, unit=payload.unit, ip_hash=ip_hash)
    # Evaluated inline: quorum is small, the query is two indexed counts, and a
    # correction that waits for a cron job looks broken to the person who made
    # it.
    evaluate_field(entity, key_raw)
    return 202, {"status": "accepted"}
```

In `api/urls.py`:

```python
from api.routers import documents, entities, events, meta, search, suggest
...
api.add_router("", entities.router, tags=["entities"])
```

- [ ] **Step 8: Write the sweep command**

`catalog/management/commands/apply_proposals.py`:

```python
"""Sweep pending proposals. The endpoint evaluates inline, so this is the
safety net for proposals stranded by a crash mid-request, plus the report on
how big the conflicted backlog has grown.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from catalog.proposals import apply_ready, stale_conflicts


class Command(BaseCommand):
    help = "Apply proposals that have reached quorum; report conflicts."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--stale-days", type=int, default=30)

    def handle(self, *args, **opts):
        counts = apply_ready(limit=opts["limit"])
        stale = len(list(stale_conflicts(opts["stale_days"])))
        self.stdout.write(self.style.SUCCESS(str(counts)))
        if stale:
            self.stdout.write(self.style.WARNING(
                f"{stale} conflicted fields older than {opts['stale_days']} "
                f"days are waiting in the admin"))
```

- [ ] **Step 9: Run the full suite**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
jj commit -m "catalog task 8: field proposals, quorum policy, propose endpoint"
```

---

## Task 9: Backfill and measurements

**Files:**
- Create: `docs/superpowers/measurements/2026-08-catalog.md`
- Modify: `docs/superpowers/plans/README.md`

**Interfaces:**
- Consumes: every command from tasks 1-8.
- Produces: the measurement file that gates the open questions in spec section 18.

- [ ] **Step 1: Run the deterministic chain**

Order matters: the taxonomy must exist before entities are keyed, and phones
before services are grouped.

```bash
python manage.py seed_taxonomy --source ibay --apply
python manage.py map_categories --source ibay
python manage.py backfill_phones --source ibay
python manage.py seed_brands
python manage.py resolve_entities --source ibay
```

Record from the output: distinct paths mapped, documents still unmapped,
documents with a phone, brands seeded, entities created, and the resolution miss
rate.

- [ ] **Step 2: Gate on resolution precision**

```bash
python manage.py eval_entities --sample 50
# label all 50 by hand
python manage.py eval_entities
```

Do not continue below 90% precision. Fix the brand vocabulary or the token
rules, re-run `resolve_entities`, and re-score.

- [ ] **Step 3: Profile services first, then products**

Services are 1,495 entities against roughly 3,802 products, and their 6.13:1
collapse means the visible improvement per call is far larger, so they are the
cheaper proof that the pass works.

```bash
python manage.py build_profiles --kind service --limit 20
# read 5 by hand before continuing
python manage.py build_profiles --kind service
python manage.py build_profiles --kind product --limit 20
# read 5 by hand
python manage.py build_profiles --kind product
```

- [ ] **Step 4: Merge, project, reindex**

```bash
python manage.py shell -c "
from catalog.merge import recompute_all
print(recompute_all())"
python manage.py reindex --source ibay
python manage.py sync_specs --source ibay --prune
python manage.py dedupe_listings --source ibay
python manage.py rebuild_suggest_terms
```

`reindex` before `sync_specs`: the projection reads `attrs['entity_id']`, which
the overlay writes during reindex.

- [ ] **Step 5: Record the measurements**

Write `docs/superpowers/measurements/2026-08-catalog.md` with these tables
filled in from the runs above:

```markdown
# Catalog normalization, measured

Date: <run date>
Profile model: <ENRICH_MODEL>   PROFILE_PROMPT_VERSION: 1

## Taxonomy
| | count |
|---|---|
| distinct iBay paths | |
| Category nodes created | |
| documents mapped | |
| documents still unmapped | |
| junk leaves collapsed to parent | |

## Phones
| | count | share |
|---|---|---|
| documents with contact_phone | | |
| distinct phone numbers | | |
| largest advertiser by phone | | |

## Entities
| | products | services |
|---|---|---|
| entities | | |
| listings linked | | |
| collapse ratio | | |
| resolution miss rate | | |
| golden-set precision | | |

## Profiles
| | count |
|---|---|
| calls made | |
| ok / needs_review / failed | |
| wall clock | |
| EntityField rows by tier: scraped / correction / consensus / grounded / inferred | |

## Facet coverage, For Sale
| | before | after |
|---|---|---|
| documents with >=1 DocumentSpec row | | |
| distinct facetable keys discovered | | |
| facets marked has_inferred | | |

## Decisions this file settles
- Whether the inferred-spec confidence floor (CATALOG_INFERRED_MIN_CONFIDENCE)
  is set correctly, from the share of inferred rows excluded by it.
- Whether the brand vocabulary needs growing, from the miss rate.
- Whether CATALOG_PROFILE_MAX_LISTINGS is too low, from the consensus row count.
```

- [ ] **Step 6: Update the plans README**

Add to the status table:

```markdown
| `2026-08-19-catalog-normalization.md` | Catalog normalization | **written, pending** |
```

Add to the measurements table:

```markdown
| Catalog - entity counts, tier shares, facet coverage | `measurements/2026-08-catalog.md` | the inferred confidence floor, brand vocabulary growth |
```

Add to the cross-plan contract:

```markdown
**From Catalog (`catalog/` + `search/taxonomy.py`, `search/contacts.py`):**

```python
Category, SourceCategoryMap
path_key(source, path) -> str
map_path(source, path) -> Category | None
family_of(category) -> Category
primary_sibling_of(category) -> Category | None

PHONE_RE, primary_phone(*texts) -> str, strip_phones(text) -> str

Brand, Entity, EntityLink, EntityField, FieldProposal
PROVENANCE_ORDER = ("scraped", "correction", "consensus", "grounded", "inferred")
resolve_document(doc) -> Entity | None
winning_fields(entity) -> list[EntityField]
recompute_winners(entity) -> dict
propose(entity, key_raw, *, value_num, value_text, unit, ip_hash) -> None
evaluate_field(entity, key_raw) -> str
apply_entity(draft) -> DocumentDraft        # in SEARCH_DRAFT_OVERLAYS
```
```

Add to the scheduled jobs table:

```markdown
| `map_categories --source ibay` | after any taxonomy edit | admin category changes never reach documents |
| `resolve_entities --source ibay` | after each sync | new listings have no entity, so no profile |
| `build_profiles` | after resolve, costs money | new entities render unprofiled |
| `apply_proposals` | daily | proposals stranded by a mid-request crash never apply |
```

Add to the pending retrofit queue note that P10 now starts at task 3.

- [ ] **Step 7: Commit**

```bash
jj commit -m "catalog task 9: backfills run, measurements recorded"
```

---

## Self-Review

**Spec coverage.** Every numbered spec section maps to a task:

| Spec section | Task |
|---|---|
| 5 canonical taxonomy, SourceCategoryMap | 1 |
| 6.5 column additions | 2 (category, contact_phone), 7 (provenance) |
| 6.1-6.4 four models | 4 (Brand), 5 (Entity, EntityLink, EntityField, FieldProposal) |
| 7.1 product resolution | 4, 5 |
| 7.2 service resolution | 5 |
| 8 stage-2, origin classification | 6 |
| 8.1 product profile | 6 |
| 8.2 service profile | 6 |
| 9 provenance ladder, no-winner ties | 7 |
| 10 quorum, conflict, abuse | 8 |
| 11 overlay, projection, facet markers | 7 |
| 11.1 API | 7 (documents), 8 (entities, propose) |
| 12 phone extraction | 3 |
| 13 error handling and idempotency | 5 (idempotent keys), 6 (failed status), 7 (no winner) |
| 14 testing and measurement | every task, plus 9 |
| 15 cost | 9 |
| 16 gazette foundation | structural: `EntityLink(source, source_key)` in task 5 |
| 17 P10 relationship | 1, 2, and the README edit in 9 |
| 19 implementation order | task order 1-9 |
| 20 settings | 6 (all six settings added at once) |

Spec section 18's open seams are deliberately not tasks: services staying
`doc_type="shopping"` is the absence of work, and the confidence floor is a
setting task 7 reads and task 9 measures.

**Gap found and closed.** Spec section 15 quotes a cost projection but the P4
measurement file records no per-token cost, so task 9's measurement table asks
for calls and wall clock rather than a dollar figure it cannot source.

**Six defects found in this plan's own code and fixed inline.**

1. Task 8's API test used pytest-django's `client` fixture and `/api/entities/`.
   The project uses its own `api` fixture and mounts at `/api/v1/`
   (`beynunehcheh/urls.py`), so every one of those tests would have 404'd.
2. `match_brand` unpacked a `canonical` variable it never used, then looked the
   value up again through the alias key.
3. `test_strip_phones_collapses_the_separator_it_leaves_behind` asserted
   `"... | Tel:"` while `strip_phones` strips a trailing colon. The
   implementation is right - a dangling `Tel:` reads as broken markup - so the
   assertion was corrected, not the code.
4. `recompute_winners` flipped an entity to `needs_review` only from status
   `ok`, but a tie is reachable at status `pending`, which is what task 7's tie
   test actually constructs. It now flips from any status except `failed`.
5. `_sellers_for` keyed on the bare `source_key`, which is unique only within a
   source. It now keys on `(source, source_key)`, and this matters the moment
   gazette entities exist - which is the whole point of section 16.
6. `resolve_document` unpacked an unused `created` from `update_or_create`.

**Type consistency.** `winning_fields(entity)` returns `list[EntityField]` in
tasks 7 and 8. `PROVENANCE_ORDER` is the tuple in `catalog/merge.py`, imported
by `catalog/overlay.py` and `api/routers/entities.py`; `PROVENANCE` in
`catalog/models.py` is the Django choices list, and the two are deliberately
different objects with different shapes. `classify_origin` is keyword-only in
both its definition and all three call sites. `push()` in
`search/specs/project.py` gains one keyword argument with a default, so the
three existing call sites keep working unchanged.

**One deviation from the spec worth flagging.** The spec's section 10 implies a
sweep job applies quorum; task 8 evaluates inline in the request and keeps the
sweep as a safety net. A correction that waits for cron looks broken to the
person who made it, and the evaluation is two indexed counts.
