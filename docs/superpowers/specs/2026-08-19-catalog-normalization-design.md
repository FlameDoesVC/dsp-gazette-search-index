# Catalog Normalization Design

Date: 2026-08-19
Status: approved, awaiting implementation plan
Supersedes: nothing. Absorbs P10 tasks 1 and 2 (see section 17).

## 1. Goal

Seller-written listings are the weakest input in the system. Titles carry phone
numbers and marketing, descriptions repeat themselves, structured attributes are
absent on most rows, and iBay's own category tree is ambiguous. The result looks
like the listings it came from.

This project inserts a canonical entity layer between source documents and
display. One entity per real-world thing, many documents linked to it, and a
normalized profile on the entity carrying per-field provenance. A correction
made once fixes every listing behind it.

## 2. Scope

In:

- iBay `For Sale` (7,105 listings) and `Services` (9,173 listings).
- A canonical category taxonomy, source-independent, plus a deterministic
  mapping from iBay's 278 paths into it.
- AI-assembled entity profiles with per-field provenance.
- Deterministic phone extraction across all 20,445 documents.
- A crowdsourced correction endpoint that auto-applies on agreement.

Out:

- GTIN/UPC retrieval. Considered and rejected; see section 3.
- Gazette document linking. This project builds the foundation it needs
  (section 16); the gazette resolution strategy is its own project.
- A `service` doc_type and a services tab. Services keep `doc_type="shopping"`
  and get a card variant. Promotion later is a reclassification UPDATE plus a
  tab, which spec 3.2 already designs for.
- P10 tasks 3 through 8 (query parsing). They consume what this project builds.

## 3. Why not UPC retrieval

The first design for this work resolved each product to a GTIN by scraping
search engines, then pulled a spec sheet from a barcode catalog. It was probed
against the live corpus and against the real endpoints before being dropped.

What the probe established:

- DuckDuckGo's `lite` and `html` endpoints answer 200 with usable snippets.
- Barcode-index results carry the GTIN in the URL path
  (`upcindex.com/887276812199`, `upcitemdb.com/upc/887276816241`), so GTIN
  harvesting is deterministic and check-digit verifiable.
- `api.upcitemdb.com/prod/trial/lookup?upc=` returns full product records on
  the free tier.
- Direct fetches of arbitrary result pages return 403. Both `greenlion.net` and
  `upcindex.com` refused. The pipeline could not rely on reading candidate
  pages, only SERP snippets.
- iBay's own listing outranks the manufacturer for its own product, so the
  source domain has to be excluded from every SERP.

None of that was fatal. The reason for dropping it is coverage. The corpus is
dominated by locally branded and unbranded goods and by services, and the
free-tier lookup quota is roughly 100 per day. A GTIN path would have delivered
verified specs for a minority of `For Sale` listings, on a timeline of weeks,
and nothing at all for the 9,173 service listings.

The accepted trade is stated plainly: a model that normalizes what we already
hold will sometimes show a fact that is wrong, where GTIN retrieval would have
shown nothing. Crowdsourced correction is the answer to that, and correcting a
visible error is a tractable problem in a way that filling 16,000 empty spec
sheets is not.

## 4. Measured evidence

All figures from the live corpus on 2026-08-19, 20,445 documents, iBay only.

Phone numbers are nearly universal and free to extract:

```
listings with a phone in title or summary   14,839 of 16,608   89.3%
```

Service listings collapse hard, and the corpus is dominated by a handful of
advertisers:

| grouping | listings | groups | collapse |
|---|---|---|---|
| Services by (seller, leaf) | 9,173 | 1,495 | 6.13:1 |
| For Sale by crude identity key | 7,105 | 3,802 | 1.87:1 |

```
one seller  ("Miabulbul")   about 2,971 listings
one phone   (7438649)             1,680 listings, 10% of the corpus
```

The product-side collapse is weak because most products are singletons:

```
group size 1   2,824 groups     group size 2   531     3   158     4   87
```

74% of product groups are singletons, so cross-listing consensus can cover only
about a quarter of products. Inferred, model-supplied specs carry the rest. That
is the reason inferred specs are filterable rather than display-only: made
display-only, the facet substrate would stay empty for the 4,792 `For Sale`
listings that have no scraped `Brand`.

The existing `dedupe_key` does not do this job. It is a repost key over
`(source, seller, normalized title, price)` (`search/dedupe.py`), it has flagged
7,959 rows, and it catches "the same ad posted twice", not "twenty sellers
listing the same charger". Entity grouping is coarser and orthogonal; both stay.

iBay's category tree cannot be used as it stands:

```
documents with an empty category_path                  188
documents on an information-free leaf                1,541
  ("General / Other" 1,250, "Other" 291)
path depth ranges from 1 to 5   (348 documents are root-only)
distinct leaves                                        278
leaf labels that are ambiguous across families            9
```

The ambiguity is the important one, because `SearchDocument.category_leaf` is a
bare string and both P9's category-aware ranking and section 8.3's facet
priority override key on it:

```
Charger        <- For Sale > Mobile Phones & Accessories > Accessories
Charger        <- For Sale > Computer, Tablets & Networking > Laptop Accessories
Car Accessories <- For Sale > Mobile Phones & Accessories > Accessories
Car Accessories <- For Sale > Motorcycle, Cars & Vehicles
```

Phone chargers and laptop chargers currently share one ranking and faceting
bucket. Mapping to a canonical taxonomy keyed on the full path fixes that as a
side effect.

One leaf, `Dhonna Machine Maraamathukurun` (644 documents), is romanized
Dhivehi sitting inside an otherwise English tree, so the tree is not even
internally consistent about language.

## 5. The canonical taxonomy

`search.Category` as specified by P10 task 1: a curated, source-independent
tree with `key`, bilingual labels, `parent`, `tier`
(`family|primary|accessory|part|service`), `doc_type`, `aliases`, `is_active`.
The tier is curated per node rather than parsed from a path segment, because
`Accessories` is a literal segment in exactly one source's paths.

This project adds the piece P10 left implicit, which is how a source's own
categories reach that tree:

```python
class SourceCategoryMap(models.Model):
    """One row per distinct source category path. Keyed on the full path, not
    the leaf: iBay spells 'Charger' under two different families and 'Car
    Accessories' under two more, and a leaf-keyed map would merge them."""

    source = models.CharField(max_length=32)
    path = models.JSONField()                 # ["For Sale", "Mobile...", ...]
    path_key = models.CharField(max_length=64) # sha256 of the joined path
    category = models.ForeignKey("search.Category", null=True, blank=True,
                                 on_delete=models.SET_NULL)
    note = models.CharField(max_length=256, blank=True)
```

Rules:

- The map is seeded by a command that walks the distinct paths in the corpus and
  proposes a canonical node, printing every proposal for human review. The
  derivation seeds; it is not the contract.
- An information-free leaf (`General / Other`, `Other`) maps to its parent's
  canonical node, not to a canonical junk node. A category that says nothing is
  worse than a category one level too general.
- `category = NULL` is a legal, expected row. It means "no canonical category
  for this path", and the document then has no category.
- A path absent from the map, and the 188 documents with no path at all, fall
  through to model classification, which picks an existing `Category.key` or
  returns nothing. The model never creates a category, exactly as it never
  invents a digit.

`SearchDocument.category` becomes an FK (`db_constraint=False`, nullable, since
the table is partitioned). `category_leaf` stays, and is populated from the
canonical node's `label_en` rather than from the source path, so P9's ranking
code and section 8.3's facet override keep working unchanged while the bucket
they key on stops being ambiguous.

`Entity.category` is an FK to the same table. No entity carries a raw source
path.

## 6. Data model

### 6.1 `catalog.Entity`

One row per real-world thing.

```python
kind             "product" | "service"
key              unique, deterministic (section 7). Re-resolution is a no-op.
brand            product identity
model_name
variant
service_type     service identity
provider_key
category         FK to search.Category, nullable
title_en/dv      the normalized display title
summary_en/dv
identity_confidence   float
profile_status   "pending" | "ok" | "needs_review" | "failed"
listing_count    denormalized
created_at / updated_at
```

### 6.2 `catalog.EntityLink`

```python
entity        FK
source        CharField
source_key    CharField
method        "identity_match" | "seller_service" | "manual"
confidence    float
```

Unique on `(source, source_key)`: a document links to at most one entity.

The link stores `source` and `source_key` rather than a document FK, following
`EnrichedRecord`'s stated reason. `SearchDocument` is LIST-partitioned, and
links have to survive a full reindex that drops and rebuilds those rows.

### 6.3 `catalog.EntityField`

The provenance-tagged value store.

```python
entity        FK
key_raw       CharField                  # "storage_gb", "brand", "phone"
key           FK to search.SpecKey, nullable   (mirrors DocumentSpec)
value_num     FloatField, nullable
value_text    CharField
unit          CharField
provenance    "scraped"|"correction"|"consensus"|"grounded"|"inferred"
confidence    float
support_count int                        # linked listings supporting it
is_winner     bool
```

Unique on `(entity, key_raw, provenance, value_num, value_text)` with
`nulls_distinct=False`, the same trick `DocumentSpec` uses.

Every candidate value is kept and the winner is flagged. A correction beats an
inference without destroying the evidence, and the retained trail is what makes
a bad auto-apply diagnosable after the fact.

### 6.4 `catalog.FieldProposal`

```python
entity            FK
key_raw           CharField
value_num / value_text / unit     the proposed value; all empty means "wrong,
                                  drop this field"
proposer_ip_hash  CharField
status            "pending" | "applied" | "rejected" | "conflicted"
created_at
```

Unique on `(entity, key_raw, value_num, value_text, proposer_ip_hash)`, so one
IP hash counts once per value.

### 6.5 Column additions

- `SearchDocument.contact_phone`, indexed. One number covers 1,680 listings, so
  making "same advertiser" groupable is worth a column.
- `SearchDocument.category`, FK, nullable, `db_constraint=False`.
- `DocumentSpec.provenance`, so facet discovery can include inferred values and
  the API can mark them.

## 7. Entity resolution

Fully deterministic. No model call.

### 7.1 Products

1. Clean the title: strip phone numbers with the existing `_PHONE` pattern from
   `enrich/preextract.py`, strip prices, strip marketing tokens
   (`FREE DELIVERY`, `CALL`, `VIBER`, `WHATSAPP`, `ORDER NOW`).
2. Brand comes from the scraped `Brand` info field where present (2,313
   listings), else from a brand vocabulary matched against the cleaned title.
   The vocabulary is seeded from the 35 brands already in `DocumentSpec` and
   grown through admin.
3. Model tokens are alphanumeric tokens carrying a digit: `RL-S07100C`, `A15`,
   `128GB`.
4. `key = sha256(brand | sorted model tokens | mapped category key)`.

`mapped category key` is the canonical key from `SourceCategoryMap` alone, and
the empty string when the path is unmapped. Model classification (section 8)
fills `Entity.category` afterwards but never participates in the key. This is
not a detail: the map is deterministic and available before resolution runs,
while classification is not, so admitting the classified category into the key
would make the key depend on a model call and destroy the idempotency section 13
requires.

A listing with neither a known brand nor a model token gets no entity. It
renders as it does today, plus its phone. This is a deliberate miss, not a
fallback into invention.

The prototype run behind section 4 reported a 0% miss rate, but only because it
fell back to treating the first surviving title token as the brand, which is too
weak to ship. With brand drawn from a vocabulary, expect a real miss rate
between 15% and 30%. The implementation plan measures it rather than assuming
it.

### 7.2 Services

`key = sha256("service" | provider_key | service_type)`.

`provider_key` is the primary phone when the listing has one, else the seller
id. The phone is the better provider identity and the corpus says so: 781
distinct phones against 946 seller accounts in Services means one operator
posts under several accounts, and the phone merges them.

`service_type` is the mapped canonical key, under the same restriction as
products: from `SourceCategoryMap` only, empty when unmapped, never from
classification. A listing with no phone, no seller and no mapped category gets
no entity.

A listing carrying several phones takes the first one appearing in the title,
else the lowest-sorting one. The tie-break is arbitrary but it has to be fixed
in code, because a provider key that varies between runs would split one
provider into several entities on every pass.

## 8. Profile assembly

Stage 1, the existing per-document enrichment, is unchanged.
`enrich/validate.py`'s grounding invariant is not weakened anywhere in this
design.

Stage 2 is new and runs per entity, through the existing `EnrichClient`, so it
inherits the provider chain, escalation and repair loop. It needs a new prompt
and a new output schema, not new infrastructure.

Input: the entity identity, plus the union of its linked listings' titles and
scraped info fields, capped by `ENRICH_MAX_INPUT_CHARS`.

Output: a normalized title, a summary, a canonical category key chosen from the
registry, and a spec sheet in which the model tags every spec `from_listings` or
`from_knowledge`.

The tag is then checked rather than trusted:

- Claimed `from_listings`, passes the existing grounding validator against the
  union text: stored as `grounded`.
- Claimed `from_listings`, fails: demoted to `inferred`, not dropped.
- Claimed `from_knowledge`: stored as `inferred`, unvalidated.

So the validator takes on a second job. It stops being a gate that deletes
everything absent from the text and becomes the classifier between trust tiers.
A model that lies about where a fact came from loses the fact's trust level, not
the fact.

`consensus` is computed afterwards, deterministically, never by the model: a
`grounded` value appearing on two or more listings from different sellers under
the same entity is promoted to `consensus`.

### 8.1 Product profile

```
title_en/dv, summary_en/dv, category_key
brand, model_name, variant
specs[]   key_raw, value_num, value_text, unit, origin
```

`origin` is the `from_listings` / `from_knowledge` tag described above. Spec keys
are matched against the `SpecKey` registry by the existing
`search/specs/normalize.py` path, so a product profile cannot promote a new
facet; that asymmetry is spec 4.4's and it stands.

### 8.2 Service profile

Services are the larger half of the corpus and they need their own shape,
because a spec sheet is the wrong model for them:

```
title_en/dv, summary_en/dv, category_key
service_type          the canonical node's label
services_offered[]    "aircon servicing", "fridge repair", "wiring"
coverage[]            "Male'", "Hulhumale'", "airport"
call_out              bool | null    does the provider come to you
shop_visit            bool | null    is there premises to visit
rate_basis            "per_job" | "per_hour" | "per_visit" | "quote_only"
availability          free text, grounded only
```

Every one of those fields runs through the same origin tagging and the same
grounding check. `coverage` in particular is high-value and cheap: the delivery
and service-area sentences quoted in section 4 are boilerplate the seller
already wrote, and they currently reach nothing but the search vector.

A service entity's `services_offered` is what makes the 6.13:1 collapse safe.
Collapsing 835 listings to one provider entity would lose information if the
listings described different work; carrying the union of the work described
keeps it.

## 9. The provenance ladder

```
scraped  >  correction  >  consensus  >  grounded  >  inferred
```

The highest tier present for a `key_raw` wins and is marked `is_winner`.
`scraped` sits on top to preserve the existing rule that a source's own
structured field is never overwritten.

A tie inside one tier goes to `support_count`. If that is also tied, the field
gets **no winner at all** and the entity is flagged `needs_review`: an
unresolvable conflict shows nothing for that field rather than picking a side by
row order. A field with no winner writes no `DocumentSpec` row, so it cannot be
filtered on either.

Inferred values are filterable, not display-only. Every result whose winning
values include an inferred one is marked, and the facet response marks facets
that contain inferred data, so a narrowed result set discloses that it may
contain a wrong match. This is the accepted risk from section 3, made visible
rather than hidden.

## 10. Crowdsourced corrections

`CATALOG_PROPOSAL_QUORUM` distinct IP hashes proposing the same normalized
value auto-applies it as a `correction`-tier `EntityField`, which then wins for
every listing linked to that entity.

Two competing values both attracting support flip the field to `conflicted`:
nothing applies, the field falls back to the next tier, and the field lands in
the admin queue. Quorum and margin are settings, so tightening them after the
first abuse costs a config change.

Rate limiting counts over the proposals table itself, reusing the reasoning in
`api/ratelimit.py`: the production stack runs three gunicorn workers, so an
in-process limiter would grant three times the intended budget, and adding
Redis for one counter is not worth a service.

The risk, stated rather than papered over: a quorum over IP hashes is
defeatable by anyone with a phone hotspot and patience. The mitigations are the
retained audit trail, revertibility, and the admin queue. Prevention is not
among them, and this is the trade the project accepted when it chose
auto-apply over an approval queue.

The existing `DocumentReport` endpoint keeps its current behaviour and stays
inert. Reports are about a document being stale or dead; proposals are about a
field being wrong.

## 11. Integration with search

- `enrich/overlay.py` gains an entity pass after `apply_enrichment`: normalized
  title and summary, entity specs, `card["phone"]`, `card["entity_id"]`,
  `card["profile_tier"]`.
- `search/specs/project.py` gains a fourth input alongside the unit extractor,
  `attrs['specs']` and `attrs['specs_raw']`: the winning `EntityField` rows,
  written to `DocumentSpec` with their `provenance`.
- Facet discovery in `search/facets.py` is untouched. Inferred values flow
  through the existing thresholds, and the API response gains a marker.
- Services select a card variant by `entity.kind` while keeping
  `doc_type="shopping"`.

### 11.1 API

```
GET  /api/documents/{id}            gains entity_id, and each spec item gains
                                    provenance
GET  /api/entities/{entity_id}      the profile: identity, category, fields with
                                    provenance and support_count, listing_count
POST /api/entities/{entity_id}/propose
       {key_raw, value_num?, value_text?, unit?}   all values empty means
       "this field is wrong"
       -> 202 always, like the report endpoint: the caller learns nothing about
          quorum state, duplicate detection or rate limiting
GET  /api/search                    facet entries gain has_inferred, and result
                                    items gain profile_tier
```

`POST .../propose` returns 202 unconditionally, following the report endpoint's
existing reasoning: a public endpoint that reports whether a proposal was
accepted, deduplicated or throttled is an oracle for probing the quorum, and the
caller has no legitimate use for the difference.

## 12. Phone extraction

Deterministic, all document types, no model call. The primary phone is the
first `_PHONE` match in the title, else the first in the description; 89.3%
coverage measured. It populates `contact_phone`, feeds the card as a tap-to-call
action, supplies the service provider identity in section 7.2, and is stripped
from the displayed normalized title.

## 13. Error handling and idempotency

- Resolution miss: no entity, document unchanged.
- Stage-2 failure: `profile_status="failed"`, listings render from grounded data
  only. Indexing never blocks on enrichment (spec 5.2).
- Entity keys are deterministic, so re-resolution is a no-op. An entity that
  loses every link keeps `listing_count=0` and is retained (spec 12.6: nothing
  is deleted).
- Corrections survive re-profiling because they occupy a higher tier than
  anything stage 2 can write.
- A `SourceCategoryMap` row pointing at a deleted category resolves to NULL,
  which is a legal state, not an error.

## 14. Testing and measurement

Unit:

- Title cleaning against real corpus samples, including the stuffed-title and
  marketing-block cases quoted in section 4.
- Blocking-key stability: the same listing yields the same key across runs, and
  a reposted listing joins the same entity.
- Ladder precedence, including that `scraped` beats `correction`.
- Quorum policy: reaching quorum applies, a competing value conflicts, one IP
  cannot reach quorum alone.
- That a `from_listings` claim failing grounding is demoted to `inferred` rather
  than dropped. This is the behaviour the whole coverage argument rests on.
- Path-keyed category mapping: the two `Charger` paths resolve to different
  canonical nodes.

Integration:

- resolve -> profile -> overlay -> index -> the facet response contains the
  inferred value and marks it.
- A proposal reaching quorum changes the displayed value on every linked
  listing.

Gating measurement: a hand-labelled 50-listing golden set for entity resolution
precision. A wrong link puts wrong specs on a real listing, so that number
gates the backfill rather than being reported after it.

Measurement document, per project convention: entity counts, collapse ratios,
spec counts by provenance tier, resolution miss rate, facet coverage on
`For Sale` before and after, and stage-2 call count and wall clock.

## 15. Cost

Stage 2 is priced per entity, not per document:

```
product entities  about 3,802        service entities  1,495
total                             about 5,300 calls
```

against 16,608 documents, so the entity layer is cheaper than a second
per-document pass by roughly a factor of three. Section 4's collapse ratios are
what produce that. No dollar figure is quoted here because the P4 measurement
document records wall clock but not per-token cost; the plan records both.

## 16. What this buys gazette

`EntityLink` is keyed on `(source, source_key)` with a `method` field, so it is
source-agnostic already. Gazette linking becomes one new resolution strategy:
key an A2 sheet or a natheejaa sheet to the reference number of a prior iulaan
and write the same link rows. No new tables, no schema change, and the
correction and provenance machinery comes along unchanged.

## 17. Relationship to P10

This project absorbs P10 task 1 (the canonical `Category` taxonomy) and task 2
(getting documents into it), because entity categories require them and two
competing taxonomy designs would be worse than one. `SourceCategoryMap`
(section 5) is an addition to what P10 task 1 specified.

P10 tasks 3 through 8 are unaffected in intent and now depend on this project.
The gazetteer they build reads `Category` labels and aliases, which is what P10
task 3 already assumed.

## 18. Open seams

- Services stay `doc_type="shopping"`. If a services tab is wanted later, spec
  3.2's reclassification path covers it.
- The brand vocabulary starts at 35 entries and needs to grow before product
  resolution coverage is good. Growing it is admin work, and the resolution
  miss rate is the metric that says how urgent it is.
- Inferred specs are filterable from day one. If the marked-result disclosure
  proves insufficient in practice, the confidence floor for promoting inferred
  values into `DocumentSpec` is a setting, not a redesign.

## 19. Implementation order

The dependency chain is strict at the front and parallel at the back:

1. Canonical `Category` plus `SourceCategoryMap`, seeded and reviewed. Nothing
   else can be keyed until the taxonomy exists.
2. Deterministic phone extraction and `contact_phone`. Independent of everything
   else, immediately visible, and section 7.2 depends on it.
3. Title cleaning and identity extraction (`catalog/identity.py`).
4. Entity resolution and links, with the golden-set precision measurement
   gating the backfill.
5. Stage-2 profiles and the provenance ladder.
6. Projection into `DocumentSpec`, overlay, cards, facet markers.
7. Proposals: model, policy, endpoint, admin queue.

Steps 1 and 2 are worth landing before the rest is written, because both are
deterministic, both are measurable on their own, and step 1 fixes the ambiguous
`Charger` bucket in P9's ranking whether or not the entity layer ever ships.

## 20. Settings

```
CATALOG_PROPOSAL_QUORUM        distinct IP hashes required to auto-apply
CATALOG_PROPOSAL_MARGIN        lead required over a competing value
CATALOG_PROPOSAL_RATE_LIMIT    proposals per IP hash per window
CATALOG_PROPOSAL_RATE_WINDOW   seconds
CATALOG_CONSENSUS_MIN_SELLERS  distinct sellers required to promote to consensus
CATALOG_INFERRED_MIN_CONFIDENCE  identity confidence floor for writing inferred
                                 values into DocumentSpec
```
