# Beynunehcheh Search Engine - Design Spec

Date: 2026-08-17
Status: awaiting review

## 1. Goal

A multilingual vertical search engine over Maldivian data. Four result
categories at launch (shopping, jobs, news, real estate), extensible to more. A
Dhivehi query returns a Dhivehi-presented result set; an English query returns an
English one. New data sources plug in by writing one adapter, not by touching
search code.

Current corpus: 20,445 iBay products, 34,895 product images, 2,001 sellers, 896
categories, 306 gazette iulaan, 170 offices, 13 iulaan types. Within iBay, 3,497
products sit under `Housing & Real Estate` and 335 under `Jobs`.

The 306 iulaan are 30 of the gazette's 35,335 listing pages. The target is the
first 5,000 pages, roughly **51,000 iulaan**, so the gazette side of the corpus
grows by about 165x. Design for that number, not for 306 - it drives the
transcription budget in 5.6.2 and the scraping effort that section flags as the
real long pole.

## 2. Decisions taken

| Decision | Choice |
|---|---|
| Store + index | PostgreSQL (tsvector, pg_trgm, JSONB); migrate off SQLite, self-hosted in Compose |
| Cross-language | Query-side expansion, plus background translation of titles/summaries only |
| Normalization + categorization | LLM extraction pass at temperature 0, validated against a Pydantic schema |
| Enrichment provider | `deepseek-v4-flash` over the API; local `qwen3.5:4b` as offline fallback |
| Scanned-PDF transcription | `claude-haiku-4-5`, native PDF input, via the Batch API |
| GPU allocation | The 9070 XT runs translation only, at raised concurrency |
| Document types | shopping, job, news, property |
| API | django-ninja (Pydantic schemas, OpenAPI) |
| Frontend | Next.js (App Router, TypeScript, types generated from OpenAPI) |
| Deployment | Docker Compose, separate dev and prod files, hot reload in dev |

## 3. Architecture

```
ibay/  gazette/  <future sources>       existing apps, unchanged: scrape and store raw
        |
        v
core/                                   shared: translation client + TranslationCache
        |
        v
enrich/                                 LLM normalization -> EnrichedRecord (typed attrs)
        |
        v
search/                                 SearchDocument index, indexers, language pipeline,
        |                                query planner, ranking, facets, ninja API
        v
web/                                    Next.js frontend
```

The principle: source apps stay dumb. They know how to scrape and nothing about
search. Everything search-related lives in new apps, so adding a source means
writing an adapter class and a Pydantic schema.

Three new Django apps:

- `core` - the translation client and its cache, currently in `gazette/`. It is
  now cross-app infrastructure, so it moves. This is the only change to existing
  code that the design requires.
- `enrich` - the LLM normalization pipeline and its output records.
- `search` - the index, the language pipeline, the query engine, and the API.

### 3.1 The source adapter contract

Everything derived - `EnrichedRecord`, `SearchDocument`, `DocumentSpec` - is
disposable and rebuildable from the source apps. That property is what makes
adding a document type later a re-run rather than a re-scrape, so it is a contract,
not an accident. Every adapter implements **both directions**:

```python
class SourceAdapter(Protocol):
    key: str                                    # 'ibay', 'gazette'

    def iter_source_keys(self, **filters) -> Iterator[str]: ...
    def fetch_raw(self, source_key: str) -> RawDocument | None: ...
    def to_document(self, raw: RawDocument) -> DocumentDraft: ...
```

`fetch_raw` is the half that is easy to forget and the half that makes
reprocessing possible. Without it, every re-run needs source-specific glue written
from scratch; with it, `reindex` and `enrich_documents` are source-agnostic and a
new source gets reprocessing for free.

**Identity is the natural key pair `(source, source_key)`**, not a foreign key.
`gazette` keys on `Iulaan.id`, `ibay` on `Product.listing_id` - different types in
different apps, so a real FK would need generic relations, which would couple the
search app to every source model and add a join to the hot path. The pair is a
deliberate, documented contract with two obligations:

- **`source_key` is immutable and never reused.** It is the source's own permanent
  identifier, not a row position or a slug.
- **Source rows are deactivated, never deleted.** Nothing enforces referential
  integrity across the boundary, so a deleted product would silently orphan its
  derived rows. `is_active = False` instead.

What survives for a re-run, without any paid work repeated: the raw gazette body
HTML, `additional_info`, `iulaan_type`, `office`, every iBay field and
`ProductInfo` row, and - critically - `Attachment.text`. Transcription output is
persisted independently of enrichment (5.6), so re-running classification and
extraction costs only the DeepSeek pass, never the Claude pass.

### 3.2 Adding a document type later

The concrete case: `tender` and `auction` are currently absorbed into `news`
(5.3), and one day they should not be. The full procedure:

1. Add `TenderAttrs` to `enrich/schemas.py` and the type to the registry.
2. Add the classification prior - `ބީލަން` maps to tender - to the rule table in 5.3.
3. Mark the affected slice stale:
   ```sql
   UPDATE search_searchdocument SET stale_marked_at = now()
   WHERE source = 'gazette' AND doc_type = 'news';
   ```
4. Run `enrich_documents --stale` then `reindex --stale`.

No re-scrape, no re-transcription. At the 51,000-iulaan scale the whole gazette
news slice re-enriches for well under $86 (3.1's cost figure covers *all* 51,000),
and the transcribed text it reads was paid for once.

This is also why `doc_type` cannot be a partition key - see 12.2.

### 3.3 Why enrichment output is a separate model

LLM output is the most expensive artifact in the system. It is persisted in
`EnrichedRecord`, keyed by a hash of the exact source text, so that re-indexing
is free and a re-scrape that did not change any text does not re-run the model.
Reindexing and re-enriching are independent operations. This is the difference
between a 3-hour operation and a 3-second one.

## 4. Data model

### 4.1 `search.SearchDocument`

One row per searchable entity. Fully denormalized: a search response requires no
joins into source apps.

```python
class SearchDocument(models.Model):
    # identity
    source        = CharField(max_length=32)     # 'ibay', 'gazette' -> Source.key
    source_key    = CharField(max_length=128)    # natural key within the source
    doc_type      = CharField(max_length=32)     # shopping | job | news | property
    url           = URLField()
    # unique_together: (source, source_key) -- the identity contract in 3.1.
    # `source` is the partition key (12.2), so this constraint is legal on the
    # partitioned table. `doc_type` is mutable and deliberately NOT part of it.

    # display, language-parallel
    title_en   = CharField(max_length=512, blank=True)
    title_dv   = CharField(max_length=512, blank=True)
    summary_en = TextField(blank=True)
    summary_dv = TextField(blank=True)

    # search vectors. The text they were built from is NOT stored here; see
    # section 12. Bodies live in the source tables, the index keeps only vectors.
    vector_en    = SearchVectorField(null=True)   # to_tsvector('english', ...)
    vector_dv    = SearchVectorField(null=True)   # to_tsvector('simple', ...)  fili-stripped
    vector_latin = SearchVectorField(null=True)   # to_tsvector('simple', ...)  transliterated
    title_latin  = CharField(max_length=512, blank=True)  # trigram target, titles only

    # universal facets
    price        = DecimalField(null=True)
    currency     = CharField(max_length=8, default='MVR')
    location     = CharField(max_length=128, blank=True)   # normalized
    island       = CharField(max_length=128, blank=True)
    atoll        = CharField(max_length=64, blank=True)
    published_at = DateTimeField(null=True)
    expires_at   = DateTimeField(null=True)   # apply-before, auction close, bid deadline
    is_active    = BooleanField(default=True)

    # type-specific data
    attrs        = JSONField(default=dict)   # validated against the doc_type schema
    card         = JSONField(default=dict)   # render-ready payload for the frontend
    card_version = IntegerField(default=1)
    thumbnails   = JSONField(default=list)

    # ranking and bookkeeping
    quality      = FloatField(default=0.0)   # completeness and trust score
    content_hash = CharField(max_length=64)
    indexed_at   = DateTimeField(auto_now=True)
    stale_marked_at = DateTimeField(null=True)   # manual reprocess override (5.7)
```

Indexes, all partial on `WHERE is_active` (see 12.2): GIN on each of the three
vectors; GIN `jsonb_path_ops` on `attrs`; GIN trigram on `title_en`, `title_dv`
and `title_latin` - **titles only, never body text**; BRIN on `published_at`;
btree on `price`, `location`, `expires_at`.

The table is LIST-partitioned by `source` (12.2 explains why not by `doc_type`).

Three vectors rather than one, because ranking needs to know which language
matched. A Thaana query that matches a Thaana title should outrank one that
matched only through transliteration, and that distinction is unrecoverable once
the vectors are merged. It also lets Thaana use the `simple` configuration (no
Dhivehi stemmer exists anywhere) while English uses `english`.

### 4.2 `enrich.EnrichedRecord`

```python
class EnrichedRecord(models.Model):
    source, source_key            # same natural key as SearchDocument
    content_hash    = CharField(max_length=64)   # sha256 of text fed to the model
    doc_type        = CharField(max_length=32)
    doc_type_confidence = FloatField()
    canonical_title_en / canonical_title_dv
    summary_en / summary_dv       # <= 240 chars
    attrs           = JSONField() # schema-validated per doc_type
    keywords        = JSONField() # aliases and synonyms, both scripts
    model_name      = CharField() # provenance
    prompt_version  = IntegerField()
    validation      = JSONField() # every field dropped, and why
    status          = CharField() # pending | ok | needs_review | failed
    attempts        = IntegerField(default=0)
    # unique_together: (source, source_key)
```

Re-enrichment triggers only on a `content_hash` change or a `prompt_version`
bump. That gate is what makes the pipeline affordable at 20k documents and
beyond. For `source='gazette'` the `prompt_version` trigger is disabled entirely -
those documents are write-once (5.7).

### 4.3 Typed attribute schemas

Per `doc_type`, one Pydantic model is the single source of truth for five
consumers: the JSON schema sent to the enrichment provider, database validation,
the facet registry, the API response type, and the generated TypeScript types.
Defined once in `enrich/schemas.py`.

**`JobAttrs`** - `role` (the job title alone, stripped of employer and
boilerplate), `employer`, `position_type`, `job_category`, `grade` (the civil
service rank, `GS3`, `MS1`, where stated), `compensation` (see 4.3.2),
`qualifications` (list of short strings), `experience_years`, `deadline`,
`apply_methods` (list, see below).

`compensation.salary_state` is a three-way discriminator rather than a nullable
number, because the card must distinguish "Negotiable" from "Unlisted" and those
are different claims. `negotiable` may only be set when the source says so - a
missing salary is `unlisted`, never `negotiable`. The grounding validator
enforces this.

### 4.3.2 Job compensation

Maldivian job ads, especially public sector ones, quote pay as line items rather
than a single figure. A real body from the corpus:

```
އަސާސީ މުސާރަ:            މަހަކު 10,750 ރުފިޔާ
އެލަވަންސް/އިނާޔަތްތައް:   ހާޒިރީ އެލަވަންސްގެ ގޮތުގައި ... މަހަކު 4,400 ރުފިޔާ
                          ލިވިންގ އެލަވަންސް ...
```

Basic salary, an attendance allowance, a living allowance, and separately a 7%
pension deduction. The figure a job seeker actually wants - what lands in their
account - appears nowhere in the ad.

```python
class Allowance(BaseModel):
    kind: Literal['service', 'living', 'attendance', 'ration', 'phone',
                  'risk', 'transport', 'overtime', 'other']
    label_raw: str                 # 'ހާޒިރީ އެލަވަންސް'
    amount: float
    basis: Literal['fixed_monthly', 'per_day', 'per_hour', 'percent_of_basic']

class Compensation(BaseModel):
    basic_salary: float | None
    basic_salary_max: float | None      # grade bands quote a range
    currency: str = 'MVR'
    period: Literal['month', 'day', 'hour', 'year'] = 'month'
    allowances: list[Allowance]
    pension_applies: bool               # only when the ad says so
    pension_rate: float = 0.07
    salary_state: Literal['listed', 'negotiable', 'unlisted']
    completeness: Literal['full', 'partial', 'basic_only', 'none']
```

**The model extracts line items. It never does arithmetic.** Every derived figure
comes from a pure Python function:

```python
def estimate_net(comp: Compensation, working_days: int = 20) -> NetEstimate
```

Take-home is `basic - (basic * pension_rate) + fixed_allowances +
(per_day_allowances * working_days)`. The Maldives Retirement Pension Scheme
deducts 7% of pensionable wage from the employee, and pensionable wage is
normally basic salary alone, so allowances are added after the deduction, not
before. `PENSION_RATE` and `PENSION_BASE` are settings, because that treatment
can change and hardcoding tax logic is how a search engine starts lying.

Arithmetic in Python rather than in the prompt is not a style preference. Language
models are unreliable at multi-step arithmetic and temperature 0 does not fix
that; a wrong take-home figure is precisely the "misleading" failure this system
is supposed to make impossible. The model's only job is to say
"4,400 per month, attendance" - a claim the grounding validator can check against
the source digits.

**The estimate is always labelled as an estimate.** The card leads with the figure
the employer actually stated (basic salary) and shows the computed take-home as a
clearly secondary, explicitly approximate value. `completeness` drives how it is
presented: `full` when basic and all allowances parsed, `basic_only` when no
allowances were found, `partial` when some line item failed validation. A
`partial` estimate is shown as a floor ("at least ..."), never as a point value.

`working_days` defaults to 20 and is adjustable on the detail page. Because the
card payload carries the line items, the recomputation happens client-side from
the same pure logic; nothing is re-fetched.

Sorting and the salary facet use `estimated_net_min`, since that is the only
figure comparable across ads that itemize differently. Cards still display basic
salary as the stated number.

`apply_methods` is a list of `{kind, value, label_en, label_dv}` where `kind` is
one of `form`, `email`, `phone`, `viber`, `whatsapp`, `portal`, `walk_in`,
`post`. `form` recognizes Google Forms and Microsoft Forms by host so the
frontend can render "Apply via form" as a button.

**`PropertyAttrs`** - `listing_kind` (`rent | sale | wanted`), `unit_kind`,
`occupancy` (see 4.3.1), `bedrooms`, `bathrooms`, `square_feet`, `floor`,
`furnishing`, `neighborhood`, `has_lift`, `room_facilities` (list),
`tenant_preference` (list), `price_period` (`month | day | year`), `contacts`.

**`ShoppingAttrs`** - `condition`, `brand`, `model`, `category_path`, `quantity`,
`delivery`, `seller_type`, `negotiable`, `contacts`, plus `specs`, the open
attribute list that drives dynamic filtering (see 4.4).

**`NewsAttrs`** - `office`, `announcement_type`, `reference_no`, `deadline`,
`tender_fee`, `documents`, `is_tender`.

### 4.3.1 Property occupancy

Occupancy cannot be a bedroom count. The data contains three genuinely different
shapes, and `Type` already labels them: `Guest House` (1,099), `Apartment` (912),
`Room on Daily Rent` (396), `Separate Room` (290),
`Room in Apartment / House` (278).

```python
class Occupancy(BaseModel):
    unit_kind: Literal['whole_unit', 'room', 'bed_space',
                       'guest_house', 'land', 'commercial']
    rooms_offered: int | None      # 1, when one room of a larger unit is let
    rooms_total: int | None        # 3
    beds_offered: int | None       # bed-space listings: "Sharing Bed Space (2 Space)"
    max_occupants: int | None
    is_shared: bool
    shared_facilities: list[str]   # kitchen, bathroom, living
    tenant_preference: list[str]   # family, expatriate, couple, male, female, student
```

Three concrete cases from the corpus this has to survive:

- `1 Room Apartment for rent Viber Only 9223232 7000/- Near IGMH` -
  `whole_unit`, `rooms_total=1`, price 7000 MVR per month.
- `Room in Apartment / House` with `Bedrooms: 3 Rooms` - `room`,
  `rooms_offered=1`, `rooms_total=3`, `is_shared=True`.
- `Vazeefaa ah dhaa firihen kudhin bahattaden (phase 2)` and
  `Sharing Bed Space (2 Space) Available Prefer South Indian Boy (Tamil) 2800` -
  `bed_space`, `beds_offered=2`, `is_shared=True`,
  `tenant_preference=['male', 'working']`.

The shared-accommodation listings are written in Latin-script Dhivehi with no
Thaana at all (`firihen` male, `anhen` female, `kudhin bahattan` roughly "to
accommodate people"), which makes the `dv-Latn` detection in section 6.1 load
bearing for this document type specifically, not a nice-to-have.

`Ideal Tenants` in the source is a single field carrying three incompatible kinds
of value: bare counts (`2` 68 times, `4` 31, `6` 22, `1` 21), categories
(`Family` 29, `Expatriates` 18, `Couples or Expatriates` 21), and prose
(`Ideal for Families, Expatriates, Diplomats & Corporate Clients` 12). Splitting
that into `max_occupants` and `tenant_preference` is exactly the normalization
work the enrichment pass exists to do.

Currency is not assumable: 1,019 products mention USD. `currency` is set from an
explicit marker (`USD`, `$`, `dollar`, `MVR`, `ރ`, `rufiyaa`, the local `/-`
suffix) and otherwise defaults to MVR with `currency_inferred = True` recorded,
so the UI can present an inferred currency differently from a stated one.

Normalization also has to handle `Bedrooms` arriving as strings, not integers:
`2 Rooms` (259), `3 Rooms` (227), `1 Room` (200), `4 Rooms and More` (40). The
last becomes `bedrooms=4, bedrooms_or_more=True`.

### 4.3.3 `search.Source` - the provenance registry

Every result shows where it came from, with an icon. That makes the source a
first-class registry rather than a bare string on `SearchDocument`:

```python
class Source(models.Model):
    key         = CharField(max_length=32, unique=True)   # 'ibay', 'gazette'
    label_en    = CharField(max_length=64)                # "iBay", "Gazette"
    label_dv    = CharField(max_length=64)                # "ގެޒެޓް"
    site_url    = URLField()
    icon        = CharField(max_length=128)   # '/static/sources/gazette.svg'
    icon_fallback_text = CharField(max_length=4, blank=True)  # monogram chip
    accent      = CharField(max_length=9, blank=True)     # optional hex tint
    is_active   = BooleanField(default=True)
```

Display metadata lives in the database so adding a source is an admin row plus an
icon file; the *adapter* stays in code, because extraction logic is not data. Those
are the two halves of "plug in a new source" and they belong in different places.

**Icons are self-hosted, never hotlinked.** Fetch each site's favicon once, at the
largest available size - `apple-touch-icon` at 180px, or an SVG if the site offers
one - and commit it to `static/sources/`. Hotlinking would put a third-party
request on every result row, which is slow, unreliable, and leaks the user's
queries by referrer to every site in the result set. A 16px favicon upscaled into a
card looks broken, so prefer SVG and treat a low-resolution-only source as a
candidate for the monogram fallback instead.

**`card` stores the source key, not the icon URL.** Resolution happens client-side
against the `/meta` registry (section 9). Embedding a path in every card payload
would duplicate the same string across all 71,445 documents for no gain, and
re-skinning a source would then require a full reindex rather than an admin edit.

### 4.4 The dynamic filter substrate

Shopping needs facets that change with the query: amps and voltage ranges for a
power supply, brand checkboxes for a phone. That requires an open attribute space,
which is a different storage problem from the typed per-type fields above.

**Two tables.**

```python
class SpecKey(models.Model):          # curated registry
    key          = CharField(unique=True)   # 'voltage', 'brand', 'storage_gb'
    label_en, label_dv = CharField()
    datatype     = CharField()   # numeric | enum | bool
    unit         = CharField(blank=True)     # 'V', 'A', 'W', 'GB'
    unit_aliases = JSONField()    # ['volt', 'volts', 'v']
    value_aliases= JSONField()    # {'Apple (iPhone)': 'Apple'}
    widget       = CharField()   # range | checkbox | toggle
    categories   = JSONField()    # leaf categories where this key is meaningful
    priority     = IntegerField() # curated ordering within a category
    is_facetable = BooleanField(default=False)

class DocumentSpec(models.Model):     # one row per extracted attribute
    document   = ForeignKey(SearchDocument, related_name='specs')
    key        = ForeignKey(SpecKey, null=True)   # null until promoted
    key_raw    = CharField()      # what the source or model called it
    value_num  = FloatField(null=True)
    value_text = CharField(blank=True)
    unit       = CharField(blank=True)
```

A relational side table rather than JSONB, because facet discovery is an
aggregation over the candidate set and `GROUP BY` on indexed columns beats
unnesting a JSONB array on every request. Volume is small: roughly 20,000
products times about 4 specs each, so under 100,000 rows.

**Extraction is open, faceting is curated.** The LLM and the unit extractor may
produce any `key_raw`. Only keys promoted into `SpecKey` with
`is_facetable = True` become filters; everything else is stored, displayed in the
spec table on the detail page, and surfaced in an admin queue ranked by frequency
for one-click promotion. This is what stops the attribute space from degenerating
into thousands of junk facets while still letting new product categories arrive
without a schema change.

The corpus shows why the registry has to be category-scoped rather than global:
`Type` means `Guest House` for property, `LED` for televisions,
`Laptop/Notebook` for computers, and `Action and Adventure` for video games (338
of those). One key, four unrelated vocabularies.

It also shows why `value_aliases` is required rather than optional:
`Apple (iPhone)` (999) and `Apple` (111) are the same brand and must collapse
into one checkbox, or the most common filter in the corpus is wrong.

**Numeric specs often live in the title, not in a field.** The real listing
`KICO METAL POWER SUPPLY 24V-5A-120W / 7884445` carries its entire spec sheet as
a compact title string. So a deterministic unit-pattern extractor runs over title
and description before the model does - a regex over
`<number><optional space><unit>` against the `SpecKey` unit vocabulary - and the
model's job is only to assign semantic keys to the numbers found and fill what
the regex missed. Cheaper, and it cannot hallucinate a voltage.

Multi-value strings need splitting too: `Room Facilities` arrives as
`Air Conditioning, Fans, Towels` (1,137 occurrences), one row per facility after
normalization, so each becomes an independent checkbox.

The existing iBay `ProductInfo` keys already supply near-schema data, which is
the strongest argument for the typed-attribute design: `Item Condition` (7,098),
`Type` (4,194), `Neighborhood` (3,363), `Brand` (2,313), `Room Facilities`
(1,608), `Lift` (1,552), `Floor` (1,356), `Furnishing` (1,278), `Bedrooms` (726),
`Bathrooms` (721), `Ideal Tenants` (550), `Square Feet` (510),
`Position Type` (283), `Job Category` (276), `Employer` (274),
`Salary Range` (244), `Apply Before` (198).

`PropertyAttrs` in particular is almost a direct read of keys that already exist,
which is why real estate is a document type rather than a subcategory of
shopping: nine of its twelve fields are already populated in the source data for
thousands of listings.

The model's job is therefore normalization - units, date formats, salary ranges,
script - plus filling gaps from free text. It is not inventing structure from
nothing, which is what keeps the hallucination surface small.

## 5. LLM enrichment pipeline

### 5.1 Provider chain

Enrichment runs against the DeepSeek API. The GPU is left to translation alone.

| Stage | Provider | Model | When |
|---|---|---|---|
| 1 | DeepSeek API | `deepseek-v4-flash` | default |
| 2 | DeepSeek API | `deepseek-v4-flash` | repair retry, validation errors fed back |
| 3 | DeepSeek API | `deepseek-v4-pro` | records that failed stage 2 |
| 4 | Ollama | `qwen3.5:4b` | offline, dev, or DeepSeek unavailable |

This mirrors the escalation ladder `translate.py` already implements (local, then
OpenRouter, then Gemini), so the enrichment client follows an idiom the codebase
already has rather than inventing a second pattern. `ENRICH_PROVIDER` selects the
head of the chain, so the whole pipeline runs offline against Ollama when needed.

`deepseek-v4-flash` rather than `pro`: extraction constrained by a schema and
checked by a grounding validator is not a reasoning-hard task, and `pro` costs
three times as much on both input and output for no gain. `pro` earns its place
only on the records `flash` failed twice.

**Cost of the cold pass.** Measured input sizes: iBay `name` plus `description`
averages 419 characters (median 246, max 13,099), roughly 115 tokens, plus 1.54
`ProductInfo` rows per product. Gazette bodies average 5,569 characters, capped
at 3,500 as `translate.py` already does, roughly 1,000 tokens. The system prompt
and JSON schema are byte-identical on every call, around 800 tokens, so they hit
DeepSeek's context cache after the first request.

| Component | Tokens | Rate (off-peak) | Cost |
|---|---|---|---|
| Cached prompt prefix | 16.6M | $0.007 / M | $0.12 |
| Uncached document text | 4.4M | $0.22 / M | $0.97 |
| Output, ~300 tokens per doc | 6.2M | $0.66 / M | $4.09 |
| **Full corpus, 20,751 documents** | | | **~$5.20** |

Peak-hour rates are double, so schedule cold passes outside 01:00-04:00 and
06:00-10:00 UTC (06:00-09:00 and 11:00-15:00 Maldives time). The jobs-and-news
first pass is 641 documents, which costs cents.

**Throughput.** A cloud provider removes the GPU bottleneck entirely: start at 8
concurrent requests with adaptive backoff on 429, and the full corpus finishes
well inside an hour instead of the roughly three hours a local 4B needed. Tune
the concurrency empirically; DeepSeek's published limits are vague.

**Two things this trades away**, both real:

- **No schema-constrained decoding.** DeepSeek supports JSON mode
  (`response_format: {"type": "json_object"}`) but not strict `json_schema`
  enforcement, and its docs acknowledge occasional empty-content responses.
  Ollama's `format` parameter guaranteed structurally valid output by
  construction; DeepSeek does not. Section 5.2 layer 2 changes accordingly.
- **No reproducibility.** There is no `seed` parameter, so temperature 0 gives
  stable-ish but not bit-identical output. `content_hash`, `model_name` and
  `prompt_version` make drift detectable, not preventable. Acceptable, because
  the grounding validator gates what is stored either way.

**Data residency.** iBay descriptions contain personal phone numbers
("Call 7994400", "Viber 9483252"). The listings are already public, but this does
send them to a third-party API hosted outside the Maldives. Flagging it as a
decision rather than an oversight; `ENRICH_PROVIDER=ollama` keeps everything
on-premises at the cost of throughput.

### 5.1.1 Freed GPU capacity

With enrichment moved off the card, GemmaTranslate-v3-12B (~6.6 GB at IQ4_XS) is
the only resident model, leaving roughly 9 GB of the 16 GB free for KV cache.

Gemma 3 interleaves sliding-window and global attention at 5:1, so a 4K-context
slot costs roughly 0.5 to 0.6 GB rather than the ~1.6 GB a fully-global 12B would
need. That supports 4 to 6 parallel slots.

GPU host configuration: `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=4`
initially, `OLLAMA_KEEP_ALIVE=30m`. Raise to 6 after confirming headroom with
`rocm-smi --showmeminfo vram` under load. Translation throughput improves
roughly fourfold, which matters because the background title-and-summary
translation job in section 5.5 is the long pole for Dhivehi display coverage.

The `SEMAPHORE = asyncio.Semaphore(2)` in `translate.py` becomes a setting and
rises to match `OLLAMA_NUM_PARALLEL`; leaving it at 2 would waste the new slots.

### 5.2 Correctness controls

The requirement is that nothing misleading reaches a result card. Six layers, in
order of how much they actually do:

0. **Deterministic pre-extraction** - phone numbers, emails, URLs, money amounts
   and `<number><unit>` pairs are pulled out by regex before the model is called,
   and passed in as a candidate list. Maldivian numbers are seven digits, mobile
   starting 7 or 9 and landline 3 or 6, optionally prefixed `+960` and often
   embedded in titles (`KICO METAL POWER SUPPLY 24V-5A-120W / 7884445`,
   `1 Room Apartment for rent Viber Only 9223232 7000/- Near IGMH`). Money is
   written several ways that all need parsing: `10,750`, `-/32,632`, `7000/-`,
   with `ރުފިޔާ`, `MVR`, `USD` or `$` markers. The model selects and labels from
   these candidates; it never transcribes them. A digit the regex did not find
   cannot appear in the output, so a wrong phone number, invented voltage or
   fabricated salary is structurally impossible rather than merely unlikely. This
   also cuts output tokens.

   Gazette bodies get one more deterministic step: they are HTML, and the tables
   inside them are already labelled key-value pairs
   (`<td>އަސާސީ މުސާރަ:</td><td>މަހަކު 10,750 ރުފިޔާ</td>`, allowances as `<li>`
   items). `lxml`, already a dependency, parses those into label/value pairs that
   are fed to the model instead of raw markup. Structure the source gave us for
   free should not be re-derived by a language model.

   **No arithmetic anywhere in the model's output.** Take-home pay, price
   conversions, range midpoints and per-day totals are all computed by tested
   Python from extracted line items (see 4.3.2).
1. **Determinism** - `temperature: 0` on every provider, plus `top_k: 1`,
   `seed: 42` and `think: false` on the Ollama path where those exist. Reasoning
   modes stay off: they cost tokens and add nondeterminism to an extraction task.
2. **Structural validity by validation, not by construction** - DeepSeek's JSON
   mode guarantees parseable JSON but not schema conformance, so the schema is
   pasted into the prompt (both providers' docs advise this) and the response is
   parsed into the Pydantic model. A `ValidationError` triggers one repair retry
   with the error text appended to the prompt, then escalation per the chain in
   5.1. An empty-content response, which DeepSeek documents as an occasional
   occurrence, is treated as a failed attempt and retried. Note this is weaker
   than the grammar-constrained decoding Ollama offers; it is the price of the
   cloud provider, and it is affordable because layer 3 was always the layer
   doing the real work.
3. **Grounding validator** - the layer that matters. Every extracted string must
   be traceable to the source text: an exact substring after normalization, or
   at least 0.85 token overlap. Every number (price, salary, square feet) must
   appear as digits in the source. Every date must parse and land in a sane
   range. A field that fails is **dropped**, and the reason is recorded in
   `validation`. Nothing is repaired by guessing.
4. **Scraped fields win** - iBay's own `price`, `product_location` and
   `ProductInfo` values, and gazette's `iulaan_type` and `office`, are ground
   truth. The model may fill a null; it may never overwrite. A conflict keeps
   the scraped value and flags the record `needs_review`.
5. **Nullable everything** - schema fields are `["string", "null"]` and the
   prompt instructs omission over guessing. A null field is correct behavior. A
   plausible invention is a bug.

A `needs_review` or `failed` record still gets indexed, using scraped data and
the rule-based `doc_type` fallback. It just carries no model-derived attributes.
Indexing never blocks on enrichment.

### 5.3 Categorization

`doc_type` comes from the same call as extraction, one pass rather than two. The
prompt is given a prior derived deterministically:

- gazette `iulaan_type`: `ވަޒީފާގެ ފުރުޞަތު` and `Job Opportunity` map to job;
  `ކުއްޔަށް ދިނުން` and `ކުއްޔަށް ހިފުން` (letting and seeking to rent) map to property;
  `ޢާންމު މަޢުލޫމާތު`, `Public Information`, `ދެންނެވުން`, `ބީލަން`, `ނީލަން` map to news.
- iBay top-level category: `Jobs` (335 products) maps to job;
  `Housing & Real Estate` (3,497) maps to property; `Announcements & Events`
  maps to news; `For Sale`, `Services`, `Wanted`, `Free Stuff` and
  `Business Opportunities` map to shopping.

The model may override the prior only at confidence >= 0.8. Otherwise the prior
wins. This matters because the data is genuinely mixed: iBay listings like
"Cleaning work daily worker" and "Daily wage jobs" sit under shopping-ish
categories and are really jobs, and `Services` contains both. The same rule table
is the complete fallback when no provider is reachable.

**`news` is the default sink.** Anything that does not classify confidently into
shopping, job or property becomes news - there is no `unknown` type and no
quarantine queue. That covers the gazette types with no home yet
(`ބީލަން` bids, `ނީލަން` auctions, `މަސައްކަތް` works, `ގަންނަން ބޭނުންވާ ތަކެތި`
items wanted, `މުބާރާތް` competitions), low-confidence classifications, and
anything a future source produces that the registry has not learned about.

This puts weight on the news card, since a news result has no typed attributes to
carry it: a **descriptive title, a useful excerpt, the source icon and a link out**
are the entire product for that document. So for the news bucket the enrichment
pass earns its cost on `canonical_title_*` and `summary_*` rather than on `attrs` -
a raw gazette title plus the first 200 characters of HTML-stripped body is not good
enough, and that is precisely the case where the model adds value. When a document
falls into news because classification was uncertain rather than because it is
genuinely an announcement, the summary is the only thing standing between the user
and an unreadable result.

Promoting a type later - `tender`, `auction` - is a registry addition plus a
reindex of the affected documents, so nothing about this decision is hard to
reverse.

### 5.4 Orchestration

- `enrich/pipeline.py`, async, with its own semaphore (`ENRICH_CONCURRENCY`,
  default 8 against DeepSeek, 2 against Ollama). Separate from translation's
  semaphore either way, so the two workloads never contend.
- `manage.py enrich_documents --source ibay --type job --limit N --provider X --force`
- Idempotent and resumable. Per-record try/except, `attempts` counter, backoff.
- Run order: jobs and news first (306 iulaan plus 335 iBay `Jobs` products, 641
  documents, minutes and cents), then property (3,497), then shopping (~16,300).
  The typed facets with the most to gain go live first, and the corpus that costs
  the most enriches last. Afterwards the `content_hash` gate makes incremental
  passes nearly free.

### 5.5 Query-side translation

The query pipeline calls the existing `translate_auto`, which is already cached
in `TranslationCache` (676 entries today). One cached call per unique query,
typically a cache hit. Separately, a background job fills `title_dv`/`title_en`
and `summary_dv`/`summary_en` on documents - short fields only, never bodies.

`TranslationCache` and `translate.py` move from `gazette/` to `core/`. The 676
rows are copied by a data migration. `gazette` imports from `core`.

### 5.6 Gazette attachments

For a large share of gazette job postings the listing is a stub and the actual
details - salary table, qualifications, how to apply - are inside an attached
file. Only 16 of 306 bodies state basic salary; the rest do not. A search engine
that indexes only the listing text is therefore missing the content users are
searching for.

Measured: 239 of 306 iulaan (78%) carry attachments - 261 PDFs, 73 `.docx`, 2
`.xlsx`. `Iulaan.attachments` is already a `{label: url}` JSON dict pointing at
stable public URLs on `storage.googleapis.com/gazette.gov.mv/docs/iulaan/`.

**Labels carry meaning.** They arrive as `iulaan`, `vazeefa ah edhey form`
(the job application form), `A2 sheet`, or Thaana equivalents. A label classifier
routes each attachment: the main document feeds extraction, while an application
form becomes an `apply_method` of kind `form` rather than text to index. Getting
this wrong means indexing a blank form as if it were the job description.

**Extraction ladder**, cheapest first:

1. `.docx` - `python-docx`, pure Python, low memory. 73 files, no OCR, and these
   are frequently the detail sheets, so this is the best value in the whole
   pipeline.
2. PDF with a text layer - `pdftotext -layout` from poppler, a subprocess with a
   flat memory profile. Most gazette PDFs are Word exports (the 73 `.docx`
   attachments alongside them prove the offices author in Word), so this handles
   the majority.
3. PDF without a usable text layer, detected as fewer than ~200 extracted
   characters per page - a scanned signed letter. Send the **PDF file itself** to
   Claude Haiku 4.5 as a `document` content block and ask for a verbatim
   transcription.
4. Give up, record `status='ocr_failed'`, and index the listing text alone.

**Tesseract is deliberately not in that ladder.** The published research on
Thaana with Tesseract reports about 69% character accuracy on *machine-generated*
text, which would be worse on scans. At that error rate the extracted text
poisons the index with plausible-looking wrong words, which is worse than having
no text at all. A vision model or nothing.

### 5.6.1 Transcription with Claude Haiku 4.5

Claude accepts PDFs natively as a `document` content block - base64, no beta
header - and renders the pages internally. **That deletes the rasterization step
entirely**: no `pdftoppm`, no page-at-a-time JPEG temp files, no RAM spike, no
cleanup. An earlier draft of this spec budgeted for that machinery; it is gone.
It also means the model sees the page as laid out, which matters because the
content we want most is a salary *table*.

- Model: `claude-haiku-4-5`, $1.00 / $5.00 per million tokens in/out, 200K
  context, 64K max output. Vision and structured outputs both supported.
- Limits: 32 MB per request, 100 pages per PDF at this context size. Gazette
  attachments are far below both; cap at the first 10 pages anyway.
- `temperature: 0`. Sampling parameters are still accepted on Haiku 4.5 (they are
  rejected on the newer Opus and Sonnet tiers, not here).
- Run it through the **Batch API**: 50% off, up to 100,000 requests, most batches
  finish within an hour. Attachment extraction is already a background management
  command with no latency requirement, so there is no reason to pay list price.
- Prompt caching is not worth configuring - Haiku 4.5's minimum cacheable prefix
  is 4,096 tokens and the transcription instruction is a few hundred.

Cost is not cents at the real corpus size - see 5.6.2.

**Accuracy is unknown and must be measured, not assumed.** No vendor publishes
Dhivehi OCR benchmarks. But the corpus supplies a free evaluation harness: take
the PDFs that *do* have a text layer, extract them with `pdftotext` to get
near-ground-truth Thaana, then send the same PDFs through the transcription path
and compute character error rate against it. Real Maldivian government documents,
real Thaana, zero labelling cost. That decides Claude Haiku 4.5 versus Gemini
empirically rather than by reputation, and it re-runs whenever a model changes.

The same threshold logic that rejected Tesseract applies to the winner: a
document whose measured CER exceeds the accepted bar is recorded `ocr_failed`
rather than indexed. Text that is confidently wrong is worse than absent text.

**Provenance follows the text.** Attributes derived from a transcribed scan carry
a lower `quality` score and a flag, and the job card can say the details came
from a scanned document. A salary read off a clean Word export and one read off a
photographed signed letter are not the same claim, and the UI should not present
them as if they were.

### 5.6.2 Transcription cost at full corpus size

The 306 iulaan currently synced are 30 listing pages of a gazette that runs to
35,335, so they are under 0.1% of the archive. At 10.2 iulaan per listing page,
the realistic 5,000-page target is **51,000 iulaan** (the full archive would be
about 360,000).

Assuming every iulaan carries a PDF, 5 pages typical, with about 300 long
documents - laws, schemes - at 60 pages:

| | pages |
|---|---:|
| 50,700 short documents at 5 pages | 253,500 |
| 300 long documents at 60 pages | 18,000 |
| **total** | **271,500** |

At roughly 2,000 input tokens (text plus page image) and 1,500 output tokens
(verbatim Thaana) per page, one page costs $0.0095 at list and **$0.00475
batched**.

#### Measured, not assumed

A 44-PDF sample drawn across the whole ID space (IDs 20,000 to 290,822) was
downloaded and run through `pdfinfo` and `pdftotext`:

| Metric | Value |
|---|---|
| Scanned (under 200 extracted chars/page) | **20 of 44 = 45%** |
| Pages per PDF | mean 4.5, median 2, p90 10, max 28 |
| Pages per PDF, scanned only | mean **3.2** |
| Pages per PDF, text-layer only | mean 5.7 |
| File size | mean 0.93 MB, median 0.40 MB, p90 2.66 MB |

Three findings change the model, and two correct earlier drafts of this spec:

- **File size does not predict whether a PDF is scanned.** A 58 KB single page
  came back scanned; a 1.1 MB, 28-page document had a complete text layer. Any
  size-based heuristic for routing is wrong - run `pdftotext` on every PDF, which
  is free and fast, and route on extracted characters per page.
- **An earlier claim in this spec was wrong.** It reasoned that most gazette PDFs
  must be Word exports because 73 `.docx` attachments sit alongside them. They are
  not: 45% are scans of signed letters. The `.docx` files are Word; the PDFs
  frequently are not.
- **Scanned documents are shorter than text-layer ones** (3.2 vs 5.7 pages), so
  the expensive path lands on the smaller documents. Only **32% of all PDF pages**
  need transcription, not 45%.

#### Revised volumes and cost

From the measured attachment ratios (239 of 306 iulaan carry attachments, 1.41
files each, 78% of files are PDFs), 51,000 iulaan implies about 56,000 attachment
files: **43,500 PDFs** (195,750 pages) and **12,500 `.docx`/`.xlsx`** that cost
nothing. Of the PDFs, roughly 19,600 are scanned, giving 62,600 pages, plus about
7,500 more from the 300 long documents - **70,100 pages** reaching Claude.

| Document gate | pages | batched | list |
|---|---:|---:|---:|
| Everything | 70,132 | **$333** | $666 |
| Jobs + stub bodies (46%) | 32,261 | **$153** | $306 |
| Jobs only (34%) | 23,845 | **$113** | $227 |

The document gate uses the current corpus's own distribution: jobs are 34% of
iulaan (93 `ވަޒީފާގެ ފުރުޞަތު` plus 11 `Job Opportunity`), and 18% of all bodies
run under 500 characters - genuine stubs where the PDF *is* the content. Transcribe
every job; transcribe everything else only when its body is a stub.

Long documents remain not worth optimizing: they are about 11% of transcribed
pages. And the 20-page chunking below is a correctness requirement, not a cost one.

**Order of operations:** jobs first (~$113), then stub-bodied news, then the rest
only if it proves worth it. The jobs slice delivers the entire reason this
subsystem exists. Re-measure the scanned fraction on a larger sample during P3 -
44 PDFs is enough to plan with and not enough to budget to two significant figures.

Two things this scale changes elsewhere:

- **Long documents must be chunked.** A 60-page document would emit about 90,000
  output tokens, over Haiku 4.5's 64,000 `max_tokens` ceiling. Chunk at 20 pages
  per request. Documents over 100 pages also exceed the per-PDF page limit at this
  context size and must be split regardless.
- **Enrichment is not the expensive half.** 51,000 iulaan through
  `deepseek-v4-flash`, with attachment text capped at 20,000 characters, is about
  **$86** off-peak. Transcription dominates it roughly 15 to 1. Attachment text
  storage is about 1 GB raw, near 290 MB after TOAST compression, and section 12.1
  keeps it out of the search index entirely, so it never enters the hot set.

#### Sourcing

Attachments live on a public GCS bucket at
`storage.googleapis.com/gazette.gov.mv/docs/iulaan/<id>.pdf`, and two properties
of it are worth knowing:

- **Objects are public but the bucket is not listable** - anonymous
  `storage.objects.list` is denied, so there is no enumerate-the-bucket shortcut.
- **Object names are dense sequential integers.** 44 of 56 probed IDs across the
  range resolved (79%), so a `HEAD` per ID is a cheap existence-and-size check.
  Useful for sizing a backfill or prefetching in ID order.

It is not a substitute for the detail pages, though: the bucket gives bytes, not
the iulaan the file belongs to nor its label (`vazeefa ah edhey form` versus
`iulaan`), and 5.6 depends on those labels to route attachments. Detail pages
remain the metadata source; ID probing is a prefetch and sizing aid.

### 5.6.3 One correction to the proposed chain

The natural instinct is to pipeline it: transcribe with Claude, translate with
GemmaTranslate, then extract with DeepSeek. **Do not make translation a
prerequisite for extraction.** DeepSeek reads Thaana directly, and each hop
compounds error - a transcription slip becomes a translation slip becomes a wrong
salary on a card, with the grounding validator checking against text that was
already corrupted two stages back.

The correct shape is a fork, not a chain:

```
attachment -> transcribed Thaana text
                |
                +--> enrichment input (DeepSeek reads Thaana directly)
                |    and the grounding validator's source text
                |
                +--> translation (display only: title_dv/_en, summary_dv/_en)
```

Translation serves display. Extraction reads the original. The validator checks
against the original. Nothing downstream depends on a translation being right.

```python
class Attachment(models.Model):
    iulaan      = ForeignKey(Iulaan, related_name='attachment_files')
    label_raw   = CharField()
    role        = CharField()   # main | application_form | annex | unknown
    url         = URLField()
    content_sha = CharField(max_length=64)
    mime        = CharField()
    text        = TextField(blank=True)      # extracted, capped at 20k chars
    page_count  = IntegerField(null=True)
    method      = CharField()   # docx | pdftotext | transcribed | none
    status      = CharField()   # pending | ok | ocr_failed | fetch_failed
    transcribed = BooleanField(default=False)   # drives quality + the card flag
```

**The file itself is not kept.** Fetch, extract, store the text and a checksum,
discard the bytes. The URL is stable and public, so re-fetching is always
possible, and storing thousands of PDFs to serve content we do not own buys
nothing while costing the disk the section 12 budget does not have.

Extracted text then flows three ways: appended to the enrichment input for that
iulaan (so `JobAttrs.compensation` can be filled from the salary table in the
PDF), added to the document's search vectors, and validated against - the
grounding validator's source text becomes listing plus attachment text, so a
salary figure found only in the PDF passes while an invented one still fails.

`content_hash` on `EnrichedRecord` covers the attachment checksums too, so a
re-published PDF triggers re-enrichment and an unchanged one does not.

Fetching is rate-limited and runs as its own management command
(`extract_attachments`), never inline with a request or a sync.

### 5.7 Gazette documents are write-once

A gazette iulaan is a published government notice. It does not change after
publication, so once transcribed and enriched it is **never reprocessed**. There is
no staleness check, no periodic re-crawl, and no re-transcription. The only path
back into the pipeline is a manual one.

This is enforced in code, not just policy, because the failure mode costs money:

- **Transcription is guarded by existence.** An `Attachment` with `status='ok'` is
  never sent to Claude again unless the document is explicitly marked stale below.
  `ocr_failed` is likewise terminal - a document that missed the CER bar stays
  missed until marked.
- **`prompt_version` bumps do not backfill gazette.** Section 4.2 re-enriches when
  `content_hash` changes or `prompt_version` is bumped; for `source='gazette'` the
  second trigger is disabled. Improving the extraction prompt therefore improves
  only newly-ingested iulaan, by design.

#### The manual override

`SearchDocument.stale_marked_at` is the single reprocess trigger, and it is meant
to be driven straight from SQL:

```sql
-- one document
UPDATE search_searchdocument SET stale_marked_at = now() WHERE id = 12345;

-- backfill a prompt improvement across a slice
UPDATE search_searchdocument SET stale_marked_at = now()
WHERE source = 'gazette' AND doc_type = 'job' AND published_at > '2026-01-01';
```

Every pipeline stage treats a non-null `stale_marked_at` as an unconditional
override - it outranks `status='ok'`, the `content_hash` gate, and the disabled
`prompt_version` trigger, including re-transcription of an `Attachment` that had
already succeeded. The stage clears the field on success, so the flag is a one-shot
work ticket rather than persistent state. Because a `WHERE` clause can mark 51,000
rows as easily as one, the management commands keep `--limit` and report the count
they are about to process before spending anything.
- **iBay keeps its `content_hash` gate.** Product listings genuinely do change and
  expire, so nothing above applies to them.

One structural benefit: since `source` is the partition key (12.2), the gazette
partition is exactly the write-once set. No updates means no row churn, no vacuum
pressure, and BRIN on `published_at` stays maximally effective - while the iBay
partition, which does churn, is physically separate and cannot drag it down.

Reclassification (3.2) is the one exception, and it is an in-partition `UPDATE` of
`doc_type` rather than a partition migration - which is precisely why `source` is
the partition key and `doc_type` is not.

#### User-reported staleness

The end user is the staleness signal. A result card carries a report action, and
reports queue for review:

```python
class DocumentReport(models.Model):
    document    = ForeignKey(SearchDocument, related_name='reports')
    reason      = CharField()   # stale | wrong_details | dead_link | spam | other
    note        = TextField(blank=True)
    reporter_ip_hash = CharField(max_length=64)   # rate limiting only
    status      = CharField()   # open | actioned | rejected
    created_at  = DateTimeField(auto_now_add=True)
```

**A report must never trigger reprocessing on its own.** The endpoint is public and
transcription plus enrichment cost real money per document, so auto-reprocessing
would be a billable denial-of-wallet vector: anyone could loop the endpoint and
spend the API budget. Reports are therefore inert data. They rate-limit per IP
hash, they deduplicate per (document, reason), and an admin action - not the report
- is what re-queues a document. The admin queue sorts by report count so genuinely
broken records surface first.

Cheap actions can bypass the queue safely because they cost nothing: a
`dead_link` report can be verified by a `HEAD` request and flip `is_active` without
human review. Only the paid paths need the gate.

## 6. Dhivehi language pipeline

This is the part that makes or breaks the product. `search/lang/`, four modules,
all pure functions with table-driven tests.

### 6.1 `script.py` - detection

- Thaana, U+0780-U+07BF present, gives `dv-Thaa`.
- Latin with Dhivehi markers gives `dv-Latn`, transliterated Dhivehi. This is
  common in the real data: "Halaalukuvefa hunna Front load washing machine
  beynun" is a live product title. Detection scores a marker wordlist (`beynun`,
  `vikkanee`, `kuyyah`, `hunna`, `laari`, `rufiyaa`, and so on) plus digraph
  frequency (`aa`, `ee`, `oo`, `dh`, `th`, `lh`, `gn`, `sh`). Thresholds, no ML.
- Latin that decodes cleanly under the Thaana keyboard layout gives `dv-Keys`
  (6.4). Tried before the phonetic check, because it is exact rather than
  heuristic.
- Otherwise `en`.
- Labels are per-token, not per-query, because real queries are mixed:
  "iPhone 13 vikkan" is half English and half Dhivehi.

### 6.2 `normalize.py`

- NFC, strip zero-width characters, fold punctuation, collapse whitespace,
  casefold Latin, map Arabic-Indic digits to ASCII.
- Strip HTML. Gazette bodies are raw markup (see 5.6), and indexing `td`,
  `valign` and `strong` as lexemes would poison both the vocabulary and the
  ranking.
- **Fili handling**: removing U+07A6-U+07B0 yields a consonant skeleton, which is
  the highest-impact recall trick available for Thaana, because users type fili
  inconsistently or omit them entirely and the same word appears with different
  fili across documents.

**Skeleton-only indexing is rejected**, because it collides genuine minimal pairs:
ހަކަތަ and ހިކަތި both reduce to ހކތ, and a search for one would rank the other
identically. Instead `vector_dv` indexes **both forms in one tsvector at different
weights**:

```
vector_dv = setweight(to_tsvector('simple', fili_preserved), 'A')
         || setweight(to_tsvector('simple', fili_stripped),  'C')
```

A query is expanded the same way, so:

- ހަކަތަ typed with correct fili hits the weight-A lexeme and outranks ހިކަތި,
  which only collides on the weight-C skeleton. Precision is kept.
- ހަކަތަ typed with wrong or missing fili still hits the weight-C skeleton and
  the document is still found, just ranked below exact-fili matches. Recall is
  kept.

The cost is roughly double the lexemes in `vector_dv` alone, which is affordable
given that section 12 removes body text from the index entirely. No extra column
and no extra index.

`SEARCH_DV_INDEX_MODE` takes `dual` (default), `skeleton` or `fili`, and the A/C
weights are settings. Changing the strategy is a settings change plus a reindex,
never a migration, so this stays reversible in both directions permanently rather
than being a decision that hardens. The minimal-pair cases go in the evaluation
set (section 13) so a regression shows up as a number rather than as a complaint.

### 6.3 `translit.py`

Bidirectional Thaana and Latin, table-driven. The mapping is many-to-one in both
directions, so it generates **variant sets**, not single strings: `ށ/ސ/ޝ` to
`sh`/`s`, `ތ/ޓ` to `th`/`t`, `ދ/ޑ` to `dh`/`d`, long vowels to `aa`/`ee`/`oo`.

Used at index time to build `vector_latin` and `title_latin` for Thaana
documents, and at query time
to produce Thaana candidates from a Latin query. This is the mechanism that
makes `kuyyah dhinun` find `ކުއްޔަށް ދިނުން`.

A wrong entry in this table is the most likely source of silent recall loss, so
it gets a golden-file test built from ground truth already in the database: the
`Office.name` and `Office.translated_name` pairs, plus the 676 `TranslationCache`
entries.

### 6.4 `keymap.py` - Thaana keyboard-layout input

Many Maldivians without a Thaana keyboard installed type Dhivehi in **keyboard
space**: the Latin key sequence that would produce Thaana under the standard
layout. `migotawq` is `މިގޮތައް`, `liyegenq` is `ލިޔެގެން`, `wewqcewq` is
`އެއްޗެއް`. This is not phonetic transliteration - it is a strict 1:1 character
mapping, and the two must never be confused (see below).

The corpus contains exactly **49 distinct Thaana codepoints** (38 consonants, 11
fili), so the table is 49 entries and the mapping is a **bijection** - which makes
it simpler than the phonetic table in 6.3, not harder. That one is many-to-one in
both directions and needs variant sets; this one is exact.

**Detection is decisive rather than heuristic**, and this is the nice property:
attempt the full decode, and if every character maps and the result is well-formed
Thaana (valid consonant-plus-fili structure), the input was keyboard space. No
marker wordlist, no scoring threshold, unlike the `dv-Latn` detection in 6.1. A
string that fails to decode cleanly simply is not keyboard space.

So it becomes a third query input mode: a query decodes to Thaana and then flows
through the normal Thaana path - fili-preserved and skeleton forms, the same
`vector_dv` weighting, everything. About 60 lines of work.

**It is a query-side decoder only. It is never a storage or index format**, for
three reasons, the first of which is decisive:

1. **It destroys model comprehension.** Language models are trained on Thaana
   script, because Dhivehi web text exists in Thaana. Keyboard-layout
   transliteration is an input-method artifact that barely appears in training data
   at all - `wasqsalAmq` is not a word any model has learned. Storing it would break
   enrichment (5.1), transcription (5.6) and translation (5.5) simultaneously. Worse,
   it would break the **grounding validator** (5.2 layer 3), which checks extracted
   values against the source text: a model that cannot read the source cannot be
   held to it, and the single strongest correctness guarantee in the design would
   evaporate.
2. **It would collide with the phonetic Latin-Dhivehi already in the corpus.**
   `text_latin` and `vector_latin` hold *phonetic* Latin Dhivehi - real titles like
   `firihen kudhin bahattaden` and `Halaalukuvefa hunna`. Keyboard space is also
   Latin characters, and the two encodings disagree on nearly every letter: `w` is
   `އ` in keyboard space and a literal `w` phonetically. Mixing them in one column
   is silent corruption, not a theoretical concern.
3. **The storage saving is real but modest, and aimed at a non-problem.** Measured
   on the corpus's 847,346 Thaana characters: keyboard space is 50% smaller
   before compression but only **34% smaller after** zlib at TOAST-like settings.
   Section 12.1 already removed body text from the index, so the Dhivehi index is
   not the memory constraint. Trading the grounding validator for a third of a
   share of 194 MB is a bad trade.

Postgres, for its part, is indifferent: the `simple` configuration already tokenizes
Thaana correctly because Dhivehi is space-separated, and `pg_trgm` operates on any
Unicode. There is no tokenizer problem to solve here.

**One caveat that argues for query-side placement specifically.** The encoding is
unforgiving - one wrong keystroke yields a different word, with no graceful
degradation. `wasqsalAmq` decodes to `އަސްސަލާމް`; `އައްސަލާމް` requires
`wawqsalAmq`. At query time the trigram and fili-skeleton layers already absorb that
class of error. At index time nothing would.

Two incidental benefits: the bijection gives an exact oracle for generating 6.3's
phonetic golden-file fixtures, and an ASCII-safe slug form for URLs.

### 6.5 `expand.py`

Produces a `QueryPlan`:

```python
QueryPlan(
    raw, lang,                        # detected primary language
    terms_en, terms_dv, terms_latin,  # expanded term sets
    phrases,                          # quoted, never expanded
    filters,                          # parsed field:value operators
    response_lang,                    # mirrors lang; drives the UI
)
```

Expansion runs cheapest-first and short-circuits once it has enough signal:

1. normalize and tokenize
2. **keyboard-space decode** (6.4) - attempted first among the Latin paths because
   it either succeeds exactly or fails cleanly, so it costs nothing to try and
   removes ambiguity before the heuristic phonetic path runs
3. transliterate phonetically (pure function, no cost)
4. alias lookup - `enrich` keywords plus a curated `search.QueryAlias` table
   ("cell phone", "mobile", "phone", `ފޯނު`)
5. translation call, only for terms unresolved by 1 to 4, and cached

Latency target: p50 under 150ms with a warm cache. Repeat queries and purely
transliterable queries never touch a model.

## 7. Retrieval and ranking

One SQL query per request for results, one for facet counts. No application-side
merging.

```
score = w1 * ts_rank_cd(vector_en, tsquery_en)
      + w2 * ts_rank_cd(vector_dv, tsquery_dv)
      + w3 * ts_rank_cd(vector_latin, tsquery_latin)
      + w4 * similarity(title_norm, q_norm)     -- trigram: typos, partial words
      + w5 * same_language_bonus                -- match in the query language wins
      + w6 * freshness(published_at)            -- exponential decay
      + w7 * quality
      + w8 * exact_phrase_bonus
      - penalty(expired, inactive, scrape_error)
```

- Weights live in `settings.SEARCH_RANKING`, tunable without a migration.
- Candidate generation ORs the three tsqueries with a trigram threshold, takes
  `LIMIT 500`, then scores and sorts. Constant cost as the corpus grows, and the
  reason `work_mem` can stay at 8 MB (see 12.3).
- Snippets are the stored `summary_en`/`summary_dv`, not `ts_headline`. No body
  text is read at query time, by design (12.1).
- Freshness half-life per type: news 7 days, jobs 14 days, shopping 30 days,
  property 45 days. News decaying fastest is the entire point of having a news
  tab; property listings stay relevant longest.
- Facets aggregate over the same candidate CTE, so counts always match the
  result set.
- Zero results relax progressively: drop the rarest term, then lower the trigram
  threshold, then suggest alternatives from a term-frequency table. A results
  page is never empty with no way forward.

## 8. Tabs, facets, cards

Tabs: `All`, `Shopping`, `Jobs`, `Property`, `News`, `Images`.

`All` interleaves types with a cap of three consecutive results from one type,
so 16k shopping listings cannot bury 306 iulaan. `Images` runs the same query and
returns flattened `thumbnails`, sourced from `ProductImage` and gazette image
attachments.

Each `doc_type` gets its own card component, its own detail treatment, and its own
facet set. The `card` JSONB payload carries exactly what its card renders, already
resolved to the response language, so the frontend does no formatting decisions
and no joins. `card_version` bumps when a card's field set changes, which triggers
a reindex rather than a runtime lookup.

**Nothing time-dependent goes in `card`.** Gazette documents are written once and
never reprocessed (5.7), so any value derived from "now" and frozen at index time
is wrong the day after it is written - a closed vacancy would advertise itself as
open indefinitely. `card` stores raw dates; `deadline_state`
(`open | closing_soon | closed`), freshness decay and relative-time labels are all
computed per request from `expires_at` and `published_at`. This is a correctness
constraint, not a preference, and it is the one thing the write-once decision
actually costs.

### 8.1 Jobs

At a glance: **role**, **employer**, **salary**. Nothing else competes for space.

```
card = {
  role, employer, employer_logo,
  salary_display,        # "MVR 10,750 / month" | "Negotiable" | "Unlisted"
  salary_state,          # listed | negotiable | unlisted
  net_estimate,          # {value, is_floor, working_days, completeness} | null
  compensation,          # the line items, so the client can recompute
  grade, location, position_type,
  deadline,              # the raw date only; state is computed at query time
  apply_kinds,           # ['form','email'] -> icon row on the card
  detail_source,         # listing | attachment  -> "Details from attached PDF"
  source_label,          # "Gazette" | "iBay"
}
```

`salary_display` is resolved server-side into one of three strings, never a null
the frontend has to interpret. "Negotiable" appears only when the source says so;
absence is "Unlisted".

`net_estimate` renders beneath it, visually subordinate and explicitly
approximate: `~MVR 14,647 take-home` with the assumptions (20 working days, 7%
pension) reachable without leaving the card. When `completeness` is `partial` it
renders as a floor - "at least ~MVR X" - and when the estimate would just restate
basic salary it is omitted entirely rather than padding the card with a fake
calculation.

`detail_source` exists because many gazette jobs carry their real content in an
attachment (5.6). A card whose qualifications and salary came out of a PDF says so,
which is both honest and a useful signal that the original document is worth
opening.

Detail: minimum qualifications as a list, the compensation breakdown as a table of
line items with the take-home calculation shown as arithmetic the user can follow
and a working-days control that recomputes it client-side, and the apply block
rendering `apply_methods` as actionable elements - a button for a Google or
Microsoft form, a `mailto:` for email, a tap-to-call and Viber link for phone
numbers. Plus deadline, grade, position type, attachment links, and a link to the
original posting.

Facets: job category, position type, take-home range, salary state, employer,
grade, deadline (open, closing soon), location, source.

### 8.2 Property

At a glance: **location**, **rent per month**, **capacity**, **one image**.

```
card = {
  hero_image, image_count,
  location_display,       # "Hulhumale Phase 2" | "Male, Maafannu"
  rent_display,           # "MVR 7,000 / month" | "USD 450 / month" | "MVR 300 / day"
  currency, currency_inferred,
  capacity_display,       # see below
  unit_kind, is_shared,
  bedrooms, bathrooms, furnishing,
  tenant_preference,      # chips: "Families", "Male only", "Expatriates"
}
```

`capacity_display` is computed from `Occupancy` and is the field most likely to
mislead if done carelessly, so it states the shape explicitly:

| Occupancy | `capacity_display` |
|---|---|
| whole_unit, rooms_total 3 | "Whole unit, 3 rooms" |
| room, 1 of 3, shared | "1 room of 3, shared" |
| bed_space, beds_offered 2 | "Bed space, 2 available, shared" |
| guest_house, max_occupants 4 | "Guest house room, up to 4" |

A listing offering one room of three must never render as a three-bedroom unit.
That is the concrete failure this table exists to prevent.

Detail: full description, image gallery, contact numbers as tap-to-call and Viber
links, room facilities as chips, floor, lift, square feet, tenant preference.

Facets: listing kind, rent range (per period, currency-aware), unit kind, shared
or whole, bedrooms, bathrooms, furnishing, neighborhood, island/atoll, lift,
square feet range, tenant preference.

Rent ranges must bucket per `price_period` and per currency separately. A
300-per-day guest house room and a 7,000-per-month apartment on one slider is
meaningless.

### 8.3 Shopping

A full commerce browse experience: image-forward result grid, and filters that
change with the query.

```
card = {
  hero_image, image_count,
  title, price_display, currency, negotiable,
  condition,              # badge
  brand, location,
  seller_name, seller_is_premium,
  spec_chips,             # up to 3 highest-priority specs: "24V", "120W"
}
```

Detail: image gallery, full spec table from `DocumentSpec` (including
non-facetable keys), description, seller card, contact actions.

**Dynamic facet discovery** runs over the candidate set from section 7, after
retrieval and before pagination:

1. Aggregate `DocumentSpec` rows joined to the candidate CTE, grouped by
   `SpecKey`, keeping only `is_facetable` keys.
2. Discard any key present in fewer than 8 results or under 5% of them. Sparse
   keys make noisy filters.
3. Discard any key whose values are effectively constant across the result set. A
   filter that cannot partition the results is dead UI, and this is the check
   most implementations skip.
4. Score the survivors by `coverage x distinctiveness`, where distinctiveness is
   the normalized entropy of the value distribution.
5. When at least 70% of candidates share one leaf category, override the ordering
   with that category's curated `SpecKey.priority`. This is what makes a phone
   search reliably lead with brand, storage and condition instead of whatever
   happened to be dense that day.
6. Emit at most 8 facets plus a "more filters" group, each shaped by `widget`:
   - `numeric` gives min, max and a histogram (10 buckets) for a range slider
   - `enum` gives the top 12 values by count, alias-collapsed, as checkboxes
   - `bool` gives a toggle with its true-count

So "power supply" surfaces voltage, amperage and wattage ranges because
`DocumentSpec` holds `24V`, `5A` and `120W` parsed out of titles, while "iphone"
surfaces brand, storage and condition checkboxes. Same code path, different data.

Universal shopping facets (price, condition, category, location, has images,
seller type, date) are always available and are not subject to the discovery
thresholds.

### 8.4 News - and the default bucket

No detail page. A news result links straight to the source article; building an
internal reader for content we do not own is work that helps nobody.

This is also where every unclassified document lands (5.3), so the card has to
stand on its own with no typed attributes behind it:

```
card = {
  source,                 # registry key -> icon + label via /meta
  title,                  # descriptive, from enrichment
  summary,                # the excerpt; carries the whole result
  office, announcement_type, published_at,
  external_url, attachment_count,
}
```

Four things and nothing else: **icon, title, excerpt, link out.** The frontend
renders the whole card as an outbound anchor with `rel="noopener noreferrer"`.
Facets: source, office, announcement type, date range, has attachments, tender or
auction.

### 8.5 Source attribution

Every result carries its source icon - result cards of all four types, detail
pages, the Images tab, suggest rows, and the source facet. `card.source` holds the
registry key; the frontend resolves it to an icon and a language-appropriate label
through `/meta` (4.3.3, section 9).

Rendering rules, so it stays a consistent visual system rather than a scattered
decoration: 16px in dense contexts (suggest rows, facet lists) and 20px on cards,
always paired with the source label rather than standing alone as a rebus, and
degrading to the monogram chip when a source has no usable icon. In mixed-script
result sets the icon anchors the leading edge of the card, which flips with `dir` -
so it is placed by logical property (`inline-start`), never by `left`.

The consequence for the API is that `/documents/{id}` serves shopping, jobs and
property only. News never needs it.

## 9. API

django-ninja rather than DRF, because the Pydantic attribute schemas are already
the source of truth and ninja's OpenAPI output generates the frontend's
TypeScript types from them directly. One definition, end to end.

```
GET /api/v1/search
    ?q=&type=&page=&per_page=&sort=&lang=&<facet filters>
    -> { query:   {raw, detected_lang, response_lang, expanded_terms},
         total, page, per_page,
         results: [{id, doc_type, url, title, summary, card, score}],
         facets:  [ {key, label, widget, ...}, ... ],
         suggestions: [...] }

GET  /api/v1/suggest?q=         autocomplete, trigram over a term table, both scripts
GET  /api/v1/documents/{id}     shopping, jobs and property only; news links out
GET  /api/v1/meta               tab, label and source registry; nothing is hardcoded
                                sources: [{key, label_en, label_dv, icon,
                                           icon_fallback_text, accent, site_url}]
POST /api/v1/documents/{id}/report
     {reason, note} -> 202      queues a DocumentReport; never reprocesses (5.7)
```

The report endpoint is rate-limited per IP hash and always returns 202 regardless
of whether the report was new or a duplicate - telling a caller which documents
they have already reported leaks nothing useful and invites probing.

`facets` is an ordered list, not a map, because for shopping the set and the
ordering are computed per query by section 8.3 and that ordering is meaningful.
Each entry is one of:

```
{key, label, widget: 'checkbox', values: [{value, label, count}]}
{key, label, widget: 'range', unit, min, max, histogram: [{from, to, count}]}
{key, label, widget: 'toggle', count_true}
```

Filter parameters are `key:value` for enums, `key:min..max` for ranges. The
frontend does not need to know which facets exist for which query, which is the
whole point.

`title` and `summary` resolve server-side to the response language, falling back
to the other language with a `translated: true` flag. The frontend never has to
choose. Every label ships both `label_en` and `label_dv`.

Offset pagination for v1; cursor pagination is a later change if deep paging
becomes real.

## 10. Frontend

Next.js App Router, TypeScript, types generated from the OpenAPI schema.

- `dir="rtl"` and `lang="dv"` applied per element on Thaana content, not as a
  page-level flip. Results are frequently mixed-script and a page-level flip is
  the thing that gets this wrong.
- A self-hosted Thaana webfont (Faruma or MV Faseyha class).
- Query language sets the UI chrome language; a manual toggle overrides and
  persists.
- Server components render the results page for first paint and SEO; facet
  interaction is client-side.
- One card component per `doc_type`, mapping 1:1 to the `card` payload.
- Source icons come from `/meta`, fetched once and held for the session, so a card
  never issues its own request for an icon. Icons are self-hosted static assets
  (4.3.3) served with a long cache lifetime; the set is a handful of files.
- Out of scope for v1: infinite scroll, saved searches, accounts.

## 11. Containerization

Written already, and validated with `docker compose config`:

```
compose.yml               development
compose.prod.yml          production
docker/api.Dockerfile     multi-stage: base -> dev | prod
docker/web.Dockerfile     multi-stage: base -> deps -> dev | build -> prod
docker/Caddyfile          production reverse proxy
.dockerignore             keeps venv/, db.sqlite3 and .jj/ out of the context
```

`compose.yml`, development:

- `db` - `postgres:18-alpine`, `pg_isready` healthcheck, named volume, port
  published to the host for direct psql access.
- `api` - built from `docker/api.Dockerfile`, runs `manage.py runserver`. The
  repository is bind-mounted, so Django's own autoreloader handles hot reload.
  `develop.watch` additionally rebuilds the image when `requirements.txt`
  changes.
- `web` - built from `docker/web.Dockerfile`, runs `next dev`. `./web` is
  bind-mounted with anonymous volumes masking `node_modules` and `.next`, so
  Next's hot module replacement works. `develop.watch` rebuilds on
  `package.json`.
- The `web` service sits behind a Compose profile until `web/` is scaffolded in
  phase 5, so `docker compose up` works from day one with just `db` and `api`.
- Ollama is deliberately not containerized. It runs on the separate 9070 XT
  machine and is reached over `OLLAMA_URL`.

`compose.prod.yml`, production:

- `db` - same image, no published port, named volume, healthcheck, and the
  section 12.5 tuning passed as `-c` flags so box sizing is one env change.
- `pgbouncer` - transaction-mode pooler in front of Postgres (12.4).
- `release` - a one-shot service that runs `migrate` and `collectstatic` into a
  shared volume, then exits. `api` waits on
  `condition: service_completed_successfully`, so deploys cannot serve traffic
  against an unmigrated database.
- `api` - gunicorn with uvicorn workers against `beynunehcheh.asgi`. ASGI rather
  than WSGI because the translation and enrichment clients are async.
- `web` - Next standalone output run under node.
- `caddy` - reverse proxy and automatic TLS. Serves `/static` and `/media` from
  the shared volumes directly, proxies `/api` and `/admin` to `api`, and
  everything else to `web`.
- `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, `SITE_ADDRESS`, `ACME_EMAIL` and
  `DEEPSEEK_API_KEY` are declared with `:?` so a missing value fails the deploy
  instead of silently defaulting.
- `restart: unless-stopped` and healthchecks throughout.

Two follow-ups these files depend on, both in phase 1:

- `settings.py` currently hardcodes `SECRET_KEY`, `DEBUG = True` and
  `ALLOWED_HOSTS`. The compose files already pass `DJANGO_SECRET_KEY`,
  `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` and `DATABASE_URL`; settings has to
  start reading them.
- `llama_cpp_python` is only needed when `OLLAMA_URL` is unset, and building
  llama.cpp inside an image costs minutes for nothing when inference lives on
  another host. `docker/api.Dockerfile` currently filters it (plus
  `huggingface_hub` and `hf-xet`) out of `requirements.txt` at install time.
  Phase 1 replaces that filter with a real split into `requirements.txt` and
  `requirements-local-llm.txt`.

## 12. Scaling and the memory budget

Design target: **5 million documents on a 4 GB box**, with a path to more by
adding disk rather than RAM. The corpus is 20,751 documents today, so this is
about not building in a ceiling.

The governing principle: Postgres does not need its indexes in RAM, it needs the
*working set* in page cache. Every decision below either shrinks the working set
or stops a query from being proportional to corpus size.

### 12.1 What is deliberately not stored

**Body text is not in the index.** The largest single saving. `SearchDocument`
holds titles, summaries and tsvectors; the source text stays in `ibay` and
`gazette` where it already is. This works because result snippets come from the
LLM-generated `summary_en`/`summary_dv` rather than `ts_headline`, which would
require reading the body on every result row. A written summary is a better
snippet than a keyword-window anyway, so the RAM-frugal choice is also the better
product choice.

**Trigram indexes cover titles only.** GIN trigram indexes are frequently larger
than the data they index, since they store every three-character window. On
titles (~60 characters) that is affordable; on bodies it would dominate the
database. An earlier draft of this spec indexed `text_latin` in full - that is
corrected: `title_en`, `title_dv` and `title_latin` only.

**Attachment files are not stored**, only their extracted text (5.6).

### 12.2 Structural decisions

- **LIST partition `SearchDocument` by `source`, not by `doc_type`.** An earlier
  draft partitioned by `doc_type`, which is wrong for two reasons that only became
  visible once reclassification became a supported operation (3.2):

  1. **Postgres requires the partition key in every unique constraint** on a
     partitioned table. Partitioning by `doc_type` would force the identity key to
     become `(source, source_key, doc_type)`, which permits the same document to
     exist twice under two different types - destroying the identity guarantee that
     3.1 depends on.
  2. **`doc_type` is mutable.** Every reclassification would migrate rows between
     partitions (legal since Postgres 11, but internally a delete plus insert), and
     a partition key that changes is a partition key chosen badly.

  `source` is immutable, it is already part of the identity key, and it matches the
  real size skew: gazette goes to 51,000 and then 360,000 while iBay sits at 20,000.
  Tab filtering then uses the btree on `(doc_type, published_at DESC)` instead of
  partition pruning, which at these sizes is not the bottleneck - the `LIMIT 500`
  candidate cap is.
- **Partial indexes: `WHERE is_active`.** Expired listings accumulate forever and
  nobody searches them. Keeping them out of the GIN indexes means index size
  tracks the live corpus, not the historical one.
- **An archive partition** for documents inactive beyond a retention window,
  carrying no GIN indexes at all. Reachable by direct lookup, absent from search.
  This is what keeps the hot index bounded no matter how long the crawlers run.
  Gazette rows are never updated after ingest (5.7), so the gazette partition is
  append-only: no row churn, no vacuum pressure, and BRIN on `published_at` stays
  maximally effective.
- **BRIN, not btree, on `published_at`.** Date-range filtering over
  append-mostly data at a few kilobytes per partition.
- **`DocumentSpec` is the table that grows fastest** - roughly four rows per
  shopping or property document, so about 20M rows at the 5M-document target.
  Only those two types produce specs. Indexed on `(key_id, value_num)` and
  `(key_id, value_text)`. If it passes 50M rows, partition it by `key_id`; not
  before.

### 12.3 Why queries stay flat

- The `LIMIT 500` candidate cap from section 7 is a memory decision as much as a
  latency one. Ranking, facet aggregation and interleaving all operate on 500
  rows, so `work_mem` can stay small and no sort ever spills to disk. Query cost
  is independent of corpus size.
- Facet discovery aggregates 500 candidates, never the full match set.
- The `card` payload means a result page performs zero joins.

### 12.4 Process memory

- **PgBouncer in transaction mode**, because each Postgres backend costs 5-10 MB
  and an async Django app can open a lot of them. Postgres runs
  `max_connections=30` rather than the default 100, with PgBouncer multiplexing
  in front at `DEFAULT_POOL_SIZE=20`.
- **Two database aliases, and this matters.** Transaction-mode pooling forbids
  server-side cursors and cannot run DDL, so `default` points at PgBouncer for
  web requests while `direct` points at Postgres for management commands and
  migrations. `reindex`, `enrich_documents`, `extract_attachments` and `migrate`
  use `direct`; everything serving traffic uses `default`. Wiring both to the
  pooler would break streaming reindex, and wiring both direct would defeat the
  memory saving.
- **Streaming reindex.** `.iterator(chunk_size=500)` over the `direct` alias with
  batched `bulk_update`. Never `list()` a queryset. Reindexing 5M documents uses
  the same memory as reindexing 5,000.
- **Enrichment adds zero memory to the app server**, because it runs on DeepSeek.
  The RAM constraint is a second, independent argument for the section 5.1
  decision.
- **Attachment transcription adds none either.** Claude takes the PDF file
  directly (5.6.1), so nothing is rasterized locally - the process streams a file
  to an API and writes back text. PDF rasterization would have been the single
  most memory-hungry operation in the system.
- **Next.js serves source image URLs directly**, with the image optimizer off.
  Next's optimizer caches derivatives on disk and holds them in memory; iBay and
  gazette images are already on hosted CDNs, so optimizing them locally spends
  the budget to re-host someone else's files.
- Gunicorn worker count scales with the box, not with taste: 2 workers at 4 GB.

### 12.5 Postgres configuration by box size

| Setting | 2 GB | 4 GB | 8 GB |
|---|---|---|---|
| `shared_buffers` | 512 MB | 1 GB | 2 GB |
| `effective_cache_size` | 1 GB | 2.5 GB | 6 GB |
| `work_mem` | 4 MB | 8 MB | 16 MB |
| `maintenance_work_mem` | 128 MB | 256 MB | 512 MB |
| `max_connections` | 20 | 30 | 50 |
| gunicorn workers | 2 | 2 | 4 |

`work_mem` stays deliberately small; the 500-row cap means nothing needs more.
`maintenance_work_mem` is raised temporarily for index builds and lowered again -
GIN index construction is the one genuinely memory-hungry operation, and it is an
offline one. Index builds use `CONCURRENTLY`.

### 12.6 Projected database size

Built from measured inputs: 4.5 pages per PDF, ~2,700 extracted characters per
page, 0.93 MB mean PDF size, 5,569-character mean gazette body, and the 20,000
character cap on `Attachment.text`. Text columns are assumed to compress about
3.5x under TOAST.

| Component | at 51,000 iulaan |
|---|---:|
| `Attachment.text` (56,000 files) | 194 MB |
| Gazette bodies | 81 MB |
| `SearchDocument` heap (71,445 rows) | 219 MB |
| GIN x3 on tsvectors (title + summary only) | 40 MB |
| GIN trigram x3 (titles only) | 100 MB |
| `DocumentSpec` + indexes | 15 MB |
| iBay products, images, product info | 18 MB |
| **Total** | **~670 MB** |

Comfortable on the 4 GB box in section 12.5, with most of it available as page
cache. Scaled to the full 360,000-iulaan archive the same structure lands around
**3.2 GB** - still a disk question rather than a RAM one, which is the whole point
of the partitioning and partial-index decisions in 12.2.

For contrast, **storing the PDFs themselves would cost about 40 GB** at 43,500
files and 0.93 MB mean. That is the measured justification for the
fetch-extract-discard rule in 5.6: the text is 194 MB, the files are 40 GB, and
the files add nothing a re-fetch cannot recover.

### 12.7 Measure at 100k, not at 5M

The index-size estimates above are reasoned, not measured, and GIN sizing depends
heavily on vocabulary. So phase 1 ends with a synthetic load of 100,000 documents
and a recorded table of `pg_relation_size` per index plus p50/p95 query latency.
That table is the input to any further partitioning decision. Committing to
numbers before measuring them is how capacity plans become fiction.

## 13. SQLite to PostgreSQL migration

1. Add `psycopg[binary]`, switch `DATABASES` on a `DATABASE_URL` environment
   variable so SQLite stays available as a fallback.
2. `manage.py dumpdata` then load into Postgres. At 20k products and 35k images
   this is minutes. Verify row counts per table against the numbers in section 1.
3. Enable extensions by migration: `pg_trgm`, `unaccent`. `pgvector` is
   deferred.
4. Postgres-specific features live only in `search`, so the source apps stay
   backend-agnostic.

## 14. Testing

- Language pipeline: pure functions, table-driven. The transliteration golden
  file is mined from `Office.name`/`translated_name` pairs and the existing
  `TranslationCache` rows.
- Enrichment: recorded Ollama fixtures, no live model in CI. The grounding
  validator is tested adversarially - feed a response containing a price that is
  not in the source and assert the field is dropped.
- Retrieval: a fixture corpus of roughly 50 documents covering all four types and
  all three scripts.
- Card resolvers: `salary_display`, `capacity_display` and `estimate_net` get
  table-driven tests covering every branch, including the cases that must never
  render - a one-room-of-three listing presented as a whole unit, and a
  `partial` take-home estimate presented as a point value. These functions make
  claims the source data does not literally contain, so they are the highest-risk
  pure functions in the system. `estimate_net` gets the worked example from the
  corpus: basic 10,750 with a 4,400 attendance allowance at 7% pension and 20
  days.
- Keyboard-layout decoding (6.4): round-trip all 49 codepoints Thaana to keys and
  back, assert `migotawq` finds `މިގޮތައް`, and assert a phonetic Latin-Dhivehi query
  (`kuyyah dhinun`) is **not** misread as keyboard space. That last assertion is the
  one that matters - the two Latin encodings disagree on nearly every letter, and
  confusing them silently corrupts results.
- Minimal-pair ranking: ހަކަތަ and a skeleton-colliding word both indexed, then
  assert the correctly-filied query ranks the right document first. This is the
  regression guard for the dual-weight fili strategy in 6.2, and it is what turns
  "did skeleton indexing hurt precision" from an argument into a measurement.
- Attachment extraction: fixture `.docx` and both flavours of PDF (text layer and
  scanned), asserting the ladder picks the right method and that an application
  form is classified as an `apply_method` rather than indexed as job text.
- **Transcription CER harness** (5.6.1): text-layer PDFs extracted with
  `pdftotext` as ground truth, the same PDFs re-transcribed, character error rate
  computed per document. This both chooses the transcription model and gates it -
  a document over the CER bar records `ocr_failed` instead of indexing. It re-runs
  on any model change, so the choice never rests on vendor reputation.
- HTML table parsing: the real gazette salary-table markup, asserting label/value
  pairs come out and that no markup tokens reach the tsvector.
- Facet discovery: fixture result sets asserting that a constant-valued key is
  dropped, that a sparse key is dropped, that `Apple (iPhone)` and `Apple`
  collapse to one value, and that a 70%-single-category result set uses the
  curated ordering.
- Write-once enforcement (5.7): assert an `Attachment` with `status='ok'` is never
  re-sent for transcription, that a `prompt_version` bump re-enriches iBay records
  and leaves gazette records untouched, that `stale_marked_at` overrides both gates
  and is cleared on success, and that `POST /report` creates a row without queueing
  any paid work.
- Classification fallback (5.3): a document matching no type prior lands in `news`
  with a non-empty title and summary. An empty-summary news card is a failure, since
  the excerpt is the entire result.
- **Rebuildability (3.1, 3.2)**: the load-bearing test for the whole design. Take a
  fixture corpus, delete every `EnrichedRecord`, `SearchDocument` and
  `DocumentSpec`, rebuild from the source apps alone, and assert the result is
  identical. Then run the 3.2 procedure end to end - add a type, mark the news slice
  stale, re-enrich, reindex - and assert documents move `news` to the new type, keep
  their `(source, source_key)` identity and their `id`, and that no `Attachment` was
  re-sent for transcription. If this test passes, adding a category later is a
  configuration change; if it ever fails, it silently becomes a re-scrape.
- Adapter contract: every registered adapter implements `fetch_raw` and round-trips
  `iter_source_keys` to a non-null `RawDocument`. A source that cannot be read back
  cannot be reprocessed, so this is enforced rather than assumed.
- Time-dependent rendering: index a job whose deadline has passed, then assert the
  response reports it closed. This is the regression guard for the "nothing
  time-dependent in `card`" rule - the failure it catches is a stale vacancy
  advertising itself as open forever.
- **Relevance evaluation set**: about 40 hand-written (query, expected top
  result) pairs across Thaana, Latin-Dhivehi and English. A ranking weight change
  that regresses recall@5 is rejected. Without this, ranking changes get judged
  on how they feel, which is how search engines quietly get worse.
- API contract tests through ninja's test client.

## 15. Phasing

Each phase ends with something demonstrable.

- **P1 Foundation** - Compose files, Postgres migration, `core` app, `search`
  app with `SearchDocument` (partitioned, partial indexes), the indexer registry,
  streaming reindex, and iBay plus gazette adapters using scraped data only.
  Ends with the 100k-document load test and the recorded size/latency table from
  12.6. Outcome: working English search, with measured headroom.
- **P2 Dhivehi** - `search/lang/`, multi-vector ranking, query-side expansion
  and translation. Outcome: working trilingual search.
- **P3 Attachments** - `Attachment` model, the docx/pdftotext/transcription
  ladder, label classification, HTML table parsing for gazette bodies, the CER
  harness, 20-page chunking for long documents. Runs before enrichment because
  enrichment quality on gazette jobs depends on it: 78% of iulaan have attachments
  and only 16 of 306 state salary in the body. **Opens with the scanned-fraction
  sample from 5.6.2** - 200 PDFs through `pdftotext` - because that number decides
  a five-fold cost range. Transcription then runs jobs-first.
- **P4 Enrichment** - `enrich` app, Pydantic schemas, deterministic
  pre-extraction, the compensation model and `estimate_net`, DeepSeek client with
  the escalation chain, grounding validator, cold pass over jobs and news, then
  property. Outcome: typed attributes and take-home estimates live for the two
  types that need them most.
- **P5 API and static facets** - ninja endpoints, the four card payloads, the
  fixed facet sets for jobs, property and news, suggest, meta registry, the report
  endpoint, and `QueryLog`/`ClickLog` (16.3). Logging ships **with** the API, not
  after it: every day the API runs unlogged is history that cannot be recovered, and
  P7's facet curation depends on it.
- **P6 Frontend** - Next.js, tabs, the four card components, job apply block and
  compensation breakdown, property gallery, RTL, filter UI.
- **P7 Dynamic shopping facets** - `SpecKey` registry, `DocumentSpec` table, the
  unit-pattern extractor, facet discovery and scoring, the commerce grid, the
  admin promotion queue, and the shopping enrichment backfill.
- **P8 Hardening** - evaluation set including the minimal-pair cases, ranking
  tuning, zero-result relaxation, background title translation, archive
  partitioning.

P1 and P2 together already constitute a usable search engine. Shopping works from
P4 with its universal facets; P6 is what makes it feel like a commerce site.
Deliberately last, because it is the most speculative part of the design and it
benefits from having real query logs to curate `SpecKey` priorities against.

## 16. Out of scope for v1

A bare exclusion list is not useful, so each item below carries the reason and the
concrete condition that would bring it in.

**Staff and admin authentication is in scope** and always was - the earlier draft's
"user accounts" line was imprecise. `django.contrib.admin` is already installed and
the project already ships 123 lines of admin definitions, and five features in this
design depend on an authenticated staff surface: the report review queue (5.7), the
`SpecKey` promotion queue (4.4), `needs_review` enrichment records (5.2), manual
stale marking (5.7), and `Source` registry rows (4.3.3). Only **end-user** accounts
and saved searches are out, on the grounds that nothing in v1 needs to know who is
searching.

One hardening consequence: the admin is the control surface for operations that
spend money (marking 51,000 documents stale re-queues paid work), so
`compose.prod.yml` routing `/admin` to the API must be paired with an IP allowlist
or an additional auth layer at the proxy. A public admin login page guarding a
billable action is not an acceptable v1 posture.

### 16.1 Semantic and vector search (pgvector)

Deferred on two measurable blockers, not on value - and it is the most promising
future addition precisely because it attacks this project's hardest problem
directly: a good multilingual embedding matches a Dhivehi query to an English
document with no translation hop at all, which is the thing sections 5.5 and 6
work hard to approximate.

1. **Dhivehi embedding quality is unknown.** Multilingual models advertise 100+
   languages, but Dhivehi is low-resource and advertised coverage is not measured
   coverage. This is exactly the trap the transcription CER harness exists to avoid.
2. **HNSW wants its graph resident in RAM**, which collides head-on with the 4 GB
   target in 12.5. At 71,445 documents and 1024 dimensions the vectors alone are
   about 293 MB before graph overhead.

**Re-entry condition:** run the section 14 evaluation set against a candidate
embedding model and compare recall@5 on the cross-language pairs specifically
against the lexical baseline. If it wins there, it earns a column - and it is purely
additive, a vector column plus an index, with no restructuring of anything in
sections 3 through 9.

### 16.2 Click-through learning to rank

Deferred on **data**, not design: a ranking model cannot be trained on traffic that
does not exist yet. But that argument only excuses the *model*, not the *logging* -
so **query and click logging moves into v1** (16.3). Shipping the API without it
would mean discarding the exact history that makes v2 possible.

**Re-entry condition:** roughly 10,000 logged clicks. There is also a cheaper
intermediate step worth taking first: use the logs to tune the static weights in
section 7 against measured outcomes, which needs far less data than a learned model
and carries far less risk of a feedback loop that entrenches whatever ranked first
on day one.

### 16.3 Query and click logging - in v1

Two places in this spec already assume these logs exist: `SpecKey` priority curation
in P7 explicitly wants "real query logs to curate against", and the `QueryAlias`
table in 6.5 has no other sensible source. They were assumed and never specified.

```python
class QueryLog(models.Model):
    q_raw, q_normalized = CharField(), CharField()
    detected_lang, response_lang = CharField(), CharField()
    doc_type      = CharField(blank=True)    # active tab
    filters       = JSONField(default=dict)
    result_count  = IntegerField()
    latency_ms    = IntegerField()
    session_hash  = CharField(max_length=64)  # salted, salt rotates daily
    created_at    = DateTimeField(auto_now_add=True)

class ClickLog(models.Model):
    query    = ForeignKey(QueryLog, related_name='clicks')
    document = ForeignKey(SearchDocument)
    position = IntegerField()     # rank at click time
    created_at = DateTimeField(auto_now_add=True)
```

`position` is the field that is easy to omit and impossible to reconstruct later.
Without rank-at-click-time there is no MRR, no nDCG and no usable ranking feature -
just a list of documents someone once opened.

Four constraints:

- **Never on the hot path.** Logging is fire-and-forget and must not add latency to
  or fail a search response.
- **Append-only and partitioned by month**, with BRIN on `created_at`. This is the
  fastest-growing table in the system and must not share partition space or
  vacuum behaviour with `SearchDocument`.
- **No user identity, because there are no accounts.** `session_hash` is a salted
  hash with a daily-rotating salt, which supports same-session analysis without
  building a durable per-person search history.
- **Raw rows expire.** Keep them for a short window, aggregate beyond it. Query
  text is the most sensitive data this system will hold; a Dhivehi search log is a
  small-population dataset and easy to de-anonymise.

Immediate payoff, before any ranking work: zero-result queries become a measurable
list, which is the highest-signal input for curating aliases and finding gaps in the
transliteration table.

### 16.4 Meilisearch - not a core component, deliberately

The direct answer to "would we have to design around it": no, and that is a
property worth stating rather than a coincidence. Section 3.1 makes
`SearchDocument` a **derived, disposable projection**. Adopting Meilisearch would
be a second indexer target reading the same `EnrichedRecord` and `SearchDocument`
rows, plus a swap of the retrieval layer behind an unchanged `/api/v1/search`
contract. The language pipeline (6), the enrichment pipeline (5), the card payloads
(8) and the API surface (9) are all untouched. It is additive, not a rewrite.

What would actually change, honestly:

- **Gained:** built-in facet distribution, which is genuinely better than the
  lateral-join aggregation in 8.3, and built-in typo tolerance, which would partly
  replace the trigram layer.
- **Lost:** the single-vector dual-weight fili trick in 6.2 has no direct
  equivalent. It would be emulated with separate searchable attributes plus
  attribute ranking - workable, less precise, and no longer one atomic index.
- **Cost:** a second datastore to keep in sync, and Meilisearch also wants its
  indexes in RAM - the same 4 GB collision that defers pgvector.

**Re-entry condition:** the 100k-document load test in 12.7 shows p95 search latency
missing target, or dynamic facet discovery proving too slow at scale. Both are
measurements this plan already produces, so the decision arrives with evidence
rather than as a preference.

### 16.5 Also out

Crawling sources beyond iBay and gazette; image understanding via a vision model
(the same capability transcription uses, pointed at product photos); and any
end-user account features.

## 17. Resolved

1. Postgres is self-hosted in Compose. No managed instance.
2. Real estate is a first-class `property` document type, not a shopping
   subcategory.
3. Enrichment runs on DeepSeek (`deepseek-v4-flash`), freeing the 9070 XT to run
   translation alone at `OLLAMA_NUM_PARALLEL=4`.
4. Enrichment order: jobs and news, then property, then shopping.

Nothing is outstanding. `DEEPSEEK_API_KEY` needs to be in `.env` before phase 3.
