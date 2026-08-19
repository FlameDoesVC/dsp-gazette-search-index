# Catalog normalization, measured

Date: 2026-08-19
Plan: `docs/superpowers/plans/2026-08-19-catalog-normalization.md`
Spec: `docs/superpowers/specs/2026-08-19-catalog-normalization-design.md`
Profile model: `deepseek-v4-flash` via `EnrichClient`, `PROFILE_PROMPT_VERSION` 1

**Read the scope before the numbers.** Only the **service** half was profiled.
Products were deliberately left unprofiled after a 10-entity sample, because
task 5's holdout put product resolution precision at 5/7 while services scored
29/29. Every product figure below is therefore resolution-only.

## 0. Two snapshots, and which one to read

A live `sync_ibay` ran throughout, so the corpus moved under the measurements.
`ibay_product` went 24,639 -> 33,917 and a reindex then pulled all of it into the
index. There are therefore two states, and mixing them produces nonsense.

**Snapshot A, 20,445 documents.** Coherent: resolved, services fully profiled,
merged, projected, deduped. Every detailed figure in sections 1 through 6 is from
this state, and it is the one that says whether the design works.

**Snapshot B, 33,917 documents.** The current state, mid-ingest:

| | A | B |
|---|---|---|
| indexed documents | 20,445 | 33,917 |
| with a phone | 18,869 (92.3%) | 30,101 (88.8%) |
| with a canonical category | 20,257 | 27,676 |
| unmapped | 188 | 6,241 |
| `Category` nodes / mappings | 366 / 306 | 453 / 390 |
| entity links | 11,886 | 14,447 |
| product entities | 1,845 | 3,311 (10 profiled) |
| service entities | 1,747 (all profiled) | 2,038 (1,747 profiled, 291 pending) |

The 6,241 unmapped in B are **link-only stubs**, not a taxonomy regression: a
product's categories come from its detail page, and `sync_product_links` creates
rows carrying only listing_id, name and url. They have no category data to map
yet. The same stubs are why resolution reports a 58.3% miss rate in B against
25.8% in A -- `in_scope` requires a category path.

B also holds 348 orphaned entities (`listing_count=0`), left behind when the
taxonomy grew and mapped-category keys changed. Nothing deletes them, per spec
12.6; they are inert and re-resolution does not recreate them.

Re-run the chain in section 7 before trusting any figure as current.

## 1. Taxonomy

| | count |
|---|---|
| distinct iBay paths | 306 |
| `Category` nodes created | 366 |
| `SourceCategoryMap` rows | 306 |
| documents mapped | 20,257 |
| documents still unmapped | 188 (the empty-`category_path` rows) |

Tier split after the corrected `infer_tier`: primary 163, service 70, accessory
42, part 25, family 6. Under the original exact-match rule it was primary 192,
accessory 33, part 5 -- 30 accessory and part nodes were claiming to be primary
products, including `Laptop Accessories > Charger`.

**The defect this fixes, measured.** 14 labels were shared by two nodes, and
`category_leaf` stores the label, so distinct keys were not enough:

```
before:  Charger  413 documents in one ranking bucket
after:   Charger (Mobile Phones & Accessories)  400
         Charger (Laptop Accessories)            13
```

No label is now shared by two document-carrying nodes.

## 2. Phones

| doc_type | with a phone | total | share |
|---|---|---|---|
| shopping | 14,848 | 16,608 | 89.4% |
| property | 2,967 | 3,497 | 84.8% |
| job | 120 | 335 | 35.8% |
| **all ibay** | **18,869** | **20,445** | **92.3%** |

1,601 distinct numbers. The largest single advertiser, `7438649`, holds 1,680
listings -- 10% of the shopping corpus. Jobs are low because an employer ad
carries an email, not a mobile.

The all-ibay figure exceeds the backfill's 17,938 because `_row` also scans
`text_en`, which the backfill command does not have access to.

## 3. Entities

| | listings | entities | collapse |
|---|---|---|---|
| product | 2,712 | 1,845 | 1.47:1 |
| service | 9,174 | 1,747 | 5.25:1 |
| **total** | **11,886** | **3,592** | |

Unlinked and why: 3,497 property and 335 job documents are out of scope, 188 have
no category path, 142 sit under Wanted / Business Opportunities / Free Stuff, and
about 4,400 For Sale listings carry no discriminating identity.

Product identity confidence, which gates whether inferred specs reach
`DocumentSpec` at the 0.7 floor: 87.4% of product entities clear it. Under the
plan's original both-or-nothing rule only 28.4% would have.

### 3.1 Resolution precision

| set | linked | correct | precision |
|---|---|---|---|
| tuning (`golden.yaml`), before the fix | 46 | 36 | 78.3% |
| holdout (`holdout.yaml`), after | 36 | 34 | **94.4%** |
| holdout, services only | 29 | 29 | 100% |
| holdout, products only | 7 | 5 | 71% |

Both sets were labelled by the agent that wrote the code, not by a second
reviewer. Treat the gate as self-certified; per-row reasoning is in the `note`
fields of both files.

The first run failed the 90% gate at 78.3%, entirely on products: brand-only
identity put 214 different Apple accessories in one entity, and the platform
token `PS5` put 291 different games in another. Requiring a discriminating token
fixed it at a real cost -- product listings linked fell from 6,062 to 2,712.

Token document frequency is what makes "discriminating" measurable:

```
PS5 426   PS4 266   5G 214   256GB 163   IP66 66   2IN1 19
WH-1000XM5 2   SQ905 1   DH-IPC-HFW1431S1 1   G06 1
distribution: p50=1  p75=3  p90=7  p95=13  p99=41   ->  threshold 15
```

Product precision rests on 7 labelled rows, so its error bars are wide. Both
surviving failures are version or generation tokens (`V18` grouping Quickbooks
with SketchUp; a dropped capacity merging 256GB and 512GB microSD) which
document frequency cannot catch, because such tokens are genuinely rare.

## 4. Profiles

Services only.

| | count |
|---|---|
| calls made | 1,747 |
| ok | 1,747 |
| failed | **0** |
| wall clock | about 58 minutes at `ENRICH_CONCURRENCY=8` |
| observed rate | about 0.5 entities/sec |

Zero failures is the retry ladder working, not the provider being flawless: the
log carries `deepseek-v4-flash: empty content` on individual attempts, absorbed
by `run_chain`.

`EntityField` rows by tier:

| tier | rows |
|---|---|
| grounded | 8,554 |
| inferred | 2,866 |
| consensus | 1,089 |
| correction | 0 (no crowd input yet) |
| scraped | 0 (services carry no scraped info fields) |

11,065 rows are marked winners. Two defects had to be fixed to get there, both
invisible to the tests:

- The ladder assumed one winner per `key_raw`, but `service_offered` and
  `coverage` are lists. 467 fields had no winner at all -- `service_offered` on
  342 entities, `coverage` on 149 -- so nothing projected and each entity was
  flagged for review. Winners went 586 -> 3,844 once the winning tier was
  allowed to win entire.
- `needs_review` was sticky. Fixing the above left 332 entities still flagged
  because nothing cleared the status.

**The prompt has to ask for world knowledge.** Measured across two providers
before rule 1a existed: DeepSeek volunteered **zero** `from_knowledge` specs
across five entities and a local 12B volunteered one. The `inferred` tier is the
entire reason the entity layer covers products that are the only listing of
themselves, and unprompted it stays empty.

## 5. Projection and facets

| provenance | `DocumentSpec` rows |
|---|---|
| consensus | 44,960 |
| grounded | 23,168 |
| extractor / pre-entity | 17,623 |
| inferred | 13,002 |
| **total** | **98,753** (was 17,605 before this project) |

15,023 documents carry at least one spec row. Distinct `key_raw` went from 26 to
69.

For Sale coverage is 7,091 of 7,105 documents, but **most of that is not the
entity layer**: the unit extractor and iBay's own `specs_raw` already covered
those rows before this project. The entity layer's contribution to For Sale is
small so far precisely because products were not profiled.

## 6. Cross-cutting effect: dedupe amplification

Not predicted by the plan and worth stating plainly. `dedupe_key` hashes the
normalized title, and the entity layer replaces seller titles with one canonical
title per entity, so reposts that previously differed now collide:

| | flagged duplicate |
|---|---|
| before this project | 7,959 |
| after | **9,499** |

Largest groups after: 717 listings under `Electrical Wiring and Repair
Services`, 369 under `Refrigerator, Aircon, Chiller & Freezer Repair Service`,
354 under `Washing Machine Repair and Installation Service`.

This is the intended direction -- one advertiser's near-identical reposts
collapsing into one result with `duplicate_count` carrying the total -- and the
distinct services survive on the entity rather than in 717 separate titles. But
it is a 46% duplicate rate on the ibay corpus, and `dedupe_listings` must now be
re-run after every reindex or the flag reflects pre-normalization titles.

## 7. Re-running the chain

Order is not a preference. `recompute_all` run while `build_profiles` is still in
flight leaves every entity profiled after it with no winning fields, which
projects no specs and shows an empty trust label. Observed: mid-pass it reported
3,844 winners and left 348 of 600 sampled entities blank.

```bash
python manage.py seed_taxonomy --source ibay --apply   # once, then review
python manage.py map_categories --source ibay
python manage.py backfill_phones --source ibay
python manage.py seed_brands
python manage.py resolve_entities --source ibay
python manage.py eval_entities --sample 50             # label, then score
python manage.py build_profiles --kind service         # costs money
# wait for it to exit
python -c "from catalog.merge import recompute_all; print(recompute_all())"
python manage.py reindex --source ibay
python manage.py sync_specs --source ibay --prune
python manage.py dedupe_listings --source ibay
python manage.py rebuild_suggest_terms
```

## 8. What this file settles

- **The inferred-spec confidence floor is set correctly.** 87.4% of product
  entities clear 0.7 under graded scoring; the both-or-nothing rule would have
  excluded 71.6%. No change needed.
- **The brand vocabulary needs growing, and the miss rate says how urgently.**
  46 brands leave about 4,400 For Sale listings with no discriminating identity.
  The highest-value additions are the most frequent leading words among
  unresolved listings, hand-checked -- frequency alone ranks `SMART`, `USB` and
  `UNIVERSAL` just as high.
- **`CATALOG_PROFILE_MAX_LISTINGS` at 12 is adequate for services** but is why
  `consensus` is only 1,089 rows: consensus needs two independent sellers on one
  entity, and a service entity is one provider by construction. Consensus will
  matter for products, where several sellers list the same item.

## 9. What is not measured

- **Product profiles.** Not run. Requires approval to spend; 3,311 entities in
  snapshot B, of which 3,301 are pending.
- **291 service entities added by the live sync** are unprofiled. The approval
  covered the first services pass; whether each ingest round re-spends is a
  standing decision, not one this file can make.
- **The taxonomy ordering constraint** in section 7 is load-bearing and was
  learned here: a product entity key includes its mapped category, so growing
  the taxonomy changes keys and orphans the old entities. Run seed_taxonomy and
  map_categories BEFORE resolve_entities, never after.
- **Facet discovery output.** `discover_facets` scores against a live candidate
  set, so it needs the API under query load rather than a static count. The
  `has_inferred` marker is wired and unit-tested but its real distribution is
  unmeasured.
- **Crowdsourced corrections.** The endpoint works end-to-end against a
  453-listing entity, but no real proposal has been submitted, so quorum and
  conflict behaviour are only covered by tests.
- **Latency.** No p95 was taken after adding the entity join to the detail
  endpoint or `has_inferred` to facet discovery.
