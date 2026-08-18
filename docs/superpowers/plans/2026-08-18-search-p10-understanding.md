# P10 Query Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse a query into the entity it names and the modifiers that qualify it, then resolve that into the category the user actually meant — so `iphone` returns phones and `iphone case` returns cases, from one mechanism.

**Architecture:** The entity fixes the product *family*; modifiers select the *tier and leaf* within it; absent a modifier the primary tier wins. All three come from `attrs.category_path`, which is already hierarchical, so the gazetteer is **derived from the corpus and refreshable**, not hand-curated. On top sit constraint modifiers (price, condition, numeric attributes) that resolve into the filters P5 already implements, and Dhivehi intent markers in all three input modes P2 supports. Every inference is surfaced to the user and removable.

**Tech Stack:** Django 6.0.5, PostgreSQL 18, `search/lang/` (P2), `search/facets.py` + `search/filters.py` (P5), `DocumentSpec` (P7).

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 4.4, 6, 7, 8.3, 9. This plan adds a section 6.6 to the spec; write it as part of task 1.

**Depends on:** P2 (`build_query_plan`, the three input modes), P5 (filters and facets), P7 (`DocumentSpec`, `SpecKey`), P9 task 4 (`category_leaf`).

---

## Global Constraints

- **Nothing is hand-curated that can be derived.** Families, tiers, leaves and most modifier words come from `category_path` and leaf names. Only genuine linguistic knowledge — synonyms, Dhivehi intent markers — is curated, and it lives in an admin table with the rest of the vocabularies.
- **Every inference is visible and removable.** An implied category filter appears in the response as an applied inference with a way to drop it. Silently narrowing a query is the same defect as silently relaxing one (P8 task 3), and it is worse, because the user cannot tell why results are missing.
- **Modifiers work in all three Dhivehi input modes.** Thaana, Latin-Dhivehi and keyboard-space, per spec 6. A modifier vocabulary that only works in English is half a feature in a bilingual engine.
- **Deterministic before probabilistic.** Gazetteer matching first; a model is only considered for what the gazetteer misses, and never invents a category that is not in the registry. Same rule as spec 5.2 layer 0.
- **Recall is never silently reduced.** An inference reorders and boosts by default; it only filters when confidence is high, and even then it is removable. `recall@5 >= 0.80` still gates.
- Version control is **jj**, not git.

---

## Measured evidence

All figures from the live corpus on 2026-08-18.

**The hierarchy is already explicit in the data.** `attrs.category_path` is an
ordered array and the tier is a literal segment:

```
For Sale > Mobile Phones & Accessories > Mobile Phones                      <- primary
For Sale > Mobile Phones & Accessories > Accessories > Cases, Protection...  <- accessory
For Sale > Mobile Phones & Accessories > Accessories > Charger
For Sale > Mobile Phones & Accessories > Accessories > Data Cable
For Sale > Mobile Phones & Accessories > Parts > LCD Screen & Digitizer      <- part
For Sale > Mobile Phones & Accessories > Parts > Battery
```

So "is this an accessory" requires **no curation at all**. This supersedes the
`CategoryKind` model proposed in P9 task 4b — that table would hand-curate a
fact the corpus already states.

**278 distinct leaves**, and modifier words largely appear in their own leaf
names, so the modifier gazetteer is derivable:

| query word | leaf it names | derivable? |
|---|---|---|
| charger | Charger, Battery and Charger | yes |
| cable | Data Cable | yes |
| headset | Headset - Bluetooth, Headset - Wired | yes |
| cover | Cover sets, Phone Cover / Housing | yes |
| screen | Screen Protection, LCD Screen & Digitizer | yes |
| protection | Cases, Protection & Skins, Screen Protection | yes |
| case | Cases, Protection & Skins | needs stemming (Cases) |
| protector | Screen Protection | needs stemming (Protection) |
| glass | Screen Protection | needs a synonym |

**Modifier frequency in titles** (21,255 titles scanned):

```
service   3,856      cover    542      case     396
repair    3,319      glass    440      cable    364
                     adapter  428      charger  316
```

`service` and `repair` outrank every accessory word combined, and there are
matching leaves (`Phone Servicing & Unlocking`, `Aircon Servicing & Repair`), so
the tier model is **primary | Accessories | Parts | Services**.

**Entities available now:** 35 distinct brands in `DocumentSpec` (Apple,
Samsung, Xiaomi, Sony...), 55 distinct `location` values (`Male City/Male`,
`Male City/HulhuMale`...). `island` and `atoll` are populated on **0** rows
despite existing as columns — task 5 addresses that.

**Dhivehi intent markers, and why they matter here more than for Google:**

```
Thaana:        ކުއްޔަށް 12    ދިނުން 14    ބޭނުންވެއްޖެ 1
Latin-Dhivehi: dhinun 152     bahattan 43   kuyyah 10    vikkaalan 3
```

The Latin-Dhivehi forms dominate, which is the shared-accommodation vocabulary
from spec 4.3.1 (`kudhin bahattan`, `firihen`). These are not accessory
modifiers — they are **intent** markers that select `listing_kind` and
`unit_kind`, and they are the reason this task cannot be a straight port of an
English query parser.

---

## The taxonomy is canonical, not iBay's

iBay's `category_path` is unusually clean — 278 leaves with `Accessories` and
`Parts` as literal segments. **That is one source's metadata, not the system's
taxonomy.** Gazette has no categories at all, and a future source may have flat
tags, wrong tags or none.

So the design mirrors the split the project already uses twice — spec 4.4's
"extraction is open, faceting is curated", and spec 4.3.3's "display metadata in
the database, adapter logic in code":

| Layer | Source-specific? | Who fills it |
|---|---|---|
| `Category` — the canonical taxonomy | no | curated, seeded from iBay's structure |
| adapter mapping | yes, one per source | code, where the source has categories |
| enrichment classification | no | the model, choosing from the registry |
| gazetteer for query parsing | no | derived from `Category` labels |

Three consequences that shape every task below:

**The model classifies into a closed registry.** It picks an existing
`Category.key` or returns nothing. It never invents a category, exactly as it
never invents a digit (spec 5.2 layer 0) and never promotes a `SpecKey` (spec
4.4). An unrecognised document goes to a review queue, which is how new
categories get proposed.

**The tier is a property of the taxonomy, not of a path string.** `primary`,
`accessory`, `part`, `service` are curated on `Category` once. iBay's path
segments are useful for *seeding* those values and are never read at query time.

**Query parsing reads `Category`, never `attrs.category_path`.** That is what
makes `iphone case` work identically for a source that never said the word
"Accessories".

---

## File structure

```
search/
  models.py                 MODIFIED: Category, QueryMarker; SearchDocument.category
  taxonomy.py               tier constants, traversal, resolution helpers
  understand/
    __init__.py
    gazetteer.py            built from Category labels + brands, cached
    parse.py                query -> Interpretation
    resolve.py              Interpretation -> filters + ranking hints
    markers.py              curated linguistic knowledge only
  adapters/ibay.py          MODIFIED: map category_path -> Category
  management/commands/
    seed_taxonomy.py        one-off, derives the tree from iBay's paths
    rebuild_gazetteer.py
    classify_categories.py  the AI pass, for sources without categories
enrich/
  schemas.py                MODIFIED: category_key on the attribute models
  prompts.py                MODIFIED: the registry is pasted into the prompt
api/schemas.py              MODIFIED: InterpretationOut
web/src/components/InterpretationNotice.tsx
```

---

### Task 1: The canonical taxonomy

**Files:** Modify `search/models.py`, `search/admin.py`. Create `search/taxonomy.py`, `search/management/commands/seed_taxonomy.py`. Test `search/tests/test_taxonomy.py`.

**Interfaces:** `Category` model; `TIERS = ("primary", "accessory", "part", "service")`;
`family_of(category) -> Category`; `primary_sibling_of(category) -> Category | None`;
`SearchDocument.category` (FK, `db_constraint=False`).

- [ ] **Step 1: Write the failing test**

```python
import pytest

from search.models import Category
from search.taxonomy import family_of, primary_sibling_of


@pytest.fixture
def taxonomy(db):
    phones = Category.objects.create(key="mobile_phones_family",
                                     label_en="Mobile Phones & Accessories",
                                     tier="family")
    primary = Category.objects.create(key="mobile_phones", label_en="Mobile Phones",
                                      parent=phones, tier="primary")
    charger = Category.objects.create(key="phone_charger", label_en="Charger",
                                      parent=phones, tier="accessory")
    battery = Category.objects.create(key="phone_battery", label_en="Battery",
                                      parent=phones, tier="part")
    repair = Category.objects.create(key="phone_repair",
                                     label_en="Phone Servicing & Unlocking",
                                     parent=phones, tier="service")
    return dict(family=phones, primary=primary, charger=charger,
                battery=battery, repair=repair)


@pytest.mark.django_db
def test_the_tier_is_curated_on_the_node_not_parsed_from_a_path(taxonomy):
    """iBay spells 'Accessories' in its path; another source will not. The tier
    is a property of the taxonomy so query parsing works for every source."""
    assert taxonomy["charger"].tier == "accessory"
    assert taxonomy["repair"].tier == "service"


@pytest.mark.django_db
def test_every_node_resolves_to_its_family(taxonomy):
    for k in ("primary", "charger", "battery", "repair"):
        assert family_of(taxonomy[k]) == taxonomy["family"]


@pytest.mark.django_db
def test_an_accessory_resolves_to_the_primary_sibling(taxonomy):
    """'iphone' with no modifier wants this. 'iphone charger' wants the
    accessory. One relationship serves both."""
    assert primary_sibling_of(taxonomy["charger"]) == taxonomy["primary"]


@pytest.mark.django_db
def test_a_family_with_no_primary_child_returns_none(taxonomy):
    orphan = Category.objects.create(key="services_family", label_en="Services",
                                     tier="family")
    Category.objects.create(key="aircon_repair", label_en="Aircon Repair",
                            parent=orphan, tier="service")
    assert primary_sibling_of(Category.objects.get(key="aircon_repair")) is None


@pytest.mark.django_db
def test_labels_are_bilingual(taxonomy):
    """Facet labels are user-visible; spec 9 requires both languages."""
    c = taxonomy["charger"]
    c.label_dv = "ޗާޖަރު"
    c.save()
    assert Category.objects.get(key="phone_charger").label_dv


@pytest.mark.django_db
def test_a_document_points_at_a_category_not_at_a_path_string(taxonomy):
    from search.models import SearchDocument
    d = SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                      doc_type="news", url="https://x",
                                      category=taxonomy["primary"])
    assert d.category.key == "mobile_phones"


@pytest.mark.django_db
def test_a_document_with_no_category_is_valid(taxonomy):
    """Most gazette documents will never have one, and search must not require
    it. An absent category means 'no inference available', not an error."""
    from search.models import SearchDocument
    SearchDocument.objects.create(source="gazette", source_key="IUL-2",
                                  doc_type="news", url="https://x")
```

- [ ] **Step 2** Run — expect FAIL.

- [ ] **Step 3** Implement:

```python
class Category(models.Model):
    """The canonical taxonomy. Source-independent by design.

    iBay happens to publish a clean hierarchy with `Accessories` and `Parts` as
    literal path segments; gazette publishes none, and a future source may
    publish flat or wrong tags. So sources *map into* this tree -- deterministically
    where they have categories (adapters), by model classification where they do
    not (task 2) -- and query parsing reads only this.

    `tier` is what makes 'iphone' mean the phone and 'iphone charger' mean the
    charger, so it is curated per node rather than inferred from a string that
    only one source emits.
    """

    TIERS = [("family", "family"), ("primary", "primary product"),
             ("accessory", "accessory"), ("part", "part"), ("service", "service")]

    key = models.SlugField(max_length=64, unique=True)
    label_en = models.CharField(max_length=128)
    label_dv = models.CharField(max_length=128, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.PROTECT, related_name="children")
    tier = models.CharField(max_length=16, choices=TIERS)
    doc_type = models.CharField(max_length=32, blank=True)
    # Extra query words that should select this node but are absent from its
    # label. Measured: 440 titles say "glass" and no label contains it.
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
```

and on `SearchDocument`:

```python
    # Partitioned table, so no FK constraint (spec 12.2). Null is normal:
    # most gazette documents have no category and search must not require one.
    category = models.ForeignKey("search.Category", null=True, blank=True,
                                 on_delete=models.SET_NULL, db_constraint=False,
                                 related_name="documents")
```

- [ ] **Step 4** `seed_taxonomy` derives the initial tree from iBay's 278 paths:
family = the segment above the tier, tier from the literal `Accessories`/`Parts`
segment or a `service|servicing|repair` match on the leaf name, else `primary`.
**This runs once as a seeding convenience.** Print every node it creates with the
inferred tier so a human reviews the mapping; the derivation is a starting point,
not the contract.

- [ ] **Step 5** Admin with `list_editable` on `tier` and `is_active`, filtered by
tier and parent, so correcting a bad inference is one click.

- [ ] **Step 6** `jj commit -m "P10 task 1: canonical category taxonomy"`

---

### Task 2: Getting documents into the taxonomy

**Files:** Modify `search/adapters/ibay.py`, `enrich/schemas.py`, `enrich/prompts.py`, `enrich/pipeline.py`. Create `search/management/commands/classify_categories.py`. Test `search/tests/test_category_mapping.py`, `tests/enrich/test_classify.py`.

Two routes in, and the second is the one that makes this versatile.

**Deterministic, where the source has categories.** iBay maps its
`category_path` to a `Category` via a seeded lookup. Free, exact, no model.

**Model classification, where it does not.** The enrichment pass is already
reading the document and already returns typed attributes; it gains a
`category_key` field and the registry is pasted into the prompt. The model
**selects from the registry** and may return null — the same constraint as every
other extracted field (spec 5.2 layer 0), and the same shape as `SpecKey`
promotion (spec 4.4): open input, curated vocabulary, a review queue for the rest.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_ibay_maps_its_path_deterministically(taxonomy, ibay_product):
    """No model call where the source already knows."""
    from search.adapters.ibay import IbayAdapter
    a = IbayAdapter()
    draft = a.to_document(a.fetch_raw(str(ibay_product.listing_id)))
    assert draft.attrs["category_key"] == "phone_charger"


@pytest.mark.django_db
def test_an_unmapped_path_yields_no_category_rather_than_a_guess(taxonomy):
    """A new iBay leaf must not silently land in the wrong node."""


@pytest.mark.django_db
async def test_the_model_classifies_a_document_with_no_source_category(taxonomy):
    """The gazette case, and every future source. The prompt carries the
    registry; the model picks a key from it."""
    inp = build_input("gazette", "IUL-1")
    client = _StubClient({"doc_type": "shopping",
                          "attrs": {"category_key": "phone_charger"}})
    rec = await enrich_one(inp, client)
    assert rec.attrs["category_key"] == "phone_charger"


@pytest.mark.django_db
async def test_an_invented_category_key_is_rejected(taxonomy):
    """The registry is closed. A key that is not in it is dropped and recorded,
    exactly like an ungrounded number."""
    inp = build_input("gazette", "IUL-1")
    client = _StubClient({"doc_type": "shopping",
                          "attrs": {"category_key": "flying_carpets"}})
    rec = await enrich_one(inp, client)
    assert rec.attrs.get("category_key") in (None, "")
    assert any(d["field"].endswith("category_key") for d in rec.validation["dropped"])


@pytest.mark.django_db
async def test_returning_no_category_is_valid(taxonomy):
    """Better than a wrong one. Most gazette notices belong nowhere in a
    shopping taxonomy."""
    inp = build_input("gazette", "IUL-1")
    rec = await enrich_one(inp, _StubClient({"doc_type": "news", "attrs": {}}))
    assert rec.status == "ok"


@pytest.mark.django_db
def test_unclassified_documents_surface_in_a_review_queue(taxonomy):
    """Frequency-ranked, like the SpecKey promotion queue (spec 4.4). This is
    how a missing category gets noticed and added."""
    from search.taxonomy import unclassified_summary
    rows = unclassified_summary(limit=10)
    assert isinstance(rows, list)
```

- [ ] **Step 2** Run — expect FAIL.

- [ ] **Step 3** Add `category_key: str = ""` to `ShoppingAttrs`, `PropertyAttrs`
and `JobAttrs`. Bump `PROMPT_VERSION`.

- [ ] **Step 4** The prompt gets the registry as `key: label` pairs, with an
explicit instruction:

```
N. `category_key` must be one of the keys listed below, copied exactly. If none
fits, leave it null. Do not invent a key and do not translate the labels.
```

At 278 nodes that is ~4k tokens of prompt, byte-identical on every call, so it
lands in DeepSeek's context cache (spec 5.1). If the taxonomy grows past what is
comfortable, send only the nodes for the document's `doc_type` — the registry is
already `doc_type`-scoped on the model.

- [ ] **Step 5** Validate in `enrich/validate.py`: a `category_key` not present
and active in `Category` is dropped with reason `unknown_category`. Reuse the
existing drop machinery; do not add a second path.

- [ ] **Step 6** `classify_categories` command for backfilling documents that
have no category, `--source`, `--limit`, `--dry-run`, reporting how many were
classified, left null, and rejected.

- [ ] **Step 7** `unclassified_summary()` groups uncategorised documents by
`doc_type` and top title tokens, frequency-ranked, for the admin queue.

- [ ] **Step 8** `jj commit -m "P10 task 2: map and classify documents into the taxonomy"`

---

### Task 3: The gazetteer, built from the taxonomy

**Files:** Create `search/understand/gazetteer.py`, `search/management/commands/rebuild_gazetteer.py`. Test `search/tests/test_gazetteer.py`.

**Interfaces:** `Gazetteer(families, nodes, tokens, brands, locations)`;
`build_gazetteer()`; `gazetteer()` (process-cached); `invalidate()`.

Reads `Category` labels and `aliases`, never `attrs.category_path`. That single
choice is what makes `iphone charger` work for a source that has never emitted
the word "Accessories".

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_tokens_map_query_words_to_taxonomy_nodes(taxonomy):
    g = build_gazetteer()
    assert "charger" in g.tokens
    assert "phone_charger" in g.tokens["charger"]


@pytest.mark.django_db
def test_aliases_close_the_gaps_labels_cannot(taxonomy):
    """440 titles say 'glass'; no label contains it. The alias lives on the
    node, so it travels with the taxonomy rather than in a separate table."""
    c = Category.objects.get(key="screen_protection")
    c.aliases = ["glass", "protector", "tempered"]
    c.save()
    g = build_gazetteer()
    assert "screen_protection" in g.tokens["glass"]


@pytest.mark.django_db
def test_dhivehi_labels_are_indexed_too(taxonomy):
    c = Category.objects.get(key="phone_charger")
    c.label_dv = "ޗާޖަރު"
    c.save()
    g = build_gazetteer()
    assert "phone_charger" in g.tokens["ޗާޖަރު"]


@pytest.mark.django_db
def test_structural_words_are_not_tokens(taxonomy):
    """'accessories', 'other', 'general' name the structure. Indexing them makes
    every query match every node."""
    g = build_gazetteer()
    for stop in ("accessories", "parts", "other", "general", "for", "sale"):
        assert stop not in g.tokens


@pytest.mark.django_db
def test_brands_come_from_documentspec(taxonomy):
    g = build_gazetteer()
    assert "apple" in g.brands


@pytest.mark.django_db
def test_the_gazetteer_is_cached_and_invalidated_on_taxonomy_change(taxonomy):
    from search.understand.gazetteer import gazetteer, invalidate
    a = gazetteer()
    assert gazetteer() is a
    Category.objects.create(key="new_node", label_en="Widgets", tier="primary")
    invalidate()
    assert "widgets" in gazetteer().tokens


@pytest.mark.django_db
def test_an_empty_taxonomy_yields_an_empty_gazetteer_not_a_crash(db):
    assert build_gazetteer().tokens == {}
```

- [ ] **Step 2** Run — expect FAIL.

- [ ] **Step 3** Implement. Tokenize `label_en`, `label_dv` and `aliases` per
node; normalize through P2's `normalize_text` so Thaana and Latin land in one
index. Stopwords come from family and tier labels plus a small fixed set. Cache
on a module global, `invalidate()` on `post_save` of `Category` and after
`reindex`.

- [ ] **Step 4** `rebuild_gazetteer` prints per-bucket counts so corpus drift is
visible. Add to the scheduled jobs table after each reindex.

- [ ] **Step 5** `jj commit -m "P10 task 3: gazetteer from the taxonomy"`

---

### Task 4: Curated linguistic knowledge

**Files:** Create `search/understand/markers.py`. Modify `search/models.py`, `search/admin.py`. Test `search/tests/test_markers.py`.

Only what neither the taxonomy nor the corpus supplies: intent, condition and
price modifiers, in all three input modes of spec 6. Node synonyms live on
`Category.aliases` (task 3), not here — one place per kind of knowledge.

```python
class QueryMarker(models.Model):
    """Query vocabulary that no amount of corpus structure supplies. Spec 6.6.

    Intent, condition and price modifiers. Category synonyms live on
    Category.aliases instead, so a node's vocabulary travels with the node.

    Measured: the Latin-Dhivehi forms dominate this corpus -- `dhinun` 152,
    `bahattan` 43, `kuyyah` 10 -- which is the shared-accommodation vocabulary
    of spec 4.3.1. An English-only modifier set would be half a feature.
    """

    KINDS = [("intent", "listing intent"), ("condition", "condition"),
             ("price", "price modifier")]
    term = models.CharField(max_length=64)
    script = models.CharField(max_length=8)          # latin | thaana | keys
    kind = models.CharField(max_length=16, choices=KINDS)
    target = models.CharField(max_length=128)        # field=value | sort=...
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("term", "kind")
```

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_latin_dhivehi_intent_is_recognised(markers):
    """`kudhin bahattan` -- shared accommodation, spec 4.3.1. 43 titles."""
    m = resolve_markers(["kudhin", "bahattan"])
    assert any(x.target == "unit_kind=bed_space" for x in m)


@pytest.mark.django_db
def test_the_same_intent_in_thaana(markers):
    assert any(x.target == "listing_kind=rent" for x in resolve_markers(["ކުއްޔަށް"]))


@pytest.mark.django_db
def test_keyboard_space_reaches_the_same_marker(markers):
    """`kuwqyaSq` decodes to ކުއްޔަށް. All three input modes of spec 6 must
    reach one marker or the vocabulary serves a third of users."""
    from search.lang.keymap import decode_keys
    assert resolve_markers([decode_keys("kuwqyaSq")])


@pytest.mark.django_db
def test_a_price_modifier_reorders_and_never_filters(markers):
    """'cheap iphone' must not hide expensive phones."""
    m = resolve_markers(["cheap", "iphone"])
    assert any("sort=" in x.target for x in m)
    assert not any("price<" in x.target for x in m)


@pytest.mark.django_db
def test_inactive_markers_are_ignored(markers):
    QueryMarker.objects.filter(term="cheap").update(is_active=False)
    assert not any(x.kind == "price" for x in resolve_markers(["cheap"]))
```

- [ ] **Step 2-3** Implement, migrate, admin, and a `seed_query_markers` command
carrying the measured vocabulary. Generate every keyboard-space form with P2's
`decode_keys` rather than typing it, so the three scripts cannot drift.

- [ ] **Step 4** `jj commit -m "P10 task 4: curated query markers"`

---

### Task 5: Parse the query

**Files:** Create `search/understand/parse.py`. Modify `search/lang/expand.py`. Test `search/tests/test_parse.py`.

**Interfaces:** `Interpretation(entities, nodes, markers, constraints, residual)`;
`interpret(tokens) -> Interpretation`; `QueryPlan.interpretation`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_a_bare_entity_yields_an_entity_and_no_node(taxonomy):
    """'iphone' -- the entity is the product family, no modifier narrows it."""
    i = interpret(["iphone"])
    assert i.entities == ["apple"] or "iphone" in i.residual
    assert i.nodes == []


@pytest.mark.django_db
def test_entity_plus_modifier_selects_the_node(taxonomy):
    """The reported defect, as a parse. The entity stays; the modifier shifts
    which node is wanted."""
    i = interpret(["iphone", "charger"])
    assert "phone_charger" in i.nodes


@pytest.mark.django_db
def test_a_two_word_modifier_is_matched(taxonomy):
    i = interpret(["iphone", "screen", "protector"])
    assert "screen_protection" in i.nodes


@pytest.mark.django_db
def test_numeric_attributes_become_constraints(taxonomy):
    i = interpret(["iphone", "128gb"])
    assert any(c.key == "storage_gb" and c.value == 128 for c in i.constraints)


@pytest.mark.django_db
def test_a_price_bound_is_parsed(taxonomy):
    i = interpret(["iphone", "under", "10000"])
    assert any(c.key == "price" and c.hi == 10000 for c in i.constraints)


@pytest.mark.django_db
def test_unmatched_words_stay_in_residual_for_lexical_search(taxonomy):
    """Query understanding never swallows the query. Whatever is not recognised
    must still reach the tsquery, or a parse gap becomes a recall loss."""
    i = interpret(["iphone", "qwertyuiop"])
    assert "qwertyuiop" in i.residual


@pytest.mark.django_db
def test_interpretation_is_attached_to_the_query_plan(taxonomy):
    from search.lang import build_query_plan
    plan = build_query_plan("iphone charger")
    assert plan.interpretation is not None
    assert plan.terms_en, "lexical terms must survive interpretation"
```

- [ ] **Step 2** Run — expect FAIL.

- [ ] **Step 3** Implement. Longest-match-first over the gazetteer across token
n-grams up to 3, so `screen protector` beats `screen`. Everything unmatched goes
to `residual` and still becomes lexical terms — **interpretation augments the
query, it never replaces it.** Numeric constraints reuse P7's
`extract_units` and `SpecKey` unit vocabulary rather than a second parser.

- [ ] **Step 4** `jj commit -m "P10 task 5: query interpretation"`

---

### Task 6: Resolve to filters and ranking hints

**Files:** Create `search/understand/resolve.py`. Modify `search/query.py`, `beynunehcheh/settings.py`. Test `search/tests/test_resolve.py`.

The policy layer, and the one that fixes the reported defect.

| Query shape | Resolution |
|---|---|
| entity, no node | boost the **primary** node of the entity's family; demote accessory / part / service |
| entity + node | boost that node; no tier demotion |
| node only (`charger`) | boost that node across all families |
| markers | apply as soft filters (`listing_kind`, `condition`) or a sort (`price`) |
| constraints | apply as P5 range filters |

**Boost, do not filter, by default.** A category inference reorders; it only
becomes a filter above a confidence threshold, and even then it is removable.
An inference that silently hides results is worse than the ranking bug it fixes.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_iphone_returns_phones_above_accessories(mixed_corpus):
    """The reported defect. Before: zero phones in the top 12; real phones at
    ranks 13, 30, 38."""
    page = search_page("iphone", doc_type="shopping", per_page=10)
    tiers = [r.card.get("category_tier") for r in page.results[:5]]
    assert tiers.count("primary") >= 3


@pytest.mark.django_db
def test_iphone_charger_returns_chargers(mixed_corpus):
    """The same mechanism, opposite outcome. The entity did not change; the
    modifier moved which node is wanted."""
    page = search_page("iphone charger", doc_type="shopping", per_page=10)
    assert any(r.card.get("category_key") == "phone_charger"
               for r in page.results[:3])


@pytest.mark.django_db
def test_an_inference_reorders_rather_than_hides(mixed_corpus):
    """Accessories must still be reachable for a bare entity query."""
    page = search_page("iphone", doc_type="shopping", per_page=50)
    assert any(r.card.get("category_tier") == "accessory" for r in page.results)


@pytest.mark.django_db
def test_the_inference_is_reported(mixed_corpus):
    page = search_page("iphone", doc_type="shopping")
    assert page.interpretation["boosted_tier"] == "primary"


@pytest.mark.django_db
def test_an_inference_can_be_turned_off_per_request(mixed_corpus):
    """The removable half. The UI needs a URL that means 'show me everything'."""
    page = search_page("iphone", doc_type="shopping", understand=False)
    assert page.interpretation is None


@pytest.mark.django_db
def test_a_document_with_no_category_is_neither_boosted_nor_buried(mixed_corpus):
    """Most gazette documents. An absent category means no signal, not a
    penalty."""
```

- [ ] **Step 2** Run — expect FAIL on the first two.

- [ ] **Step 3** Implement. Add `w_category_tier` and `w_category_node` to
`SEARCH_RANKING`; join `Category` in the candidate CTE for `tier` and `key`;
add the boost terms to the score expression. Carry `category_key` and
`category_tier` into `card` so the tests above and the UI can read them.

- [ ] **Step 4** Tune the two new weights with `tune_ranking` (P8 task 4) against
the eval set. Record before and after.

- [ ] **Step 5** `jj commit -m "P10 task 6: resolve interpretation into ranking"`

---

### Task 7: Show the user what was inferred

**Files:** Modify `api/schemas.py`, `api/routers/search.py`. Create `web/src/components/InterpretationNotice.tsx`. Test `tests/api/test_interpretation.py`, the web test.

`InterpretationOut` on the response: recognised entity, selected node, applied
markers and constraints, each with a URL that drops it. `InterpretationNotice`
renders it above the results — *"Showing **phones** for iphone · show all
categories"* — as an inline notice, not an overlay, per the P9 task 9 invariant.

- [ ] **Step 1** Tests: the field is present and populated; every inference
carries a removal URL; disabling understanding returns null; a query with no
inference renders nothing.

- [ ] **Step 2-4** Implement, wire into `SearchShell` next to
`RelaxationNotice`, commit.

---

### Task 8: Eval cases and measurement

- [ ] **Step 1** Add to `search/eval/queries.yaml`, each asserting the tier or
node rather than a document id, so the case survives a reindex:

```
iphone                -> tier=primary
iphone case           -> node=cases_protection_skins
iphone charger        -> node=phone_charger
iphone screen protector -> node=screen_protection
iphone repair         -> tier=service
samsung battery       -> tier=part
kuyyah                -> listing_kind=rent
kudhin bahattan       -> unit_kind=bed_space
ކުއްޔަށް               -> listing_kind=rent
laptop charger        -> tier=accessory
washing machine       -> tier=primary
```

Cross-script cases are the point: the same intent must resolve identically from
Thaana, Latin-Dhivehi and keyboard-space input.

- [ ] **Step 2** `eval_search` before and after; recall@5 >= 0.80 still gates.

- [ ] **Step 3** Write `docs/superpowers/measurements/2026-08-p10-understanding.md`:

| Metric | Before | After |
|---|---|---|
| `iphone` primary-tier results in top 5 | 0 | |
| `iphone charger` charger results in top 3 | | |
| documents with a category | | |
| classified by model vs mapped by adapter | | |
| unclassified queue depth | | |
| recall@5 / MRR | | |
| p95 latency (gazetteer adds a join) | | |

- [ ] **Step 4** `jj commit -m "P10 task 8: eval cases and measurements"`

---

## Self-Review

**The correction that shaped this plan.** An earlier draft derived the gazetteer
directly from iBay's `category_path` and read the tier from literal
`Accessories`/`Parts` segments. That works only for iBay — gazette has no
categories and a future source may have flat or wrong ones. The taxonomy is now
canonical and source-independent; iBay's structure is used once, to *seed* it.

**How a new source joins.** Add the adapter (spec 3.1). If it has categories,
add a mapping. If it does not, `classify_categories` runs the enrichment pass and
the model picks from the registry. Nothing about query parsing changes, because
query parsing never reads a source's own taxonomy.

**The registry is closed, deliberately.** The model selects a `Category.key` or
returns null; an invented key is dropped and recorded, exactly like an ungrounded
number (spec 5.2 layer 0) and exactly like an unpromoted `SpecKey` (spec 4.4).
Unclassified documents rank by frequency in a review queue, which is how the
taxonomy grows.

**Supersedes P9 task 4b.** The `CategoryKind` model proposed there hand-curated
the accessory/primary distinction. `Category.tier` covers it for every source,
so P9 task 4b should be struck and 4a kept — category diversity in the result
page remains useful as a safety net when no inference fires.

**Deliberately out of scope.** Learning the entity-to-node association from click
data (spec 16.2) needs ~10,000 clicks. Embedding-based matching (16.1) would
subsume much of this and remains the better long-term answer; this is the
deterministic, inspectable interim.
