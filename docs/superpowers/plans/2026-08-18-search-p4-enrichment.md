# P4 Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn raw listing text into schema-validated typed attributes and rendered card payloads, with every extracted value traceable to the source text.

**Architecture:** A deterministic pre-extraction pass pulls phone numbers, money, dates and `<number><unit>` pairs out with regex and hands them to the model as a candidate list. The model selects and labels from those candidates and fills prose gaps; it never transcribes digits and it never does arithmetic. Its JSON response is parsed into a Pydantic model, then run through a grounding validator that drops any field it cannot trace back to the source. The survivor is stored in `EnrichedRecord`, keyed by a hash of the exact text fed to the model, so re-indexing never re-runs the model. `search.indexing` picks the record up through a settings-declared overlay hook, so `search` never imports `enrich`.

**Tech Stack:** Django 6.0.5, Python 3.12, PostgreSQL 18, Pydantic v2, httpx (async), DeepSeek API (`deepseek-v4-flash` / `deepseek-v4-pro`), Ollama fallback, pytest + pytest-django.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 3.3, 4.2, 4.3, 4.3.1, 4.3.2, 5.1 through 5.5, 5.7, 8.1 through 8.5.

**Depends on:** P1 (`search.adapters.base`, `search.indexing`, `search.models`), P3 (`gazette.models.Attachment`, extracted attachment text). P2 is not a hard dependency but `core.translate` is used for the Dhivehi half of titles and summaries.

---

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include this section.

- **The model never does arithmetic.** Take-home pay, range midpoints, per-day totals and currency conversions are computed by tested Python from extracted line items. Spec 4.3.2, 5.2 layer 0.
- **The model never transcribes digits.** Every number in the output must have been produced by the regex pre-extractor and offered to the model as a candidate. Spec 5.2 layer 0.
- **Scraped fields win.** iBay `price`, `product_location`, `ProductInfo`; gazette `iulaan_type`, `office`. The model may fill a null; it may never overwrite. A conflict keeps the scraped value and flags the record `needs_review`. Spec 5.2 layer 4.
- **A field that fails validation is dropped, never repaired.** The reason goes in `EnrichedRecord.validation`. Spec 5.2 layer 3.
- **`negotiable` may only be set when the source says so.** A missing salary is `unlisted`. Spec 4.3.
- **`news` is the default sink.** There is no `unknown` doc_type and no quarantine queue. Spec 5.3.
- **`prompt_version` bumps do not backfill `source='gazette'`.** Only `content_hash` change or `stale_marked_at` re-enriches a gazette document. Spec 4.2, 5.7.
- **Enrichment must NOT clear `stale_marked_at`.** `reindex` is the last stage in the chain and the only one that clears it (P1 `search/indexing.py:59`). If enrichment cleared it, the documented recovery sequence `enrich_documents --stale` then `reindex --stale` would silently index nothing. Spec 5.7.
- **Nothing time-dependent goes in `card`.** `card` stores raw dates; `deadline_state`, freshness and relative-time labels are computed per request. Spec 8.
- **Indexing never blocks on enrichment.** A `needs_review` or `failed` record still indexes, using scraped data and the rule-based `doc_type` fallback. Spec 5.2.
- **`temperature: 0` on every provider**, plus `top_k: 1`, `seed: 42`, `think: false` where they exist. Reasoning modes stay off. Spec 5.2 layer 1.
- Version control is **jj**, not git. Commit with `jj commit -m "..."`.
- Streaming uses `.iterator(chunk_size=500)` on the `direct` database alias.

---

## File Structure

```
enrich/
  __init__.py
  apps.py
  models.py                        EnrichedRecord
  schemas.py                       Pydantic: the five attribute models + parts
  compensation.py                  estimate_net, NetEstimate -- pure Python
  preextract.py                    Candidates, extract_candidates
  prior.py                         deterministic doc_type prior + full fallback
  prompts.py                       PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
  client.py                        EnrichClient: the four-stage provider chain
  validate.py                      ground(): the grounding validator
  cards.py                         build_attrs / build_card per doc_type
  overlay.py                       apply_enrichment(draft) -> DocumentDraft
  pipeline.py                      enrich_one, run_pass
  admin.py
  migrations/0001_initial.py
  management/commands/enrich_documents.py

search/indexing.py                 MODIFIED: draft overlay hook
beynunehcheh/settings.py           MODIFIED: enrich settings block
requirements.txt                   MODIFIED: pydantic

tests/enrich/
  test_schemas.py
  test_compensation.py
  test_preextract.py
  test_prior.py
  test_validate.py
  test_client.py
  test_cards.py
  test_overlay.py
  test_pipeline.py
  test_command.py
  fixtures/corpus_samples.py       real strings from the corpus, one place
```

Why the split: `preextract`, `compensation` and `validate` are pure functions over strings and numbers with no I/O and no Django, which is what makes them cheap to test exhaustively — and they are the three modules where a bug becomes a wrong number on a card. `client` is the only module that touches the network. `overlay` is the only module `search` knows about.

---

### Task 1: `enrich` app, `EnrichedRecord`, settings

**Files:**
- Create: `enrich/__init__.py`, `enrich/apps.py`, `enrich/models.py`, `enrich/admin.py`, `enrich/migrations/__init__.py`
- Modify: `beynunehcheh/settings.py`, `requirements.txt`
- Test: `tests/enrich/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `enrich.models.EnrichedRecord` with fields `source`, `source_key`, `content_hash`, `doc_type`, `doc_type_confidence`, `canonical_title_en`, `canonical_title_dv`, `summary_en`, `summary_dv`, `attrs`, `keywords`, `model_name`, `prompt_version`, `validation`, `status`, `attempts`, `error`, `created_at`, `updated_at`. Settings names `ENRICH_PROVIDER`, `ENRICH_MODEL`, `ENRICH_MODEL_ESCALATION`, `ENRICH_MODEL_LOCAL`, `ENRICH_CONCURRENCY`, `ENRICH_TIMEOUT`, `ENRICH_MAX_INPUT_CHARS`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `PENSION_RATE`, `PENSION_BASE`, `DEFAULT_WORKING_DAYS`.

- [ ] **Step 1: Add pydantic to requirements**

Append to `requirements.txt`:

```
pydantic==2.12.3
```

Install: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `tests/enrich/__init__.py` (empty) and `tests/enrich/test_models.py`:

```python
import pytest
from django.db import IntegrityError

from enrich.models import EnrichedRecord


@pytest.mark.django_db
def test_identity_is_source_plus_source_key():
    EnrichedRecord.objects.create(
        source="ibay", source_key="1", content_hash="a" * 64, doc_type="shopping"
    )
    with pytest.raises(IntegrityError):
        EnrichedRecord.objects.create(
            source="ibay", source_key="1", content_hash="b" * 64, doc_type="job"
        )


@pytest.mark.django_db
def test_defaults_are_pending_and_empty():
    r = EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="c" * 64, doc_type="news"
    )
    assert r.status == "pending"
    assert r.attrs == {}
    assert r.validation == {}
    assert r.keywords == []
    assert r.attempts == 0
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/enrich/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich'`

- [ ] **Step 4: Create the app package**

`enrich/__init__.py` — empty.

`enrich/apps.py`:

```python
from django.apps import AppConfig


class EnrichConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "enrich"
```

`enrich/migrations/__init__.py` — empty.

- [ ] **Step 5: Write the model**

`enrich/models.py`:

```python
"""Persisted LLM output. Spec 4.2, 3.3.

This table is the most expensive artifact in the system: every row cost an API
call. It is keyed by a hash of the exact text that was fed to the model, so a
re-scrape that changed nothing re-uses it, and re-indexing never re-runs the
model. Reindexing and re-enriching are independent operations.

Not partitioned. It is one row per SearchDocument at most, it is read by exact
key, and it does not churn.
"""

from django.db import models

STATUS_CHOICES = [
    ("pending", "pending"),
    ("ok", "ok"),
    ("needs_review", "needs review"),
    ("failed", "failed"),
]


class EnrichedRecord(models.Model):
    # Same natural key as SearchDocument. Deliberately not a FK: SearchDocument
    # is partitioned, and enrichment must survive a full reindex that drops and
    # rebuilds those rows.
    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)

    content_hash = models.CharField(max_length=64)
    doc_type = models.CharField(max_length=32)
    doc_type_confidence = models.FloatField(default=0.0)

    canonical_title_en = models.CharField(max_length=512, blank=True)
    canonical_title_dv = models.CharField(max_length=512, blank=True)
    summary_en = models.CharField(max_length=240, blank=True)
    summary_dv = models.CharField(max_length=240, blank=True)

    attrs = models.JSONField(default=dict, blank=True)
    keywords = models.JSONField(default=list, blank=True)

    model_name = models.CharField(max_length=64, blank=True)
    prompt_version = models.IntegerField(default=0)
    validation = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_key"], name="uniq_enriched_source_key"
            )
        ]
        indexes = [
            models.Index(fields=["source", "status"], name="enriched_source_status"),
            models.Index(fields=["content_hash"], name="enriched_content_hash"),
        ]

    def __str__(self):
        return f"{self.source}:{self.source_key} [{self.status}]"
```

- [ ] **Step 6: Register the app and settings**

In `beynunehcheh/settings.py`, add `'enrich',` to `INSTALLED_APPS` after `'search',`.

Add this block near the other env-driven settings:

```python
# --- Enrichment (spec 5.1, 4.3.2) ---
ENRICH_PROVIDER = os.getenv("ENRICH_PROVIDER", "deepseek")
ENRICH_MODEL = os.getenv("ENRICH_MODEL", "deepseek-v4-flash")
ENRICH_MODEL_ESCALATION = os.getenv("ENRICH_MODEL_ESCALATION", "deepseek-v4-pro")
ENRICH_MODEL_LOCAL = os.getenv("ENRICH_MODEL_LOCAL", "qwen3.5:4b")
ENRICH_CONCURRENCY = int(os.getenv("ENRICH_CONCURRENCY", "8"))
ENRICH_TIMEOUT = float(os.getenv("ENRICH_TIMEOUT", "120"))
# translate.py already caps gazette bodies at 3,500 chars; match it.
ENRICH_MAX_INPUT_CHARS = int(os.getenv("ENRICH_MAX_INPUT_CHARS", "3500"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# Maldives Retirement Pension Scheme. Settings, not constants, because tax
# treatment changes and hardcoding it is how a search engine starts lying.
# Spec 4.3.2.
PENSION_RATE = float(os.getenv("PENSION_RATE", "0.07"))
PENSION_BASE = os.getenv("PENSION_BASE", "basic")   # basic | gross
DEFAULT_WORKING_DAYS = int(os.getenv("DEFAULT_WORKING_DAYS", "20"))
```

Add `DEEPSEEK_API_KEY` and the `PENSION_*` keys to the `x-django-env` block in `compose.yml` if not already present (`compose.prod.yml` already carries `DEEPSEEK_API_KEY`).

- [ ] **Step 7: Make and run the migration**

Run: `python manage.py makemigrations enrich && python manage.py migrate`

- [ ] **Step 8: Run the tests**

Run: `pytest tests/enrich/test_models.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 9: Admin**

`enrich/admin.py`:

```python
from django.contrib import admin

from enrich.models import EnrichedRecord


@admin.register(EnrichedRecord)
class EnrichedRecordAdmin(admin.ModelAdmin):
    list_display = (
        "source", "source_key", "doc_type", "status",
        "doc_type_confidence", "model_name", "prompt_version", "updated_at",
    )
    list_filter = ("source", "doc_type", "status", "model_name", "prompt_version")
    search_fields = ("source_key", "canonical_title_en", "canonical_title_dv")
    readonly_fields = ("attrs", "validation", "keywords", "created_at", "updated_at")
    # needs_review first: those are the records where a scraped field and the
    # model disagreed, which is the queue a human is actually here to clear.
    ordering = ("status", "-updated_at")
```

- [ ] **Step 10: Commit**

```bash
jj commit -m "P4 task 1: enrich app, EnrichedRecord, settings"
```

---

### Task 2: Pydantic attribute schemas

**Files:**
- Create: `enrich/schemas.py`, `tests/enrich/fixtures/__init__.py`, `tests/enrich/fixtures/corpus_samples.py`
- Test: `tests/enrich/test_schemas.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Allowance`, `Compensation`, `ApplyMethod`, `Contact`, `Occupancy`, `Spec`, `JobAttrs`, `PropertyAttrs`, `ShoppingAttrs`, `NewsAttrs`, `EnrichmentOutput`, `ATTRS_FOR_TYPE: dict[str, type[BaseModel]]`, `schema_text(doc_type) -> str`.

The five consumers of these models (spec 4.3): the JSON schema pasted into the prompt, database validation, the facet registry, the API response type, and the generated TypeScript types. One definition.

- [ ] **Step 1: Write the corpus fixtures**

These strings are real. Every later task tests against them rather than against invented input.

`tests/enrich/fixtures/__init__.py` — empty.

`tests/enrich/fixtures/corpus_samples.py`:

```python
"""Real strings from the corpus. Spec 4.3.1, 4.4, 5.2.

Do not paraphrase these. They are the specific shapes the extractors have to
survive, and several of them (the '/-' suffix, the seven-digit number embedded
in a spec title) are the reason a rule exists.
"""

# --- iBay titles that carry their whole spec sheet ---
POWER_SUPPLY_TITLE = "KICO METAL POWER SUPPLY 24V-5A-120W / 7884445"
ROOM_TITLE = "1 Room Apartment for rent Viber Only 9223232 7000/- Near IGMH"
BEDSPACE_TITLE = (
    "Sharing Bed Space (2 Space) Available Prefer South Indian Boy (Tamil) 2800"
)
SHARED_HOUSE_TITLE = "Vazeefaa ah dhaa firihen kudhin bahattaden (phase 2)"

# --- gazette job body, table-flattened by P3 ---
GAZETTE_JOB_BODY = """\
މަޤާމް: އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ
މަޤާމުގެ ގްރޭޑް: GS3
އަސާސީ މުސާރަ: މަހަކު 10,750 ރުފިޔާ
އެލަވަންސް/އިނާޔަތްތައް: ހާޒިރީ އެލަވަންސްގެ ގޮތުގައި މަހަކު 4,400 ރުފިޔާ
ސަރވިސް އެލަވަންސް: މަހަކު 2,000 ރުފިޔާ
ވަޒީފާއަށް އެންމެ ޤާބިލު ފަރާތެއް ހޮވުމަށް ބެލޭނެ ކަންތައްތައް
ސުންގަޑި: 2026 އޯގަސްޓް 31
އީމެއިލް: hr@example.gov.mv ފޯނު: 3323838
"""

# --- an ad that states negotiability, and one that simply omits salary ---
NEGOTIABLE_BODY = "Salary negotiable depending on experience. Call 7994400."
NO_SALARY_BODY = "Looking for a cashier. Call 9483252 for details."

# --- iBay ProductInfo values that arrive as strings, not numbers ---
INFO_BEDROOMS = {"Bedrooms": "3 Rooms", "Bathrooms": "2", "Ideal Tenants": "Family"}
INFO_BEDROOMS_PLUS = {"Bedrooms": "4 Rooms and More"}
INFO_FACILITIES = {"Room Facilities": "Air Conditioning, Fans, Towels"}
INFO_BRAND_ALIAS = {"Brand": "Apple (iPhone)"}

# --- money written four ways, all of which appear ---
MONEY_STRINGS = ["10,750", "-/32,632", "7000/-", "MVR 5,000", "USD 450", "$450"]
```

- [ ] **Step 2: Write the failing test**

`tests/enrich/test_schemas.py`:

```python
import json

import pytest
from pydantic import ValidationError

from enrich.schemas import (
    ATTRS_FOR_TYPE,
    Allowance,
    Compensation,
    JobAttrs,
    NewsAttrs,
    Occupancy,
    PropertyAttrs,
    ShoppingAttrs,
    schema_text,
)


def test_every_doc_type_has_a_schema():
    assert set(ATTRS_FOR_TYPE) == {"job", "property", "shopping", "news"}


def test_all_fields_are_optional():
    """Spec 5.2 layer 5: a null field is correct behavior, a plausible
    invention is a bug. Every model must construct from nothing."""
    for model in ATTRS_FOR_TYPE.values():
        model()   # must not raise


def test_compensation_defaults_to_unlisted():
    c = Compensation()
    assert c.salary_state == "unlisted"
    assert c.completeness == "none"
    assert c.allowances == []
    assert c.pension_applies is False


def test_allowance_rejects_unknown_basis():
    with pytest.raises(ValidationError):
        Allowance(kind="attendance", label_raw="x", amount=1.0, basis="per_fortnight")


def test_occupancy_rejects_unknown_unit_kind():
    with pytest.raises(ValidationError):
        Occupancy(unit_kind="houseboat")


def test_schema_text_is_json_and_stable():
    """The prompt pastes this verbatim, so it must be deterministic -- a
    dict ordering change would silently invalidate DeepSeek's context cache
    on every call and triple the input cost."""
    a = schema_text("job")
    b = schema_text("job")
    assert a == b
    json.loads(a)


def test_job_attrs_accepts_a_realistic_payload():
    j = JobAttrs(
        role="Administrative Officer",
        employer="Ministry of Example",
        grade="GS3",
        compensation=Compensation(
            basic_salary=10750,
            allowances=[
                Allowance(kind="attendance", label_raw="ހާޒިރީ އެލަވަންސް",
                          amount=4400, basis="fixed_monthly"),
            ],
            pension_applies=True,
            salary_state="listed",
            completeness="full",
        ),
        apply_methods=[{"kind": "email", "value": "hr@example.gov.mv"}],
    )
    assert j.compensation.basic_salary == 10750
    assert j.apply_methods[0].kind == "email"


def test_property_and_shopping_and_news_construct():
    PropertyAttrs(listing_kind="rent", occupancy=Occupancy(unit_kind="room",
                  rooms_offered=1, rooms_total=3, is_shared=True))
    ShoppingAttrs(condition="used", brand="Apple",
                  specs=[{"key_raw": "voltage", "value_num": 24, "unit": "V"}])
    NewsAttrs(office="Ministry of Example", is_tender=True)
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/enrich/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.schemas'`

- [ ] **Step 4: Write the schemas**

`enrich/schemas.py`:

```python
"""Typed attribute schemas. Spec 4.3, 4.3.1, 4.3.2.

One Pydantic model per doc_type is the single source of truth for five
consumers: the JSON schema sent to the provider, database validation, the
facet registry, the API response type, and the generated TypeScript types.

Everything is optional. Spec 5.2 layer 5: the prompt instructs omission over
guessing, so a null field is correct behavior and a plausible invention is a
bug. There is no field here whose absence should fail a parse.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    # Extra keys are dropped rather than raising: a provider that invents a
    # field should lose the field, not the whole record.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --------------------------------------------------------------------------
# shared parts
# --------------------------------------------------------------------------

class Contact(_Base):
    kind: Literal["phone", "mobile", "landline", "viber", "whatsapp",
                  "email", "url"] = "phone"
    value: str = ""
    label_raw: str = ""


class ApplyMethod(_Base):
    kind: Literal["form", "email", "phone", "viber", "whatsapp",
                  "portal", "walk_in", "post"] = "email"
    value: str = ""
    label_en: str = ""
    label_dv: str = ""


class Spec(_Base):
    """One extracted attribute. Mirrors DocumentSpec (spec 4.4) but lives in
    the model output; P7 is what turns these rows into facets."""

    key_raw: str = ""
    value_num: float | None = None
    value_text: str = ""
    unit: str = ""


# --------------------------------------------------------------------------
# job
# --------------------------------------------------------------------------

class Allowance(_Base):
    kind: Literal["service", "living", "attendance", "ration", "phone",
                  "risk", "transport", "overtime", "other"] = "other"
    label_raw: str = ""
    amount: float | None = None
    basis: Literal["fixed_monthly", "per_day", "per_hour",
                   "percent_of_basic"] = "fixed_monthly"


class Compensation(_Base):
    basic_salary: float | None = None
    basic_salary_max: float | None = None      # grade bands quote a range
    currency: str = "MVR"
    period: Literal["month", "day", "hour", "year"] = "month"
    allowances: list[Allowance] = Field(default_factory=list)
    # Only when the ad says so. A silent ad is not evidence of a pension.
    pension_applies: bool = False
    pension_rate: float = 0.07
    # Three-way, not a nullable number: the card must distinguish "Negotiable"
    # from "Unlisted" and those are different claims. Spec 4.3.
    salary_state: Literal["listed", "negotiable", "unlisted"] = "unlisted"
    completeness: Literal["full", "partial", "basic_only", "none"] = "none"


class JobAttrs(_Base):
    role: str = ""                 # the job title alone, no employer, no boilerplate
    employer: str = ""
    position_type: str = ""        # Permanent | Contract | Temporary | Part-time
    job_category: str = ""
    grade: str = ""                # civil service rank: GS3, MS1
    compensation: Compensation = Field(default_factory=Compensation)
    qualifications: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    deadline: str = ""             # ISO date; validated in validate.py
    apply_methods: list[ApplyMethod] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    vacancies: int | None = None


# --------------------------------------------------------------------------
# property
# --------------------------------------------------------------------------

class Occupancy(_Base):
    """Occupancy is not a bedroom count. Spec 4.3.1.

    A listing offering one room of three must never render as a three-bedroom
    unit, which is why rooms_offered and rooms_total are separate fields.
    """

    unit_kind: Literal["whole_unit", "room", "bed_space",
                       "guest_house", "land", "commercial"] = "whole_unit"
    rooms_offered: int | None = None
    rooms_total: int | None = None
    beds_offered: int | None = None
    max_occupants: int | None = None
    is_shared: bool = False
    shared_facilities: list[str] = Field(default_factory=list)
    tenant_preference: list[str] = Field(default_factory=list)


class PropertyAttrs(_Base):
    listing_kind: Literal["rent", "sale", "wanted"] = "rent"
    unit_kind: str = ""
    occupancy: Occupancy = Field(default_factory=Occupancy)
    bedrooms: int | None = None
    bedrooms_or_more: bool = False        # '4 Rooms and More'
    bathrooms: int | None = None
    square_feet: float | None = None
    floor: str = ""
    furnishing: str = ""
    neighborhood: str = ""
    has_lift: bool | None = None
    room_facilities: list[str] = Field(default_factory=list)
    tenant_preference: list[str] = Field(default_factory=list)
    price_period: Literal["month", "day", "year"] = "month"
    currency_inferred: bool = False
    contacts: list[Contact] = Field(default_factory=list)


# --------------------------------------------------------------------------
# shopping
# --------------------------------------------------------------------------

class ShoppingAttrs(_Base):
    condition: str = ""
    brand: str = ""
    model: str = ""
    category_path: list[str] = Field(default_factory=list)
    quantity: int | None = None
    delivery: str = ""
    seller_type: str = ""
    negotiable: bool | None = None
    contacts: list[Contact] = Field(default_factory=list)
    specs: list[Spec] = Field(default_factory=list)


# --------------------------------------------------------------------------
# news -- the default sink (spec 5.3)
# --------------------------------------------------------------------------

class NewsAttrs(_Base):
    office: str = ""
    announcement_type: str = ""
    reference_no: str = ""
    deadline: str = ""
    tender_fee: float | None = None
    documents: list[str] = Field(default_factory=list)
    is_tender: bool = False


ATTRS_FOR_TYPE: dict[str, type[_Base]] = {
    "job": JobAttrs,
    "property": PropertyAttrs,
    "shopping": ShoppingAttrs,
    "news": NewsAttrs,
}


class EnrichmentOutput(_Base):
    """The whole model response. `attrs` stays a raw dict here and is parsed
    into the per-type model afterwards, so a bad `attrs` does not cost us the
    title and summary the news card depends on."""

    doc_type: Literal["job", "property", "shopping", "news"] = "news"
    doc_type_confidence: float = 0.0
    canonical_title_en: str = ""
    canonical_title_dv: str = ""
    summary_en: str = ""
    summary_dv: str = ""
    keywords: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)


_SCHEMA_CACHE: dict[str, str] = {}


def schema_text(doc_type: str) -> str:
    """The JSON schema, pasted into the prompt verbatim.

    Cached and sorted: the prefix must be byte-identical on every call or
    DeepSeek's context cache misses and the input cost triples (spec 5.1).
    """
    if doc_type not in _SCHEMA_CACHE:
        model = ATTRS_FOR_TYPE[doc_type]
        _SCHEMA_CACHE[doc_type] = json.dumps(
            model.model_json_schema(), sort_keys=True, ensure_ascii=False
        )
    return _SCHEMA_CACHE[doc_type]
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/enrich/test_schemas.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P4 task 2: Pydantic attribute schemas"
```

---

### Task 3: `estimate_net` — the arithmetic the model is not allowed to do

**Files:**
- Create: `enrich/compensation.py`
- Test: `tests/enrich/test_compensation.py`

**Interfaces:**
- Consumes: `enrich.schemas.Compensation`, `Allowance`.
- Produces: `NetEstimate(value, is_floor, working_days, completeness, breakdown)`, `estimate_net(comp, working_days=None) -> NetEstimate | None`, `salary_display(comp) -> str`.

This is the module a wrong line of code turns into a lie on a card. Test it before it exists and test the boundaries, not just the happy path.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_compensation.py`:

```python
import pytest

from enrich.compensation import estimate_net, salary_display
from enrich.schemas import Allowance, Compensation


def _fixed(kind, amount):
    return Allowance(kind=kind, label_raw=kind, amount=amount, basis="fixed_monthly")


def test_the_worked_example_from_the_spec():
    """basic 10,750, attendance 4,400 fixed, 7% pension on basic alone.
    10750 - 752.50 + 4400 = 14,397.50"""
    comp = Compensation(
        basic_salary=10750,
        allowances=[_fixed("attendance", 4400)],
        pension_applies=True,
        salary_state="listed",
        completeness="full",
    )
    est = estimate_net(comp)
    assert est.value == pytest.approx(14397.50)
    assert est.is_floor is False
    assert est.working_days == 20


def test_pension_is_deducted_from_basic_not_from_gross():
    """Allowances are added AFTER the deduction. Pensionable wage is basic
    salary alone. Getting this backwards overstates take-home by 7% of every
    allowance, which is exactly the misleading number this system forbids."""
    comp = Compensation(basic_salary=10000, allowances=[_fixed("living", 5000)],
                        pension_applies=True)
    est = estimate_net(comp)
    assert est.value == pytest.approx(14300.0)     # not 13950.0


def test_no_pension_when_the_ad_does_not_say_so():
    comp = Compensation(basic_salary=10000, pension_applies=False)
    assert estimate_net(comp).value == pytest.approx(10000.0)


def test_per_day_allowance_multiplies_by_working_days():
    comp = Compensation(
        basic_salary=8000,
        allowances=[Allowance(kind="attendance", label_raw="daily", amount=100,
                              basis="per_day")],
        pension_applies=True,
    )
    assert estimate_net(comp).value == pytest.approx(8000 - 560 + 2000)
    assert estimate_net(comp, working_days=26).value == pytest.approx(8000 - 560 + 2600)


def test_per_hour_allowance_uses_eight_hour_days():
    comp = Compensation(
        basic_salary=0,
        allowances=[Allowance(kind="overtime", label_raw="hourly", amount=50,
                              basis="per_hour")],
    )
    assert estimate_net(comp, working_days=20).value == pytest.approx(50 * 8 * 20)


def test_percent_of_basic_allowance():
    comp = Compensation(
        basic_salary=10000,
        allowances=[Allowance(kind="service", label_raw="35%", amount=35,
                              basis="percent_of_basic")],
    )
    assert estimate_net(comp).value == pytest.approx(13500.0)


def test_partial_completeness_renders_as_a_floor():
    comp = Compensation(basic_salary=10000, allowances=[_fixed("living", 1000)],
                        completeness="partial")
    est = estimate_net(comp)
    assert est.is_floor is True


def test_basic_only_returns_none_rather_than_restating_basic():
    """Spec 8.1: when the estimate would just restate basic salary it is
    omitted entirely rather than padding the card with a fake calculation."""
    comp = Compensation(basic_salary=10000, pension_applies=False,
                        completeness="basic_only")
    assert estimate_net(comp) is None


def test_basic_only_with_pension_is_still_worth_showing():
    comp = Compensation(basic_salary=10000, pension_applies=True,
                        completeness="basic_only")
    assert estimate_net(comp).value == pytest.approx(9300.0)


def test_no_basic_salary_returns_none():
    assert estimate_net(Compensation(salary_state="unlisted")) is None
    assert estimate_net(Compensation(salary_state="negotiable")) is None


def test_daily_and_hourly_period_are_not_monthly_estimates():
    """A wage quoted per day cannot be turned into a monthly take-home
    without inventing a schedule. Return None rather than guess."""
    assert estimate_net(Compensation(basic_salary=500, period="day")) is None
    assert estimate_net(Compensation(basic_salary=60, period="hour")) is None


def test_breakdown_shows_the_arithmetic():
    comp = Compensation(basic_salary=10750, allowances=[_fixed("attendance", 4400)],
                        pension_applies=True, completeness="full")
    est = estimate_net(comp)
    assert est.breakdown == [
        {"label": "basic", "amount": pytest.approx(10750.0)},
        {"label": "pension", "amount": pytest.approx(-752.50)},
        {"label": "attendance", "amount": pytest.approx(4400.0)},
    ]


@pytest.mark.parametrize(
    "comp,expected",
    [
        (Compensation(basic_salary=10750, salary_state="listed"), "MVR 10,750 / month"),
        (Compensation(basic_salary=450, currency="USD", salary_state="listed"),
         "USD 450 / month"),
        (Compensation(basic_salary=500, period="day", salary_state="listed"),
         "MVR 500 / day"),
        (Compensation(basic_salary=8000, basic_salary_max=12000, salary_state="listed"),
         "MVR 8,000 - 12,000 / month"),
        (Compensation(salary_state="negotiable"), "Negotiable"),
        (Compensation(salary_state="unlisted"), "Unlisted"),
        # listed but no number: fall back to Unlisted, never to an empty string
        (Compensation(salary_state="listed"), "Unlisted"),
    ],
)
def test_salary_display_is_always_one_of_three_shapes(comp, expected):
    assert salary_display(comp) == expected
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_compensation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.compensation'`

- [ ] **Step 3: Write the implementation**

`enrich/compensation.py`:

```python
"""Take-home estimation. Spec 4.3.2.

Every derived figure in the system comes from here. The language model
extracts line items and nothing else; arithmetic in a prompt is unreliable at
temperature 0 and a wrong take-home figure is precisely the misleading failure
this design forbids.

The estimate is always labelled as an estimate. The card leads with the number
the employer actually stated and shows this as clearly secondary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from enrich.schemas import Compensation

HOURS_PER_DAY = 8


@dataclass(slots=True)
class NetEstimate:
    value: float
    is_floor: bool
    working_days: int
    completeness: str
    breakdown: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "value": round(self.value, 2),
            "is_floor": self.is_floor,
            "working_days": self.working_days,
            "completeness": self.completeness,
            "breakdown": [
                {"label": b["label"], "amount": round(b["amount"], 2)}
                for b in self.breakdown
            ],
        }


def estimate_net(comp: Compensation, working_days: int | None = None) -> NetEstimate | None:
    """Estimated monthly take-home, or None when no honest figure exists.

    None is returned in four cases, all deliberate:
      - no basic salary (unlisted or negotiable): nothing to compute from
      - a non-monthly period: turning a daily wage into a monthly figure
        requires inventing a schedule
      - basic_only with no pension: the result would just restate the number
        already displayed above it (spec 8.1)
      - completeness 'none'
    """
    if working_days is None:
        working_days = settings.DEFAULT_WORKING_DAYS

    if comp.salary_state != "listed" or not comp.basic_salary:
        return None
    if comp.period != "month":
        return None
    if comp.completeness == "none":
        return None

    basic = float(comp.basic_salary)
    breakdown = [{"label": "basic", "amount": basic}]

    pension = 0.0
    if comp.pension_applies:
        rate = comp.pension_rate or settings.PENSION_RATE
        base = basic if settings.PENSION_BASE == "basic" else basic
        pension = base * rate
        breakdown.append({"label": "pension", "amount": -pension})

    added = 0.0
    for a in comp.allowances:
        if a.amount is None:
            continue
        if a.basis == "fixed_monthly":
            amount = float(a.amount)
        elif a.basis == "per_day":
            amount = float(a.amount) * working_days
        elif a.basis == "per_hour":
            amount = float(a.amount) * HOURS_PER_DAY * working_days
        elif a.basis == "percent_of_basic":
            amount = basic * float(a.amount) / 100.0
        else:                                    # unreachable: Literal-typed
            continue
        added += amount
        breakdown.append({"label": a.kind, "amount": amount})

    if added == 0.0 and pension == 0.0:
        # Nothing to say that the stated salary does not already say.
        return None

    return NetEstimate(
        value=basic - pension + added,
        is_floor=comp.completeness == "partial",
        working_days=working_days,
        completeness=comp.completeness,
        breakdown=breakdown,
    )


def salary_display(comp: Compensation) -> str:
    """One of three strings, never a null the frontend has to interpret.

    'Negotiable' appears only when the source said so; absence is 'Unlisted'.
    Spec 8.1.
    """
    if comp.salary_state == "negotiable":
        return "Negotiable"
    if comp.salary_state != "listed" or not comp.basic_salary:
        return "Unlisted"

    cur = comp.currency or "MVR"
    lo = f"{comp.basic_salary:,.0f}"
    if comp.basic_salary_max and comp.basic_salary_max > comp.basic_salary:
        hi = f"{comp.basic_salary_max:,.0f}"
        return f"{cur} {lo} - {hi} / {comp.period}"
    return f"{cur} {lo} / {comp.period}"
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/enrich/test_compensation.py -v`
Expected: PASS, 19 tests (13 named plus 7 parametrized cases minus the shared name).

- [ ] **Step 5: Commit**

```bash
jj commit -m "P4 task 3: estimate_net and salary_display"
```

---

### Task 4: Deterministic pre-extraction

**Files:**
- Create: `enrich/preextract.py`
- Test: `tests/enrich/test_preextract.py`

**Interfaces:**
- Consumes: `tests/enrich/fixtures/corpus_samples.py`.
- Produces: `Candidates` dataclass with `phones`, `emails`, `urls`, `money`, `units`, `dates`, `numbers`; `extract_candidates(text) -> Candidates`; `candidates_block(c) -> str`; `UNIT_VOCAB`; `parse_money(s) -> tuple[float, str] | None`; `parse_count(s) -> tuple[int, bool] | None`; `split_multivalue(s) -> list[str]`.

This is layer 0, and it is the layer that makes a fabricated phone number or an invented voltage structurally impossible rather than merely unlikely. A digit the regex did not find cannot appear in the output, because task 7 drops anything that is not in `Candidates`.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_preextract.py`:

```python
import pytest

from enrich.preextract import (
    extract_candidates,
    parse_count,
    parse_money,
    split_multivalue,
)
from tests.enrich.fixtures.corpus_samples import (
    BEDSPACE_TITLE,
    GAZETTE_JOB_BODY,
    POWER_SUPPLY_TITLE,
    ROOM_TITLE,
)


# --- phones -------------------------------------------------------------

def test_phone_hidden_at_the_end_of_a_spec_title():
    """'KICO METAL POWER SUPPLY 24V-5A-120W / 7884445' -- the trailing seven
    digits are a mobile number, and 24/5/120 must not be mistaken for one."""
    c = extract_candidates(POWER_SUPPLY_TITLE)
    assert c.phones == ["7884445"]


def test_phone_in_a_property_title():
    c = extract_candidates(ROOM_TITLE)
    assert c.phones == ["9223232"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Call 7994400", ["7994400"]),          # mobile, 7
        ("Viber 9483252", ["9483252"]),          # mobile, 9
        ("Tel 3323838", ["3323838"]),            # landline, 3
        ("Office 6650123", ["6650123"]),         # landline, 6
        ("+960 7994400", ["7994400"]),
        ("+9607994400", ["7994400"]),
        ("960-7994400", ["7994400"]),
        ("call 79944001", []),                   # eight digits, not a number
        ("ref 1234567", []),                     # starts with 1
        ("2026 08 17", []),                      # a date, not a phone
    ],
)
def test_phone_boundaries(text, expected):
    assert extract_candidates(text).phones == expected


def test_phone_is_deduplicated_and_ordered_by_first_appearance():
    c = extract_candidates("Call 7994400 or 9483252, again 7994400")
    assert c.phones == ["7994400", "9483252"]


# --- money --------------------------------------------------------------

@pytest.mark.parametrize(
    "text,amount,currency",
    [
        ("މަހަކު 10,750 ރުފިޔާ", 10750.0, "MVR"),
        ("-/32,632", 32632.0, "MVR"),
        ("7000/-", 7000.0, "MVR"),
        ("MVR 5,000", 5000.0, "MVR"),
        ("Rf 5,000", 5000.0, "MVR"),
        ("USD 450", 450.0, "USD"),
        ("$450", 450.0, "USD"),
        ("450 dollars", 450.0, "USD"),
    ],
)
def test_parse_money_handles_every_local_shape(text, amount, currency):
    assert parse_money(text) == (amount, currency)


def test_money_candidates_from_the_gazette_body():
    c = extract_candidates(GAZETTE_JOB_BODY)
    amounts = [m["amount"] for m in c.money]
    assert 10750.0 in amounts
    assert 4400.0 in amounts
    assert 2000.0 in amounts


def test_bare_price_in_a_title_is_a_money_candidate():
    c = extract_candidates(BEDSPACE_TITLE)
    assert 2800.0 in [m["amount"] for m in c.money]


def test_a_seven_digit_phone_is_not_offered_as_money():
    """Otherwise every listing with a contact number gets a 7,884,445 rufiyaa
    price tag."""
    c = extract_candidates(POWER_SUPPLY_TITLE)
    assert 7884445.0 not in [m["amount"] for m in c.money]


# --- units --------------------------------------------------------------

def test_units_parsed_out_of_a_compact_title():
    c = extract_candidates(POWER_SUPPLY_TITLE)
    got = {(u["value"], u["unit"]) for u in c.units}
    assert got == {(24.0, "V"), (5.0, "A"), (120.0, "W")}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("128GB storage", (128.0, "GB")),
        ("6.7 inch display", (6.7, "inch")),
        ("5000mAh battery", (5000.0, "mAh")),
        ("1.5 kW", (1.5, "kW")),
        ("750 sqft", (750.0, "sqft")),
    ],
)
def test_more_unit_shapes(text, expected):
    c = extract_candidates(text)
    assert (c.units[0]["value"], c.units[0]["unit"]) == expected


def test_a_year_is_not_a_unit_and_not_money():
    c = extract_candidates("Model year 2019 A/C unit")
    assert c.units == []


# --- emails, urls, dates ------------------------------------------------

def test_email_and_url():
    c = extract_candidates("Apply via https://forms.gle/abc123 or hr@example.gov.mv")
    assert c.emails == ["hr@example.gov.mv"]
    assert c.urls == ["https://forms.gle/abc123"]


@pytest.mark.parametrize(
    "text,iso",
    [
        ("Apply before 2026-08-31", "2026-08-31"),
        ("31 August 2026", "2026-08-31"),
        ("31/08/2026", "2026-08-31"),
        ("2026 އޯގަސްޓް 31", "2026-08-31"),
    ],
)
def test_dates_normalize_to_iso(text, iso):
    assert iso in extract_candidates(text).dates


# --- normalization helpers used by the adapters -------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3 Rooms", (3, False)),
        ("1 Room", (1, False)),
        ("4 Rooms and More", (4, True)),
        ("2", (2, False)),
        ("", None),
        ("Studio", None),
    ],
)
def test_parse_count(raw, expected):
    assert parse_count(raw) == expected


def test_split_multivalue():
    assert split_multivalue("Air Conditioning, Fans, Towels") == [
        "Air Conditioning", "Fans", "Towels"
    ]
    assert split_multivalue("Couples or Expatriates") == ["Couples", "Expatriates"]
    assert split_multivalue("") == []


# --- the block handed to the model --------------------------------------

def test_candidates_block_is_deterministic():
    from enrich.preextract import candidates_block
    c = extract_candidates(GAZETTE_JOB_BODY)
    assert candidates_block(c) == candidates_block(c)
    assert "10750" in candidates_block(c) or "10,750" in candidates_block(c)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_preextract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.preextract'`

- [ ] **Step 3: Write the implementation**

`enrich/preextract.py`:

```python
"""Layer 0: deterministic pre-extraction. Spec 5.2.

Phone numbers, emails, URLs, money amounts, <number><unit> pairs and dates are
pulled out with regex before the model is called and passed in as a candidate
list. The model selects and labels from these candidates; it never transcribes
them.

The consequence is structural, not statistical: task 7 drops any number in the
model's output that is not in this candidate set, so a wrong phone number, an
invented voltage or a fabricated salary cannot reach a card. It also cuts
output tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Maldivian numbers are seven digits: mobile starts 7 or 9, landline 3 or 6.
# The +960 prefix is optional and the number is frequently embedded in a title
# with no separator, hence the explicit boundary guards rather than \b (which
# would happily match the '445' tail of a longer run of digits).
_PHONE = re.compile(r"(?<![\d])(?:\+?960[\s\-]?)?([79]\d{6}|[36]\d{6})(?![\d])")

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")

# Money is written at least four ways in this corpus and all of them appear:
#   10,750 ރުފިޔާ | -/32,632 | 7000/- | MVR 5,000 | USD 450 | $450
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MONEY_PATTERNS = [
    (re.compile(rf"(?:USD|\$)\s*({_NUM})", re.I), "USD"),
    (re.compile(rf"({_NUM})\s*(?:USD|dollars?)", re.I), "USD"),
    (re.compile(rf"(?:MVR|Rf\.?|ރ\.?|ރުފިޔާ)\s*({_NUM})", re.I), "MVR"),
    (re.compile(rf"({_NUM})\s*(?:MVR|rufiyaa|ރުފިޔާ)", re.I), "MVR"),
    (re.compile(rf"-/\s*({_NUM})"), "MVR"),
    (re.compile(rf"({_NUM})\s*/-"), "MVR"),
]
# Anything else that looks like an amount. Kept separate because it is the
# weakest signal and the model is told so.
_BARE_AMOUNT = re.compile(rf"(?<![\d,.])({_NUM})(?![\d,.])")

# P7 replaces this constant with the SpecKey unit vocabulary (spec 4.4). Until
# then it is a fixed list, ordered longest-first so 'mAh' wins over 'A'.
UNIT_VOCAB = [
    "kWh", "mAh", "GHz", "MHz", "sqft", "inch", "kW", "GB", "TB", "MB",
    "kg", "ml", "cm", "mm", "V", "A", "W", "L", '"',
]
_UNIT = re.compile(
    rf"(?<![A-Za-z\d])({_NUM})\s*({'|'.join(re.escape(u) for u in UNIT_VOCAB)})"
    r"(?![A-Za-z])"
)

_DV_MONTHS = {
    "ޖެނުއަރީ": 1, "ފެބްރުއަރީ": 2, "މާރިޗު": 3, "މާރޗް": 3, "އޭޕްރީލް": 4,
    "މެއި": 5, "މޭ": 5, "ޖޫން": 6, "ޖުލައި": 7, "އޯގަސްޓް": 8, "އޮގަސްޓް": 8,
    "ސެޕްޓެމްބަރ": 9, "އޮކްޓޯބަރ": 10, "ނޮވެމްބަރ": 11, "ޑިސެމްބަރ": 12,
}
_EN_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1
    )
}
_EN_MONTHS.update({m[:3]: i for m, i in list(_EN_MONTHS.items())})

_ISO_DATE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
_DMY_DATE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2})")
_EN_TEXT_DATE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})")
_DV_TEXT_DATE = re.compile(r"(20\d{2})\s+([ހ-޿]+)\s+(\d{1,2})")

_COUNT = re.compile(r"^\s*(\d+)\s*(?:rooms?|bedrooms?|baths?|bathrooms?)?"
                    r"\s*(and\s+more)?\s*$", re.I)
_MULTIVALUE_SPLIT = re.compile(r"\s*(?:,|/|\||\bor\b|\band\b)\s*", re.I)


@dataclass(slots=True)
class Candidates:
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    money: list[dict] = field(default_factory=list)     # {amount, currency, raw}
    units: list[dict] = field(default_factory=list)     # {value, unit, raw}
    dates: list[str] = field(default_factory=list)      # ISO
    numbers: list[float] = field(default_factory=list)  # every bare number seen

    def all_numeric_strings(self) -> set[str]:
        """Every digit run the validator will accept in model output."""
        out: set[str] = set()
        for p in self.phones:
            out.add(p)
        for m in self.money:
            out.add(_fmt(m["amount"]))
        for u in self.units:
            out.add(_fmt(u["value"]))
        for n in self.numbers:
            out.add(_fmt(n))
        return out


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_money(s: str) -> tuple[float, str] | None:
    """First money amount in `s`, with its currency.

    Currency is set from an explicit marker only. A bare number defaults to
    MVR and the caller records `currency_inferred` (spec 4.3.1) -- 1,019
    products in this corpus mention USD, so assuming is not safe.
    """
    for pattern, currency in _MONEY_PATTERNS:
        m = pattern.search(s)
        if m:
            return float(m.group(1).replace(",", "")), currency
    m = _BARE_AMOUNT.search(s)
    if m:
        return float(m.group(1).replace(",", "")), "MVR"
    return None


def parse_count(raw: str) -> tuple[int, bool] | None:
    """'3 Rooms' -> (3, False); '4 Rooms and More' -> (4, True). Spec 4.3.1."""
    if not raw:
        return None
    m = _COUNT.match(raw)
    if not m:
        return None
    return int(m.group(1)), bool(m.group(2))


def split_multivalue(raw: str) -> list[str]:
    """'Air Conditioning, Fans, Towels' -> three values. Spec 4.4."""
    if not raw:
        return []
    return [p.strip() for p in _MULTIVALUE_SPLIT.split(raw) if p.strip()]


def _extract_dates(text: str) -> list[str]:
    out: list[str] = []

    def push(y, mo, d):
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            out.append(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")

    for y, mo, d in _ISO_DATE.findall(text):
        push(y, mo, d)
    for d, mo, y in _DMY_DATE.findall(text):
        push(y, mo, d)
    for d, name, y in _EN_TEXT_DATE.findall(text):
        mo = _EN_MONTHS.get(name.lower()) or _EN_MONTHS.get(name.lower()[:3])
        if mo:
            push(y, mo, d)
    for y, name, d in _DV_TEXT_DATE.findall(text):
        mo = _DV_MONTHS.get(name)
        if mo:
            push(y, mo, d)
    return _dedup(out)


def extract_candidates(text: str) -> Candidates:
    if not text:
        return Candidates()

    phones = _dedup(_PHONE.findall(text))
    phone_set = set(phones)

    money: list[dict] = []
    consumed: set[str] = set()
    for pattern, currency in _MONEY_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(1)
            amount = float(raw.replace(",", ""))
            money.append({"amount": amount, "currency": currency, "raw": m.group(0)})
            consumed.add(raw)

    units: list[dict] = []
    unit_spans: list[tuple[int, int]] = []
    for m in _UNIT.finditer(text):
        units.append({
            "value": float(m.group(1).replace(",", "")),
            "unit": m.group(2),
            "raw": m.group(0),
        })
        unit_spans.append(m.span())

    dates = _extract_dates(text)
    date_digits = {p for d in dates for p in d.split("-")}

    numbers: list[float] = []
    for m in _BARE_AMOUNT.finditer(text):
        raw = m.group(1)
        if raw in consumed or raw in phone_set:
            continue
        if any(s <= m.start() < e for s, e in unit_spans):
            continue
        numbers.append(float(raw.replace(",", "")))
        # A four-digit run that reads as a year is not a price. Everything else
        # bare is offered as a weak money candidate.
        looks_like_year = raw.isdigit() and len(raw) == 4 and raw.startswith("20")
        if not looks_like_year and raw not in date_digits:
            money.append({"amount": float(raw.replace(",", "")),
                          "currency": "MVR", "raw": raw})

    # dedupe money on (amount, currency), first appearance wins
    seen, dedup_money = set(), []
    for m in money:
        k = (m["amount"], m["currency"])
        if k not in seen:
            seen.add(k)
            dedup_money.append(m)

    return Candidates(
        phones=phones,
        emails=_dedup(_EMAIL.findall(text)),
        urls=_dedup(_URL.findall(text)),
        money=dedup_money,
        units=units,
        dates=dates,
        numbers=_dedup(numbers),
    )


def candidates_block(c: Candidates) -> str:
    """The block appended to the user prompt. Sorted, so identical input
    produces an identical prompt and the provider cache keeps hitting."""
    return json.dumps(
        {
            "phones": c.phones,
            "emails": c.emails,
            "urls": c.urls,
            "money": [{"amount": m["amount"], "currency": m["currency"]}
                      for m in c.money],
            "units": [{"value": u["value"], "unit": u["unit"]} for u in c.units],
            "dates": c.dates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/enrich/test_preextract.py -v`
Expected: PASS.

If `test_a_year_is_not_a_unit_and_not_money` fails on `A/C`, the `_UNIT` pattern's trailing `(?![A-Za-z])` is doing its job but the leading guard is not — check that `(?<![A-Za-z\d])` precedes the number.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P4 task 4: deterministic pre-extraction"
```

---

### Task 5: Classification prior and rule-based fallback

**Files:**
- Create: `enrich/prior.py`
- Test: `tests/enrich/test_prior.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `prior_for(source, *, iulaan_type="", categories=()) -> str`, `apply_confidence_gate(prior, model_type, confidence) -> tuple[str, bool]`, `IULAAN_TYPE_MAP`, `IBAY_CATEGORY_MAP`, `CONFIDENCE_FLOOR`.

The same table is both the prior handed to the model and the complete fallback when no provider is reachable. One table, two uses — a second table would drift.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_prior.py`:

```python
import pytest

from enrich.prior import apply_confidence_gate, prior_for


@pytest.mark.parametrize(
    "iulaan_type,expected",
    [
        ("ވަޒީފާގެ ފުރުޞަތު", "job"),
        ("Job Opportunity", "job"),
        ("ކުއްޔަށް ދިނުން", "property"),
        ("ކުއްޔަށް ހިފުން", "property"),
        ("ޢާންމު މަޢުލޫމާތު", "news"),
        ("Public Information", "news"),
        ("ދެންނެވުން", "news"),
        ("ބީލަން", "news"),
        ("ނީލަން", "news"),
        ("މުބާރާތް", "news"),
        ("", "news"),
        ("something nobody has seen before", "news"),
    ],
)
def test_gazette_prior(iulaan_type, expected):
    assert prior_for("gazette", iulaan_type=iulaan_type) == expected


@pytest.mark.parametrize(
    "categories,expected",
    [
        (["Jobs"], "job"),
        (["Housing & Real Estate"], "property"),
        (["Announcements & Events"], "news"),
        (["For Sale"], "shopping"),
        (["Services"], "shopping"),
        (["Wanted"], "shopping"),
        (["Free Stuff"], "shopping"),
        (["Business Opportunities"], "shopping"),
        ([], "news"),
        (["Electronics", "Jobs"], "job"),      # any matching level wins
    ],
)
def test_ibay_prior(categories, expected):
    assert prior_for("ibay", categories=categories) == expected


def test_unknown_source_falls_back_to_news():
    """news is the default sink -- there is no 'unknown' type. Spec 5.3."""
    assert prior_for("newspaper-mv") == "news"


def test_model_may_override_only_at_high_confidence():
    assert apply_confidence_gate("shopping", "job", 0.91) == ("job", True)
    assert apply_confidence_gate("shopping", "job", 0.80) == ("job", True)
    assert apply_confidence_gate("shopping", "job", 0.79) == ("shopping", False)
    assert apply_confidence_gate("shopping", "job", 0.0) == ("shopping", False)


def test_agreement_is_never_an_override():
    assert apply_confidence_gate("job", "job", 0.1) == ("job", False)


def test_an_unknown_model_type_never_wins():
    assert apply_confidence_gate("shopping", "tender", 0.99) == ("shopping", False)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_prior.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`enrich/prior.py`:

```python
"""Deterministic classification prior. Spec 5.3.

doc_type comes from the same model call as extraction, but the call is given a
prior derived from data the source already labelled. The model may override it
only at confidence >= 0.8; otherwise the prior wins.

This same table is the complete fallback when no provider is reachable, which
is why it lives here and not inside the prompt builder.
"""

from __future__ import annotations

DOC_TYPES = ("job", "property", "shopping", "news")

# news is the default sink: anything that does not classify confidently lands
# here. There is deliberately no 'unknown' type and no quarantine queue.
DEFAULT_DOC_TYPE = "news"

CONFIDENCE_FLOOR = 0.8

IULAAN_TYPE_MAP = {
    "ވަޒީފާގެ ފުރުޞަތު": "job",
    "Job Opportunity": "job",
    "ކުއްޔަށް ދިނުން": "property",       # letting
    "ކުއްޔަށް ހިފުން": "property",       # seeking to rent
    "ޢާންމު މަޢުލޫމާތު": "news",
    "Public Information": "news",
    "ދެންނެވުން": "news",
    "ބީލަން": "news",                    # bids -- a future `tender` type (3.2)
    "ނީލަން": "news",                    # auctions
    "މަސައްކަތް": "news",                # works
    "ގަންނަން ބޭނުންވާ ތަކެތި": "news",   # items wanted
    "މުބާރާތް": "news",                  # competitions
}

IBAY_CATEGORY_MAP = {
    "Jobs": "job",
    "Housing & Real Estate": "property",
    "Announcements & Events": "news",
    "For Sale": "shopping",
    "Services": "shopping",
    "Wanted": "shopping",
    "Free Stuff": "shopping",
    "Business Opportunities": "shopping",
}


def prior_for(source: str, *, iulaan_type: str = "", categories=()) -> str:
    if source == "gazette":
        return IULAAN_TYPE_MAP.get((iulaan_type or "").strip(), DEFAULT_DOC_TYPE)
    if source == "ibay":
        for name in categories:
            hit = IBAY_CATEGORY_MAP.get((name or "").strip())
            if hit:
                return hit
        return DEFAULT_DOC_TYPE
    return DEFAULT_DOC_TYPE


def apply_confidence_gate(
    prior: str, model_type: str, confidence: float
) -> tuple[str, bool]:
    """Returns (chosen_type, was_overridden).

    The gate exists because the data is genuinely mixed: iBay listings like
    'Cleaning work daily worker' sit under shopping-ish categories and are
    really jobs. It is set at 0.8 so an override needs the model to be sure,
    not merely to have an opinion.
    """
    if model_type not in DOC_TYPES:
        return prior, False
    if model_type == prior:
        return prior, False
    if confidence >= CONFIDENCE_FLOOR:
        return model_type, True
    return prior, False
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/enrich/test_prior.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P4 task 5: classification prior and fallback"
```

---

### Task 6: Prompt builder and the provider chain

**Files:**
- Create: `enrich/prompts.py`, `enrich/client.py`
- Test: `tests/enrich/test_client.py`

**Interfaces:**
- Consumes: `enrich.schemas.schema_text`, `enrich.preextract.candidates_block`, `enrich.prior`.
- Produces: `PROMPT_VERSION: int`, `SYSTEM_PROMPT: str`, `build_messages(*, source, doc_type_prior, title, body, candidates, scraped) -> list[dict]`; `EnrichClient` with `async def complete(messages, *, model=None, repair_error=None) -> str` and `async def run_chain(messages) -> tuple[dict, str]`; `ProviderError`.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_client.py`:

```python
import json

import pytest

from enrich.client import EnrichClient, ProviderError
from enrich.preextract import extract_candidates
from enrich.prompts import PROMPT_VERSION, build_messages


def test_prompt_version_is_an_int():
    assert isinstance(PROMPT_VERSION, int) and PROMPT_VERSION >= 1


def test_system_prompt_is_identical_across_calls():
    """It is ~800 tokens and it must hit DeepSeek's context cache, so it
    cannot interpolate anything per-document. Spec 5.1."""
    a = build_messages(source="ibay", doc_type_prior="shopping", title="A",
                       body="b", candidates=extract_candidates("b"), scraped={})
    b = build_messages(source="gazette", doc_type_prior="job", title="C",
                       body="d", candidates=extract_candidates("d"), scraped={})
    assert a[0]["content"] == b[0]["content"]
    assert a[0]["role"] == "system"


def test_user_prompt_carries_prior_candidates_and_scraped_truth():
    c = extract_candidates("Call 7994400, 10,750 rufiyaa")
    msgs = build_messages(
        source="gazette", doc_type_prior="job", title="Officer",
        body="Call 7994400, 10,750 rufiyaa", candidates=c,
        scraped={"office": "Ministry of Example"},
    )
    user = msgs[1]["content"]
    assert "job" in user
    assert "7994400" in user
    assert "Ministry of Example" in user


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeHTTP:
    """Records every call so the test can assert on the escalation ladder."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)

    async def aclose(self):
        pass


def _deepseek_reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}]}


@pytest.mark.asyncio
async def test_happy_path_uses_flash_once(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([_deepseek_reply({"doc_type": "job"})])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "job"}
    assert model == settings.ENRICH_MODEL
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_unparseable_json_triggers_a_repair_retry_on_the_same_model(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([
        {"choices": [{"message": {"content": "not json at all"}}]},
        _deepseek_reply({"doc_type": "news"}),
    ])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "news"}
    assert model == settings.ENRICH_MODEL
    assert len(http.calls) == 2
    # the repair call must carry the error text back in
    assert "not valid JSON" in json.dumps(http.calls[1][1])


@pytest.mark.asyncio
async def test_empty_content_is_a_failed_attempt_not_an_empty_result(settings):
    """DeepSeek documents occasional empty-content responses. Treating one as
    a valid empty extraction would silently blank a record. Spec 5.2 layer 2."""
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([
        {"choices": [{"message": {"content": ""}}]},
        _deepseek_reply({"doc_type": "news"}),
    ])
    client = EnrichClient(http=http)
    payload, _ = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "news"}


@pytest.mark.asyncio
async def test_two_failures_escalate_to_pro(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    http = _FakeHTTP([
        {"choices": [{"message": {"content": "junk"}}]},
        {"choices": [{"message": {"content": "junk again"}}]},
        _deepseek_reply({"doc_type": "property"}),
    ])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "property"}
    assert model == settings.ENRICH_MODEL_ESCALATION


@pytest.mark.asyncio
async def test_everything_failing_raises_provider_error(settings):
    settings.ENRICH_PROVIDER = "deepseek"
    settings.DEEPSEEK_API_KEY = "k"
    settings.OLLAMA_URL = ""
    http = _FakeHTTP([{"choices": [{"message": {"content": "junk"}}]}] * 3)
    client = EnrichClient(http=http)
    with pytest.raises(ProviderError):
        await client.run_chain([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_ollama_provider_sends_deterministic_options(settings):
    settings.ENRICH_PROVIDER = "ollama"
    settings.OLLAMA_URL = "http://gpu:11434"
    http = _FakeHTTP([{"message": {"content": json.dumps({"doc_type": "news"})}}])
    client = EnrichClient(http=http)
    payload, model = await client.run_chain([{"role": "user", "content": "x"}])
    assert payload == {"doc_type": "news"}
    opts = http.calls[0][1]["json"]["options"]
    assert opts["temperature"] == 0
    assert opts["top_k"] == 1
    assert opts["seed"] == 42
    assert http.calls[0][1]["json"]["think"] is False
```

Add `pytest-asyncio==1.3.0` to `requirements.txt` and `asyncio_mode = auto` under `[pytest]` in `pytest.ini` if P1 did not already.

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.client'`

- [ ] **Step 3: Write the prompt builder**

`enrich/prompts.py`:

```python
"""Prompt construction. Spec 5.2, 5.3.

Two rules govern everything here:

1. The system prompt is byte-identical on every call. It is ~800 tokens of
   instructions plus schema, and DeepSeek's context cache makes it cost
   $0.007/M instead of $0.22/M -- but only if the prefix never varies. Nothing
   per-document may leak into it.
2. The instructions repeat, in the imperative, the two rules the grounding
   validator enforces anyway: select numbers from the candidate list, and do
   no arithmetic. Telling the model reduces the number of records that have to
   be repaired; the validator is what makes it true.
"""

from __future__ import annotations

import json

from enrich.preextract import Candidates, candidates_block
from enrich.schemas import ATTRS_FOR_TYPE, schema_text

# Bump when the instructions or the schemas change in a way that would produce
# different output. Spec 4.2: a bump re-enriches iBay, and deliberately does
# NOT backfill gazette (spec 5.7).
PROMPT_VERSION = 1

_ALL_SCHEMAS = json.dumps(
    {t: json.loads(schema_text(t)) for t in sorted(ATTRS_FOR_TYPE)},
    sort_keys=True,
    ensure_ascii=False,
)

SYSTEM_PROMPT = f"""\
You extract structured data from Maldivian classified listings and government \
gazette notices. You return JSON and nothing else.

Rules, in order of importance:

1. NEVER write a number that does not appear in the CANDIDATES block of the \
user message. Phone numbers, salaries, prices, voltages and dates have already \
been extracted from the source text for you. Your job is to choose which \
candidate belongs in which field and to label it. If the right number is not \
in CANDIDATES, leave the field null.
2. NEVER perform arithmetic. Do not total allowances, do not compute take-home \
pay, do not convert currencies, do not average a range. Report line items \
exactly as stated. Arithmetic is done elsewhere.
3. NEVER overwrite a value in the SCRAPED block. Those are ground truth. You \
may fill a field the SCRAPED block leaves empty; you may not contradict it.
4. Prefer null over a guess. Every field is optional. A null field is correct \
behavior. An invented field is a defect.
5. Copy strings from the source rather than paraphrasing them. Every string you \
emit must be traceable to the input text.
6. `salary_state` is `negotiable` ONLY when the source actually says the salary \
is negotiable or open to discussion. A listing that simply does not mention pay \
is `unlisted`.
7. Classify `doc_type` as one of job, property, shopping, news. A PRIOR is given \
in the user message. Override it only if you are confident; report your \
confidence honestly in `doc_type_confidence` between 0 and 1. If nothing else \
fits, use news.
8. Write `summary_en` and `summary_dv` as one useful sentence of at most 240 \
characters each. For a news document the summary is the entire product, so make \
it say what actually happened, not what kind of document it is. Leave the \
Dhivehi fields empty if the source has no Dhivehi.

Return an object with exactly these keys:
  doc_type, doc_type_confidence, canonical_title_en, canonical_title_dv,
  summary_en, summary_dv, keywords, attrs

`attrs` must match the JSON schema for the doc_type you chose:

{_ALL_SCHEMAS}
"""


def build_messages(
    *,
    source: str,
    doc_type_prior: str,
    title: str,
    body: str,
    candidates: Candidates,
    scraped: dict,
    repair_error: str | None = None,
) -> list[dict]:
    parts = [
        f"SOURCE: {source}",
        f"PRIOR: {doc_type_prior}",
        f"SCRAPED: {json.dumps(scraped, ensure_ascii=False, sort_keys=True)}",
        f"CANDIDATES: {candidates_block(candidates)}",
        f"TITLE: {title}",
        "BODY:",
        body,
    ]
    if repair_error:
        parts.append(
            "\nYour previous response could not be used. Fix exactly this and "
            f"return the corrected JSON object:\n{repair_error}"
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]
```

- [ ] **Step 4: Write the client**

`enrich/client.py`:

```python
"""The four-stage provider chain. Spec 5.1, 5.2 layers 1 and 2.

| Stage | Provider | Model                  | When                          |
|-------|----------|------------------------|-------------------------------|
| 1     | DeepSeek | deepseek-v4-flash      | default                       |
| 2     | DeepSeek | deepseek-v4-flash      | repair retry, error fed back  |
| 3     | DeepSeek | deepseek-v4-pro        | records that failed stage 2   |
| 4     | Ollama   | qwen3.5:4b             | offline, dev, or unavailable  |

This mirrors the escalation ladder core/translate.py already implements, so
the enrichment client follows an idiom the codebase has rather than inventing
a second one.

DeepSeek's JSON mode guarantees parseable JSON, not schema conformance, and
its docs acknowledge occasional empty-content responses. Both are handled here
as failed attempts; schema conformance is task 7's problem.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Every stage of the chain failed."""


def _extract_content(provider: str, payload: dict) -> str:
    if provider == "ollama":
        return (payload.get("message") or {}).get("content", "")
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content", "") or ""


class EnrichClient:
    def __init__(self, http=None):
        self._http = http
        self._owns_http = http is None

    async def _client(self):
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=settings.ENRICH_TIMEOUT)
        return self._http

    async def aclose(self):
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    async def _call_deepseek(self, messages: list[dict], model: str) -> str:
        http = await self._client()
        r = await http.post(
            f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        r.raise_for_status()
        return _extract_content("deepseek", r.json())

    async def _call_ollama(self, messages: list[dict], model: str) -> str:
        http = await self._client()
        r = await http.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "format": "json",
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "top_k": 1, "seed": 42},
            },
        )
        r.raise_for_status()
        return _extract_content("ollama", r.json())

    async def complete(self, messages: list[dict], *, provider: str, model: str) -> dict:
        """One attempt. Raises ProviderError on anything unusable."""
        try:
            if provider == "ollama":
                content = await self._call_ollama(messages, model)
            else:
                content = await self._call_deepseek(messages, model)
        except Exception as exc:                       # network, 4xx, 5xx
            raise ProviderError(f"{provider}/{model}: {exc}") from exc

        if not content.strip():
            raise ProviderError(f"{provider}/{model}: empty content")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"{provider}/{model}: response was not valid JSON: {exc}"
            ) from exc

    def _stages(self) -> list[tuple[str, str]]:
        head = settings.ENRICH_PROVIDER
        if head == "ollama":
            return [("ollama", settings.ENRICH_MODEL_LOCAL)] * 2
        stages = [
            ("deepseek", settings.ENRICH_MODEL),
            ("deepseek", settings.ENRICH_MODEL),           # repair retry
            ("deepseek", settings.ENRICH_MODEL_ESCALATION),
        ]
        if getattr(settings, "OLLAMA_URL", ""):
            stages.append(("ollama", settings.ENRICH_MODEL_LOCAL))
        return stages

    async def run_chain(
        self, messages: list[dict], *, rebuild=None
    ) -> tuple[dict, str]:
        """Walk the ladder. Returns (parsed_json, model_name).

        `rebuild(error_text) -> messages` lets the caller re-render the prompt
        with the validation error appended; without it the same messages are
        re-sent, which is still worth one attempt against a transient failure.
        """
        last: Exception | None = None
        current = messages
        for attempt, (provider, model) in enumerate(self._stages()):
            try:
                return await self.complete(current, provider=provider, model=model), model
            except ProviderError as exc:
                last = exc
                logger.warning("enrich attempt %d failed: %s", attempt + 1, exc)
                if rebuild is not None:
                    current = rebuild(str(exc))
                else:
                    current = messages
                # Backoff only between network-ish failures; a JSON parse
                # failure is instant to retry.
                if "429" in str(exc) or "timeout" in str(exc).lower():
                    await asyncio.sleep(2 ** attempt)
        raise ProviderError(f"all stages failed; last error: {last}")
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/enrich/test_client.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P4 task 6: prompt builder and provider chain"
```

---

### Task 7: The grounding validator

**Files:**
- Create: `enrich/validate.py`
- Test: `tests/enrich/test_validate.py`

**Interfaces:**
- Consumes: `enrich.schemas`, `enrich.preextract.Candidates`.
- Produces: `ground(raw_attrs, *, doc_type, source_text, candidates, scraped) -> tuple[BaseModel, dict]`, `normalize_for_match(s) -> str`, `token_overlap(a, b) -> float`, `STRING_OVERLAP_FLOOR = 0.85`.

Layer 3, and the layer that actually does the work. Everything upstream is cost reduction; this is the correctness guarantee.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_validate.py`:

```python
import pytest

from enrich.preextract import extract_candidates
from enrich.validate import ground, normalize_for_match, token_overlap
from tests.enrich.fixtures.corpus_samples import GAZETTE_JOB_BODY


def _ground(attrs, text, doc_type="job", scraped=None):
    return ground(
        attrs,
        doc_type=doc_type,
        source_text=text,
        candidates=extract_candidates(text),
        scraped=scraped or {},
    )


# --- strings ------------------------------------------------------------

def test_a_string_present_verbatim_survives():
    model, report = _ground({"role": "Administrative Officer"},
                            "Vacancy: Administrative Officer at the Ministry")
    assert model.role == "Administrative Officer"
    assert report["dropped"] == []


def test_a_string_that_is_not_in_the_source_is_dropped():
    model, report = _ground({"employer": "Bank of Maldives"},
                            "Vacancy: Administrative Officer")
    assert model.employer == ""
    assert any(d["field"] == "employer" for d in report["dropped"])
    assert report["dropped"][0]["reason"] == "not_grounded"


def test_a_lightly_reworded_string_survives_on_token_overlap():
    model, _ = _ground({"role": "Senior Administrative Officer"},
                       "Post: Senior  Administrative   Officer (contract)")
    assert model.role == "Senior Administrative Officer"


def test_normalization_ignores_case_punctuation_and_spacing():
    assert normalize_for_match("Senior  Officer, (GS3)") == "senior officer gs3"


def test_token_overlap_is_symmetric_enough_to_be_useful():
    assert token_overlap("administrative officer", "administrative officer") == 1.0
    assert token_overlap("administrative officer", "officer") == 1.0
    assert token_overlap("bank of maldives", "administrative officer") == 0.0


# --- numbers ------------------------------------------------------------

def test_a_salary_present_in_the_source_survives():
    model, _ = _ground(
        {"compensation": {"basic_salary": 10750, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.basic_salary == 10750


def test_a_salary_the_model_invented_is_dropped():
    """The number 99,999 appears nowhere in the body. Spec 5.2 layer 3."""
    model, report = _ground(
        {"compensation": {"basic_salary": 99999, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.basic_salary is None
    assert any("basic_salary" in d["field"] for d in report["dropped"])


def test_a_totalled_salary_is_dropped_because_the_total_is_not_in_the_source():
    """10,750 + 4,400 + 2,000 = 17,150. The model was told not to add. If it
    adds anyway, the sum is not a digit run in the source and dies here.
    This is the test that makes 'no arithmetic' enforceable rather than
    aspirational."""
    model, _ = _ground(
        {"compensation": {"basic_salary": 17150, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.basic_salary is None


def test_a_phone_not_in_the_candidate_set_is_dropped():
    model, report = _ground(
        {"contacts": [{"kind": "phone", "value": "7771234"}]},
        "Call 7994400 for details",
    )
    assert model.contacts == []
    assert any(d["reason"] == "not_grounded" for d in report["dropped"])


def test_a_phone_in_the_candidate_set_survives():
    model, _ = _ground(
        {"contacts": [{"kind": "phone", "value": "7994400"}]},
        "Call 7994400 for details",
    )
    assert model.contacts[0].value == "7994400"


def test_thousands_separators_do_not_break_number_matching():
    model, _ = _ground(
        {"compensation": {"basic_salary": 32632, "salary_state": "listed"}},
        "Basic salary -/32,632 per month",
    )
    assert model.compensation.basic_salary == 32632


# --- dates --------------------------------------------------------------

def test_a_parseable_in_range_date_survives():
    model, _ = _ground({"deadline": "2026-08-31"}, GAZETTE_JOB_BODY)
    assert model.deadline == "2026-08-31"


def test_an_unparseable_date_is_dropped():
    model, report = _ground({"deadline": "next Thursday"}, GAZETTE_JOB_BODY)
    assert model.deadline == ""
    assert any(d["reason"] == "bad_date" for d in report["dropped"])


def test_a_date_outside_the_sane_range_is_dropped():
    model, report = _ground({"deadline": "1953-01-01"}, "deadline 1953-01-01")
    assert model.deadline == ""
    assert any(d["reason"] == "date_out_of_range" for d in report["dropped"])


# --- the negotiable rule ------------------------------------------------

def test_negotiable_survives_when_the_source_says_so():
    model, _ = _ground(
        {"compensation": {"salary_state": "negotiable"}},
        "Salary negotiable depending on experience",
    )
    assert model.compensation.salary_state == "negotiable"


def test_negotiable_is_demoted_to_unlisted_when_the_source_is_silent():
    """Spec 4.3: a missing salary is `unlisted`, never `negotiable`. Those are
    different claims and the card renders them differently."""
    model, report = _ground(
        {"compensation": {"salary_state": "negotiable"}},
        "Looking for a cashier. Call 9483252.",
    )
    assert model.compensation.salary_state == "unlisted"
    assert any(d["reason"] == "negotiable_unsupported" for d in report["dropped"])


@pytest.mark.parametrize(
    "text",
    ["salary negotiable", "Salary is Negotiable", "pay to be discussed",
     "މުސާރަ: އެއްބަސްވެވޭ ގޮތެއްގެ މަތިން"],
)
def test_negotiable_markers(text):
    model, _ = _ground({"compensation": {"salary_state": "negotiable"}}, text)
    assert model.compensation.salary_state == "negotiable"


# --- scraped fields win -------------------------------------------------

def test_the_model_may_fill_a_null_scraped_field():
    model, report = _ground(
        {"employer": "Ministry of Example"},
        "Ministry of Example is hiring",
        scraped={"employer": ""},
    )
    assert model.employer == "Ministry of Example"
    assert report["needs_review"] is False


def test_the_model_may_not_overwrite_a_scraped_field():
    """Spec 5.2 layer 4. The scraped value stays and the record is flagged."""
    model, report = _ground(
        {"employer": "Ministry of Example"},
        "Ministry of Example is hiring",
        scraped={"employer": "Ministry of Health"},
    )
    assert model.employer == "Ministry of Health"
    assert report["needs_review"] is True
    assert any(d["reason"] == "scraped_conflict" for d in report["dropped"])


def test_an_identical_value_is_not_a_conflict():
    model, report = _ground(
        {"employer": "Ministry of Health"},
        "Ministry of Health is hiring",
        scraped={"employer": "Ministry of Health"},
    )
    assert report["needs_review"] is False


# --- completeness is derived, not trusted -------------------------------

def test_completeness_is_recomputed_from_what_survived():
    """The model claimed `full`, but the allowance was dropped as ungrounded,
    so the estimate is now partial and the card must say 'at least'."""
    model, _ = _ground(
        {"compensation": {
            "basic_salary": 10750, "salary_state": "listed", "completeness": "full",
            "allowances": [{"kind": "living", "label_raw": "living", "amount": 9999,
                            "basis": "fixed_monthly"}],
        }},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.allowances == []
    assert model.compensation.completeness == "partial"


def test_basic_only_when_no_allowances_were_claimed():
    model, _ = _ground(
        {"compensation": {"basic_salary": 10750, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.completeness == "basic_only"


# --- schema violations --------------------------------------------------

def test_an_unknown_enum_value_does_not_lose_the_whole_record():
    model, report = _ground(
        {"role": "Administrative Officer", "position_type": "Permanent",
         "compensation": {"period": "fortnight"}},
        "Administrative Officer, Permanent",
    )
    assert model.role == "Administrative Officer"
    assert model.compensation.period == "month"        # default restored
    assert any(d["reason"] == "schema" for d in report["dropped"])


def test_garbage_attrs_yields_an_empty_model_not_an_exception():
    model, report = _ground({"role": {"nested": "object"}}, "text")
    assert model.role == ""
    assert report["dropped"]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.validate'`

- [ ] **Step 3: Write the implementation**

`enrich/validate.py`:

```python
"""Layer 3: the grounding validator. Spec 5.2.

Every extracted string must be traceable to the source text: an exact
substring after normalization, or at least 0.85 token overlap. Every number
must appear as digits in the candidate set. Every date must parse and land in
a sane range. A field that fails is dropped and the reason is recorded.
Nothing is repaired by guessing.

Three rules here are not generic validation and exist for named failures:

- `negotiable` is demoted to `unlisted` unless the source contains a
  negotiability marker, because those are different claims on the card.
- `completeness` is recomputed from what actually survived, not taken from the
  model, so a dropped allowance turns a point estimate into a floor.
- A scraped field is never overwritten; a conflict keeps the scraped value and
  flags the record `needs_review`.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from pydantic import ValidationError

from enrich.preextract import Candidates
from enrich.schemas import ATTRS_FOR_TYPE

STRING_OVERLAP_FLOOR = 0.85
MIN_YEAR = 2000
MAX_YEARS_AHEAD = 10
# Below this length a substring match is meaningless -- 'GS3' is fine, but a
# two-character string matches almost anything.
MIN_GROUNDED_LEN = 3

_PUNCT = re.compile(r"[^\w\sހ-޿]", re.UNICODE)
_WS = re.compile(r"\s+")

_NEGOTIABLE_MARKERS = (
    "negotiable", "negotiation", "negotiate", "to be discussed",
    "depending on experience", "as per experience", "doe",
    "އެއްބަސްވެވޭ", "މަޝްވަރާ",
)

# Fields whose values are enums, free labels or lists of short tokens that the
# model is allowed to normalize rather than copy. Grounding a normalized
# category against the raw text would drop almost all of them.
_UNGROUNDED_STRING_FIELDS = {
    "position_type", "job_category", "listing_kind", "unit_kind",
    "furnishing", "condition", "seller_type", "delivery", "announcement_type",
    "period", "basis", "kind", "price_period", "datatype", "widget",
    "tenant_preference", "shared_facilities", "category_path", "keywords",
}


def normalize_for_match(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def token_overlap(a: str, b: str) -> float:
    """Fraction of `a`'s tokens that appear in `b`. Asymmetric on purpose:
    the question is whether the extracted value is supported by the source,
    not whether the source is summarized by the value."""
    ta = set(normalize_for_match(a).split())
    tb = set(normalize_for_match(b).split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def _digit_forms(value) -> set[str]:
    """Every way `value` might be written in the source."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return set()
    out = set()
    if f.is_integer():
        i = int(f)
        out.add(str(i))
        out.add(f"{i:,}")
    else:
        out.add(str(f))
        out.add(f"{f:,}")
    return out


class _Report:
    def __init__(self):
        self.dropped: list[dict] = []
        self.needs_review = False

    def drop(self, field: str, value, reason: str):
        self.dropped.append({"field": field, "value": _jsonable(value),
                             "reason": reason})

    def as_dict(self) -> dict:
        return {"dropped": self.dropped, "needs_review": self.needs_review}


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _string_is_grounded(value: str, source_norm: str) -> bool:
    if len(value.strip()) < MIN_GROUNDED_LEN:
        return True
    v = normalize_for_match(value)
    if not v:
        return True
    if v in source_norm:
        return True
    return token_overlap(value, source_norm) >= STRING_OVERLAP_FLOOR


def _date_is_sane(value: str) -> str | None:
    """Returns a reason string when the date is bad, None when it is fine."""
    try:
        d = dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return "bad_date"
    today = dt.date.today()
    if d.year < MIN_YEAR or d.year > today.year + MAX_YEARS_AHEAD:
        return "date_out_of_range"
    return None


def _walk(node, path, *, source_norm, numeric_ok, report):
    """Recursively prune ungrounded leaves out of the raw attrs dict."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            kept = _walk(v, f"{path}.{k}" if path else k,
                         source_norm=source_norm, numeric_ok=numeric_ok,
                         report=report)
            if kept is not None:
                out[k] = kept
        return out

    if isinstance(node, list):
        out_list = []
        for i, v in enumerate(node):
            kept = _walk(v, f"{path}[{i}]", source_norm=source_norm,
                         numeric_ok=numeric_ok, report=report)
            if kept is None:
                continue
            # An object whose identifying value was dropped is not worth
            # keeping: an allowance with no amount, a contact with no number.
            if isinstance(kept, dict) and _is_empty_item(v, kept):
                report.drop(path, v, "not_grounded")
                continue
            out_list.append(kept)
        return out_list

    leaf = path.rsplit(".", 1)[-1].split("[")[0]

    if isinstance(node, bool) or node is None:
        return node

    if isinstance(node, (int, float)):
        if leaf in {"doc_type_confidence", "pension_rate", "priority"}:
            return node
        if not (_digit_forms(node) & numeric_ok):
            report.drop(path, node, "not_grounded")
            return None
        return node

    if isinstance(node, str):
        if leaf in {"deadline", "apply_before", "published"} and node:
            reason = _date_is_sane(node)
            if reason:
                report.drop(path, node, reason)
                return None
            return node
        if leaf in _UNGROUNDED_STRING_FIELDS:
            return node
        if leaf == "value":
            # contact / apply-method values: phone numbers, emails, URLs. Those
            # are all in the candidate set verbatim.
            if node and normalize_for_match(node) not in source_norm:
                report.drop(path, node, "not_grounded")
                return None
            return node
        if node and not _string_is_grounded(node, source_norm):
            report.drop(path, node, "not_grounded")
            return None
        return node

    report.drop(path, node, "unexpected_type")
    return None


def _is_empty_item(original: dict, kept: dict) -> bool:
    """True when the identifying field of a list item did not survive."""
    for identifying in ("amount", "value", "value_num", "value_text"):
        if identifying in original and identifying not in kept:
            return True
    return False


def _apply_scraped(attrs: dict, scraped: dict, report: _Report) -> dict:
    """Layer 4. Scraped fields win; a conflict flags the record."""
    for key, truth in scraped.items():
        if truth in (None, "", [], {}):
            continue
        claimed = attrs.get(key)
        if claimed in (None, "", [], {}):
            attrs[key] = truth
            continue
        if normalize_for_match(str(claimed)) != normalize_for_match(str(truth)):
            report.drop(key, claimed, "scraped_conflict")
            report.needs_review = True
        attrs[key] = truth
    return attrs


def _fix_compensation(attrs: dict, source_text: str, claimed_allowances: int):
    """The negotiable rule and the completeness recomputation."""
    comp = attrs.get("compensation")
    if not isinstance(comp, dict):
        return None

    reason = None
    if comp.get("salary_state") == "negotiable":
        low = source_text.lower()
        if not any(m in low for m in _NEGOTIABLE_MARKERS):
            comp["salary_state"] = "unlisted"
            reason = "negotiable_unsupported"

    has_basic = bool(comp.get("basic_salary"))
    kept_allowances = len(comp.get("allowances") or [])
    if not has_basic:
        comp["completeness"] = "none"
    elif claimed_allowances == 0:
        comp["completeness"] = "basic_only"
    elif kept_allowances == claimed_allowances:
        comp["completeness"] = "full"
    else:
        comp["completeness"] = "partial"
    return reason


def ground(
    raw_attrs: dict,
    *,
    doc_type: str,
    source_text: str,
    candidates: Candidates,
    scraped: dict | None = None,
):
    """Prune, then parse. Returns (validated_model, report_dict)."""
    report = _Report()
    model_cls = ATTRS_FOR_TYPE.get(doc_type, ATTRS_FOR_TYPE["news"])

    if not isinstance(raw_attrs, dict):
        report.drop("attrs", raw_attrs, "unexpected_type")
        return model_cls(), report.as_dict()

    claimed_allowances = len(
        ((raw_attrs.get("compensation") or {}).get("allowances") or [])
        if isinstance(raw_attrs.get("compensation"), dict) else []
    )

    source_norm = normalize_for_match(source_text)
    numeric_ok = candidates.all_numeric_strings()

    pruned = _walk(raw_attrs, "", source_norm=source_norm,
                   numeric_ok=numeric_ok, report=report)
    pruned = _apply_scraped(pruned, scraped or {}, report)

    reason = _fix_compensation(pruned, source_text, claimed_allowances)
    if reason:
        report.drop("compensation.salary_state", "negotiable", reason)

    try:
        model = model_cls(**pruned)
    except ValidationError as exc:
        # Drop only the offending keys and re-parse, so one bad enum does not
        # cost the whole record.
        bad = {e["loc"][0] for e in exc.errors() if e.get("loc")}
        for key in bad:
            report.drop(str(key), pruned.get(key), "schema")
            pruned.pop(key, None)
        try:
            model = model_cls(**pruned)
        except ValidationError:
            report.drop("attrs", None, "schema")
            model = model_cls()

    return model, report.as_dict()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/enrich/test_validate.py -v`
Expected: PASS.

Two failures are expected during development and both point at real gaps:

- `test_an_unknown_enum_value_does_not_lose_the_whole_record` — Pydantic reports the error at `('compensation', 'period')`, so `exc.errors()[0]["loc"][0]` is `compensation` and the whole compensation block gets dropped rather than the one field. Fix by walking the full `loc` path and popping the leaf, not the root. Add that walk before moving on; the test as written asserts `basic_salary` survives.
- `test_a_string_that_is_not_in_the_source_is_dropped` will pass only if `employer` is not in `_UNGROUNDED_STRING_FIELDS`. It must not be — employer is a claim about a real organization and is exactly the kind of string worth grounding.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P4 task 7: grounding validator"
```

---

### Task 8: Card and attrs builders

**Files:**
- Create: `enrich/cards.py`
- Test: `tests/enrich/test_cards.py`

**Interfaces:**
- Consumes: `enrich.schemas`, `enrich.compensation`.
- Produces: `build_card(doc_type, attrs_model, *, base) -> dict`, `capacity_display(occ) -> str`, `rent_display(price, currency, period) -> str`, `spec_chips(specs, limit=3) -> list[str]`, `CARD_VERSION`.

`base` carries what the adapter already knows — source key, title, price, images, location — so the card builder is a pure function of (typed attrs + scraped base) and can be tested without a database.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_cards.py`:

```python
import pytest

from enrich.cards import build_card, capacity_display, rent_display, spec_chips
from enrich.schemas import (
    Allowance, Compensation, JobAttrs, NewsAttrs, Occupancy,
    PropertyAttrs, ShoppingAttrs, Spec,
)


# --- jobs ---------------------------------------------------------------

def test_job_card_leads_with_role_employer_salary():
    attrs = JobAttrs(
        role="Administrative Officer", employer="Ministry of Example", grade="GS3",
        compensation=Compensation(
            basic_salary=10750,
            allowances=[Allowance(kind="attendance", label_raw="ހާޒިރީ",
                                  amount=4400, basis="fixed_monthly")],
            pension_applies=True, salary_state="listed", completeness="full",
        ),
        deadline="2026-08-31",
        apply_methods=[{"kind": "form", "value": "https://forms.gle/x"},
                       {"kind": "email", "value": "hr@example.gov.mv"}],
    )
    card = build_card("job", attrs, base={"source": "gazette",
                                          "detail_source": "attachment"})
    assert card["role"] == "Administrative Officer"
    assert card["employer"] == "Ministry of Example"
    assert card["salary_display"] == "MVR 10,750 / month"
    assert card["salary_state"] == "listed"
    assert card["net_estimate"]["value"] == pytest.approx(14397.50)
    assert card["net_estimate"]["is_floor"] is False
    assert card["apply_kinds"] == ["form", "email"]
    assert card["detail_source"] == "attachment"


def test_job_card_carries_the_line_items_so_the_client_can_recompute():
    """Spec 4.3.2: the working-days control recomputes client-side from the
    same pure logic; nothing is re-fetched."""
    attrs = JobAttrs(compensation=Compensation(
        basic_salary=8000,
        allowances=[Allowance(kind="attendance", label_raw="daily", amount=100,
                              basis="per_day")],
        pension_applies=True, salary_state="listed", completeness="full"))
    card = build_card("job", attrs, base={})
    assert card["compensation"]["allowances"][0]["basis"] == "per_day"
    assert card["compensation"]["allowances"][0]["amount"] == 100


def test_job_card_stores_the_raw_deadline_and_no_computed_state():
    """Spec 8: nothing time-dependent in `card`. A gazette document is written
    once and never reprocessed, so a frozen `deadline_state` would advertise a
    closed vacancy as open indefinitely."""
    card = build_card("job", JobAttrs(deadline="2026-08-31"), base={})
    assert card["deadline"] == "2026-08-31"
    assert "deadline_state" not in card
    assert "days_left" not in card
    assert "is_open" not in card


def test_job_card_omits_a_net_estimate_that_would_restate_basic():
    card = build_card("job", JobAttrs(compensation=Compensation(
        basic_salary=10000, salary_state="listed", completeness="basic_only")),
        base={})
    assert card["net_estimate"] is None


@pytest.mark.parametrize(
    "state,expected",
    [("negotiable", "Negotiable"), ("unlisted", "Unlisted")],
)
def test_job_card_salary_display_never_null(state, expected):
    card = build_card("job", JobAttrs(compensation=Compensation(salary_state=state)),
                      base={})
    assert card["salary_display"] == expected


# --- property -----------------------------------------------------------

@pytest.mark.parametrize(
    "occ,expected",
    [
        (Occupancy(unit_kind="whole_unit", rooms_total=3), "Whole unit, 3 rooms"),
        (Occupancy(unit_kind="room", rooms_offered=1, rooms_total=3, is_shared=True),
         "1 room of 3, shared"),
        (Occupancy(unit_kind="bed_space", beds_offered=2, is_shared=True),
         "Bed space, 2 available, shared"),
        (Occupancy(unit_kind="guest_house", max_occupants=4),
         "Guest house room, up to 4"),
        (Occupancy(unit_kind="whole_unit"), "Whole unit"),
        (Occupancy(unit_kind="land"), "Land"),
    ],
)
def test_capacity_display_table(occ, expected):
    assert capacity_display(occ) == expected


def test_one_room_of_three_never_renders_as_three_bedrooms():
    """The concrete failure spec 8.2 exists to prevent."""
    attrs = PropertyAttrs(
        occupancy=Occupancy(unit_kind="room", rooms_offered=1, rooms_total=3,
                            is_shared=True),
        bedrooms=3,
    )
    card = build_card("property", attrs, base={"price": 7000, "currency": "MVR"})
    assert card["capacity_display"] == "1 room of 3, shared"
    assert card["is_shared"] is True


@pytest.mark.parametrize(
    "price,currency,period,expected",
    [
        (7000, "MVR", "month", "MVR 7,000 / month"),
        (450, "USD", "month", "USD 450 / month"),
        (300, "MVR", "day", "MVR 300 / day"),
        (None, "MVR", "month", "Price on request"),
    ],
)
def test_rent_display(price, currency, period, expected):
    assert rent_display(price, currency, period) == expected


def test_property_card_marks_an_inferred_currency():
    card = build_card("property", PropertyAttrs(currency_inferred=True),
                      base={"price": 7000, "currency": "MVR"})
    assert card["currency_inferred"] is True


# --- shopping -----------------------------------------------------------

def test_spec_chips_are_capped_and_formatted():
    specs = [Spec(key_raw="voltage", value_num=24, unit="V"),
             Spec(key_raw="current", value_num=5, unit="A"),
             Spec(key_raw="power", value_num=120, unit="W"),
             Spec(key_raw="colour", value_text="black")]
    assert spec_chips(specs) == ["24V", "5A", "120W"]


def test_shopping_card():
    card = build_card(
        "shopping",
        ShoppingAttrs(condition="Used", brand="Apple",
                      specs=[Spec(key_raw="storage", value_num=128, unit="GB")]),
        base={"title": "iPhone 13", "price": 9500, "currency": "MVR",
              "hero_image": "https://x/1.jpg", "image_count": 4,
              "seller_name": "Ali", "seller_is_premium": True},
    )
    assert card["price_display"] == "MVR 9,500"
    assert card["condition"] == "Used"
    assert card["spec_chips"] == ["128GB"]
    assert card["seller_is_premium"] is True


# --- news ---------------------------------------------------------------

def test_news_card_is_four_things_and_nothing_else():
    """Spec 8.4: icon, title, excerpt, link out."""
    card = build_card(
        "news", NewsAttrs(office="Ministry of Example", announcement_type="ބީލަން",
                          is_tender=True),
        base={"source": "gazette", "title": "Tender for X",
              "summary": "The ministry invites bids for X.",
              "external_url": "https://gazette.gov.mv/iulaan/1",
              "attachment_count": 2, "published_at": "2026-08-01"},
    )
    assert card["source"] == "gazette"
    assert card["title"] == "Tender for X"
    assert card["summary"] == "The ministry invites bids for X."
    assert card["external_url"].startswith("https://")
    assert card["attachment_count"] == 2
    assert set(card) == {
        "source", "title", "summary", "office", "announcement_type",
        "published_at", "external_url", "attachment_count", "is_tender",
    }


def test_every_card_carries_its_source():
    for doc_type, attrs in [("job", JobAttrs()), ("property", PropertyAttrs()),
                            ("shopping", ShoppingAttrs()), ("news", NewsAttrs())]:
        card = build_card(doc_type, attrs, base={"source": "ibay"})
        assert card["source"] == "ibay", doc_type


def test_no_card_embeds_an_icon_path():
    """Spec 4.3.3: card stores the source key, not the icon URL. Embedding a
    path would duplicate the same string across 71,445 rows and make
    re-skinning a source a full reindex."""
    for doc_type, attrs in [("job", JobAttrs()), ("property", PropertyAttrs()),
                            ("shopping", ShoppingAttrs()), ("news", NewsAttrs())]:
        card = build_card(doc_type, attrs, base={"source": "ibay"})
        assert not any("icon" in k or "svg" in str(v) for k, v in card.items())
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_cards.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`enrich/cards.py`:

```python
"""Card payload builders. Spec 8.1 through 8.5.

The `card` JSONB carries exactly what its card component renders, already
resolved, so the frontend does no formatting decisions and no joins.

Two constraints hold across all four builders:

- Nothing time-dependent. `card` stores raw dates; deadline_state, freshness
  and relative-time labels are computed per request. A gazette document is
  written once and never reprocessed, so a frozen "3 days left" is wrong the
  next morning and a frozen "open" is wrong forever.
- The source key, never an icon path. The frontend resolves it against /meta.
"""

from __future__ import annotations

from enrich.compensation import estimate_net, salary_display
from enrich.schemas import Occupancy, Spec

# Bump when a card's field set changes. Triggers a reindex rather than a
# runtime lookup. Spec 8.
CARD_VERSION = 1


def _money(value, currency="MVR") -> str | None:
    if value is None:
        return None
    return f"{currency} {float(value):,.0f}"


def capacity_display(occ: Occupancy) -> str:
    """The field most likely to mislead if done carelessly, so it states the
    shape explicitly rather than reducing everything to a room count."""
    kind = occ.unit_kind
    shared = ", shared" if occ.is_shared else ""

    if kind == "room":
        if occ.rooms_offered and occ.rooms_total:
            return f"{occ.rooms_offered} room of {occ.rooms_total}{shared}"
        if occ.rooms_offered:
            return f"{occ.rooms_offered} room{shared}"
        return f"Room{shared}"

    if kind == "bed_space":
        if occ.beds_offered:
            return f"Bed space, {occ.beds_offered} available{shared}"
        return f"Bed space{shared}"

    if kind == "guest_house":
        if occ.max_occupants:
            return f"Guest house room, up to {occ.max_occupants}"
        return "Guest house room"

    if kind == "whole_unit":
        if occ.rooms_total:
            return f"Whole unit, {occ.rooms_total} rooms"
        return "Whole unit"

    if kind == "land":
        return "Land"
    if kind == "commercial":
        return "Commercial space"
    return "Whole unit"


def rent_display(price, currency: str, period: str) -> str:
    if price is None:
        return "Price on request"
    return f"{currency or 'MVR'} {float(price):,.0f} / {period or 'month'}"


def spec_chips(specs: list[Spec], limit: int = 3) -> list[str]:
    """Up to three compact chips: '24V', '120W', '128GB'."""
    out: list[str] = []
    for s in specs:
        if s.value_num is None or not s.unit:
            continue
        n = int(s.value_num) if float(s.value_num).is_integer() else s.value_num
        out.append(f"{n}{s.unit}")
        if len(out) >= limit:
            break
    return out


def _job_card(a, base: dict) -> dict:
    est = estimate_net(a.compensation)
    return {
        "source": base.get("source", ""),
        "role": a.role or base.get("title", ""),
        "employer": a.employer or base.get("employer", ""),
        "employer_logo": base.get("employer_logo"),
        "salary_display": salary_display(a.compensation),
        "salary_state": a.compensation.salary_state,
        "net_estimate": est.as_dict() if est else None,
        "compensation": a.compensation.model_dump(),
        "grade": a.grade,
        "location": base.get("location", ""),
        "position_type": a.position_type,
        # raw date only; state is computed at query time
        "deadline": a.deadline,
        "apply_kinds": [m.kind for m in a.apply_methods],
        "detail_source": base.get("detail_source", "listing"),
        "source_label": base.get("source_label", ""),
    }


def _property_card(a, base: dict) -> dict:
    return {
        "source": base.get("source", ""),
        "hero_image": base.get("hero_image"),
        "image_count": base.get("image_count", 0),
        "location_display": base.get("location", "") or a.neighborhood,
        "rent_display": rent_display(base.get("price"), base.get("currency", "MVR"),
                                     a.price_period),
        "currency": base.get("currency", "MVR"),
        "currency_inferred": a.currency_inferred,
        "capacity_display": capacity_display(a.occupancy),
        "unit_kind": a.occupancy.unit_kind,
        "is_shared": a.occupancy.is_shared,
        "bedrooms": a.bedrooms,
        "bathrooms": a.bathrooms,
        "furnishing": a.furnishing,
        "tenant_preference": a.tenant_preference or a.occupancy.tenant_preference,
    }


def _shopping_card(a, base: dict) -> dict:
    return {
        "source": base.get("source", ""),
        "hero_image": base.get("hero_image"),
        "image_count": base.get("image_count", 0),
        "title": base.get("title", ""),
        "price_display": _money(base.get("price"), base.get("currency", "MVR")),
        "currency": base.get("currency", "MVR"),
        "negotiable": a.negotiable,
        "condition": a.condition,
        "brand": a.brand,
        "location": base.get("location", ""),
        "seller_name": base.get("seller_name", ""),
        "seller_is_premium": base.get("seller_is_premium", False),
        "spec_chips": spec_chips(a.specs),
    }


def _news_card(a, base: dict) -> dict:
    """Four things and nothing else: icon, title, excerpt, link out. The rest
    is context that costs nothing to carry."""
    return {
        "source": base.get("source", ""),
        "title": base.get("title", ""),
        "summary": base.get("summary", ""),
        "office": a.office,
        "announcement_type": a.announcement_type,
        "published_at": base.get("published_at"),
        "external_url": base.get("external_url", ""),
        "attachment_count": base.get("attachment_count", 0),
        "is_tender": a.is_tender,
    }


_BUILDERS = {
    "job": _job_card,
    "property": _property_card,
    "shopping": _shopping_card,
    "news": _news_card,
}


def build_card(doc_type: str, attrs_model, *, base: dict) -> dict:
    return _BUILDERS.get(doc_type, _news_card)(attrs_model, base)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/enrich/test_cards.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P4 task 8: card payload builders"
```

---

### Task 9: The overlay hook — enrichment reaches the index

**Files:**
- Create: `enrich/overlay.py`
- Modify: `search/indexing.py`, `beynunehcheh/settings.py`
- Test: `tests/enrich/test_overlay.py`

**Interfaces:**
- Consumes: `enrich.models.EnrichedRecord`, `enrich.cards.build_card`, `search.adapters.base.DocumentDraft`.
- Produces: `apply_enrichment(draft) -> DocumentDraft`; setting `SEARCH_DRAFT_OVERLAYS: list[str]`; `search.indexing._overlays()`.

`search` must not import `enrich`. The dependency runs the other way — enrichment is an optional layer over a search index that works without it (spec 5.2: indexing never blocks on enrichment). A settings-declared dotted path keeps that true and makes the overlay trivially disableable in tests.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_overlay.py`:

```python
import pytest

from enrich.models import EnrichedRecord
from enrich.overlay import apply_enrichment
from search.adapters.base import DocumentDraft


def _draft(**kw):
    base = dict(source="gazette", source_key="IUL-1", doc_type="news",
                url="https://gazette.gov.mv/iulaan/1", title_en="Raw title",
                summary_en="raw", card={"source": "gazette", "title": "Raw title"},
                content_hash="h" * 64)
    base.update(kw)
    return DocumentDraft(**base)


@pytest.mark.django_db
def test_a_draft_with_no_record_passes_through_untouched():
    d = _draft()
    out = apply_enrichment(d)
    assert out is d


@pytest.mark.django_db
def test_a_failed_record_does_not_degrade_the_draft():
    """Indexing never blocks on enrichment. Spec 5.2."""
    EnrichedRecord.objects.create(source="gazette", source_key="IUL-1",
                                  content_hash="h" * 64, doc_type="job",
                                  status="failed")
    out = apply_enrichment(_draft())
    assert out.doc_type == "news"
    assert out.title_en == "Raw title"


@pytest.mark.django_db
def test_a_stale_hash_record_is_ignored():
    """The record describes text that no longer exists. Using its attrs would
    attach last month's salary to this month's listing."""
    EnrichedRecord.objects.create(source="gazette", source_key="IUL-1",
                                  content_hash="OLD", doc_type="job", status="ok",
                                  canonical_title_en="Officer")
    out = apply_enrichment(_draft(content_hash="NEW"))
    assert out.title_en == "Raw title"


@pytest.mark.django_db
def test_an_ok_record_supplies_doc_type_title_summary_attrs_and_card():
    EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="h" * 64,
        doc_type="job", status="ok",
        canonical_title_en="Administrative Officer",
        canonical_title_dv="އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ",
        summary_en="A GS3 post at the Ministry of Example.",
        attrs={"role": "Administrative Officer", "employer": "Ministry of Example",
               "compensation": {"basic_salary": 10750, "salary_state": "listed",
                                "completeness": "basic_only"}},
        keywords=["officer", "GS3"],
    )
    out = apply_enrichment(_draft())
    assert out.doc_type == "job"
    assert out.title_en == "Administrative Officer"
    assert out.title_dv == "އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ"
    assert out.summary_en.startswith("A GS3 post")
    assert out.attrs["role"] == "Administrative Officer"
    assert out.card["role"] == "Administrative Officer"
    assert out.card["salary_display"] == "MVR 10,750 / month"


@pytest.mark.django_db
def test_needs_review_still_supplies_what_survived():
    """A conflict on one field is not a reason to discard the other nine."""
    EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="h" * 64,
        doc_type="job", status="needs_review",
        canonical_title_en="Administrative Officer", attrs={"role": "Officer"},
    )
    out = apply_enrichment(_draft())
    assert out.doc_type == "job"
    assert out.card["role"] == "Officer"


@pytest.mark.django_db
def test_keywords_are_folded_into_the_search_text_not_into_the_card():
    EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="h" * 64,
        doc_type="news", status="ok", keywords=["tender", "ބީލަން"],
        summary_en="Bids invited.",
    )
    out = apply_enrichment(_draft(text_en="body text"))
    assert "tender" in out.text_en
    assert "ބީލަން" in out.text_dv
    assert "keywords" not in out.card


@pytest.mark.django_db
def test_the_overlay_never_clears_stale_marked_at():
    """reindex is the last stage in the chain and the only one that clears the
    work ticket. If enrichment cleared it, `enrich_documents --stale` followed
    by `reindex --stale` would index nothing. Spec 5.7."""
    from search.models import SearchDocument
    from django.utils import timezone
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x",
                                  stale_marked_at=timezone.now())
    apply_enrichment(_draft())
    assert SearchDocument.objects.get().stale_marked_at is not None
```

Add to `tests/search/test_indexing.py` (or create `tests/search/test_overlay_hook.py`):

```python
import pytest
from django.test import override_settings

from search import indexing
from search.adapters.base import DocumentDraft


def _tag_overlay(draft):
    draft.title_en = draft.title_en + " [OVERLAID]"
    return draft


@override_settings(SEARCH_DRAFT_OVERLAYS=["tests.search.test_overlay_hook._tag_overlay"])
def test_overlays_are_applied_in_order():
    d = DocumentDraft(source="ibay", source_key="1", doc_type="shopping",
                      url="https://x", title_en="Thing")
    out = indexing.apply_overlays(d)
    assert out.title_en == "Thing [OVERLAID]"


@override_settings(SEARCH_DRAFT_OVERLAYS=[])
def test_no_overlays_configured_is_a_no_op():
    d = DocumentDraft(source="ibay", source_key="1", doc_type="shopping",
                      url="https://x", title_en="Thing")
    assert indexing.apply_overlays(d) is d
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_overlay.py tests/search/test_overlay_hook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrich.overlay'` and `AttributeError: module 'search.indexing' has no attribute 'apply_overlays'`

- [ ] **Step 3: Add the hook to the indexer**

In `search/indexing.py`, add after the imports:

```python
from django.conf import settings
from django.utils.module_loading import import_string
```

and add:

```python
_OVERLAY_CACHE: list | None = None


def _overlays():
    """Resolved once per process. Spec 3.3: enrichment is a layer over the
    index, so `search` declares the seam and `enrich` fills it -- the import
    never runs the other way."""
    global _OVERLAY_CACHE
    if _OVERLAY_CACHE is None:
        _OVERLAY_CACHE = [
            import_string(path)
            for path in getattr(settings, "SEARCH_DRAFT_OVERLAYS", [])
        ]
    return _OVERLAY_CACHE


def apply_overlays(draft: DocumentDraft) -> DocumentDraft:
    for fn in _overlays():
        draft = fn(draft)
    return draft
```

Because `@override_settings` must be able to change the list mid-test, reset the cache in a `setting_changed` receiver. Add at the bottom of `search/indexing.py`:

```python
from django.core.signals import setting_changed
from django.dispatch import receiver


@receiver(setting_changed)
def _reset_overlay_cache(sender, setting, **kwargs):
    global _OVERLAY_CACHE
    if setting == "SEARCH_DRAFT_OVERLAYS":
        _OVERLAY_CACHE = None
```

Then in `reindex_source`, change:

```python
        draft = adapter.to_document(raw)
        if draft is None:
            continue
        buffer.append(draft)
```

to:

```python
        draft = adapter.to_document(raw)
        if draft is None:
            continue
        buffer.append(apply_overlays(draft))
```

- [ ] **Step 4: Add the setting**

In `beynunehcheh/settings.py`, in the enrichment block:

```python
# Draft overlays run between adapter.to_document() and upsert. `search` knows
# only these dotted paths; it never imports `enrich`.
SEARCH_DRAFT_OVERLAYS = [
    "enrich.overlay.apply_enrichment",
]
```

- [ ] **Step 5: Write the overlay**

`enrich/overlay.py`:

```python
"""Fold EnrichedRecord into a DocumentDraft. Spec 3.3, 5.2.

Called by search.indexing between the adapter and the upsert. Three rules:

- A record whose content_hash does not match the draft describes text that no
  longer exists, and applying it would attach last month's extraction to this
  month's listing. Ignored.
- `failed` records are ignored; `needs_review` records are applied. A conflict
  on one field is not a reason to discard the other nine.
- This function must not touch stale_marked_at. reindex clears it, and it must
  still be set when reindex runs.
"""

from __future__ import annotations

import logging

from enrich.cards import build_card
from enrich.models import EnrichedRecord
from enrich.schemas import ATTRS_FOR_TYPE
from search.adapters.base import DocumentDraft

logger = logging.getLogger(__name__)

_USABLE = ("ok", "needs_review")


def apply_enrichment(draft: DocumentDraft) -> DocumentDraft:
    record = (
        EnrichedRecord.objects
        .filter(source=draft.source, source_key=draft.source_key)
        .only("doc_type", "status", "content_hash", "canonical_title_en",
              "canonical_title_dv", "summary_en", "summary_dv", "attrs", "keywords")
        .first()
    )
    if record is None or record.status not in _USABLE:
        return draft
    if draft.content_hash and record.content_hash != draft.content_hash:
        logger.debug("enrichment hash mismatch for %s:%s", draft.source, draft.source_key)
        return draft

    draft.doc_type = record.doc_type or draft.doc_type

    if record.canonical_title_en:
        draft.title_en = record.canonical_title_en
    if record.canonical_title_dv:
        draft.title_dv = record.canonical_title_dv
    if record.summary_en:
        draft.summary_en = record.summary_en
    if record.summary_dv:
        draft.summary_dv = record.summary_dv

    model_cls = ATTRS_FOR_TYPE.get(draft.doc_type, ATTRS_FOR_TYPE["news"])
    try:
        attrs_model = model_cls(**(record.attrs or {}))
    except Exception:                      # already validated at write time
        logger.warning("unparseable stored attrs for %s:%s",
                       draft.source, draft.source_key)
        return draft

    draft.attrs = {**draft.attrs, **attrs_model.model_dump()}

    base = dict(draft.card)
    base.setdefault("source", draft.source)
    base.setdefault("title", draft.title_en or draft.title_dv)
    base.setdefault("summary", draft.summary_en or draft.summary_dv)
    base.setdefault("external_url", draft.url)
    base.setdefault("price", draft.price)
    base.setdefault("currency", draft.currency)
    base.setdefault("location", draft.location)
    base.setdefault("published_at",
                    draft.published_at.isoformat() if draft.published_at else None)
    draft.card = build_card(draft.doc_type, attrs_model, base=base)

    # Aliases and synonyms are search surface, not display surface, so they go
    # into the vectors and stay out of the card.
    if record.keywords:
        latin = [k for k in record.keywords if not _is_thaana(k)]
        thaana = [k for k in record.keywords if _is_thaana(k)]
        if latin:
            draft.text_en = f"{draft.text_en}\n{' '.join(latin)}".strip()
        if thaana:
            draft.text_dv = f"{draft.text_dv}\n{' '.join(thaana)}".strip()

    return draft


def _is_thaana(s: str) -> bool:
    return any("ހ" <= c <= "޿" for c in s)
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/enrich/test_overlay.py tests/search/ -v`
Expected: PASS. The P1 search suite must still be green — the overlay is inert when no `EnrichedRecord` exists, which every P1 test relies on.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P4 task 9: draft overlay hook, enrichment reaches the index"
```

---

### Task 10: The pipeline and `enrich_documents`

**Files:**
- Create: `enrich/pipeline.py`, `enrich/management/__init__.py`, `enrich/management/commands/__init__.py`, `enrich/management/commands/enrich_documents.py`
- Test: `tests/enrich/test_pipeline.py`, `tests/enrich/test_command.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build_input(source, source_key) -> EnrichInput | None`, `async def enrich_one(inp, client) -> EnrichedRecord`, `async def run_pass(...) -> dict`, `select_keys(...) -> Iterator[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

`tests/enrich/test_pipeline.py`:

```python
import json

import pytest
from django.utils import timezone

from enrich.models import EnrichedRecord
from enrich.pipeline import build_input, enrich_one, select_keys
from search.models import SearchDocument


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def run_chain(self, messages, rebuild=None):
        self.calls += 1
        return self.payload, "stub-model"

    async def aclose(self):
        pass


@pytest.fixture
def gazette_job(db):
    from gazette.models import Iulaan, IulaanType, Office
    t = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    o = Office.objects.create(name="Ministry of Example")
    return Iulaan.objects.create(
        id="IUL-1", title="އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ", office=o, iulaan_type=t,
        additional_info={}, attachments=[],
        body="އަސާސީ މުސާރަ: މަހަކު 10,750 ރުފިޔާ ފޯނު: 3323838",
    )


@pytest.mark.django_db
def test_build_input_carries_prior_scraped_and_candidates(gazette_job):
    inp = build_input("gazette", "IUL-1")
    assert inp.doc_type_prior == "job"
    assert inp.scraped["office"] == "Ministry of Example"
    assert "3323838" in inp.candidates.phones
    assert 10750.0 in [m["amount"] for m in inp.candidates.money]


@pytest.mark.django_db
def test_build_input_returns_none_for_a_missing_key():
    assert build_input("gazette", "nope") is None


@pytest.mark.django_db
async def test_enrich_one_writes_an_ok_record(gazette_job):
    inp = await sync_to_async(build_input)("gazette", "IUL-1")
    client = _StubClient({
        "doc_type": "job", "doc_type_confidence": 0.95,
        "canonical_title_en": "Administrative Officer",
        "summary_en": "A post at the Ministry of Example.",
        "attrs": {"compensation": {"basic_salary": 10750, "salary_state": "listed"}},
    })
    rec = await enrich_one(inp, client)
    assert rec.status == "ok"
    assert rec.doc_type == "job"
    assert rec.attrs["compensation"]["basic_salary"] == 10750
    assert rec.model_name == "stub-model"
    assert rec.attempts == 1


@pytest.mark.django_db
async def test_low_confidence_override_loses_to_the_prior(gazette_job):
    inp = await sync_to_async(build_input)("gazette", "IUL-1")
    client = _StubClient({"doc_type": "shopping", "doc_type_confidence": 0.4,
                          "attrs": {}})
    rec = await enrich_one(inp, client)
    assert rec.doc_type == "job"          # the prior wins


@pytest.mark.django_db
async def test_an_ungrounded_salary_is_dropped_and_recorded(gazette_job):
    inp = await sync_to_async(build_input)("gazette", "IUL-1")
    client = _StubClient({
        "doc_type": "job",
        "attrs": {"compensation": {"basic_salary": 99999, "salary_state": "listed"}},
    })
    rec = await enrich_one(inp, client)
    assert rec.attrs["compensation"]["basic_salary"] is None
    assert rec.validation["dropped"]


@pytest.mark.django_db
async def test_a_provider_failure_records_failed_and_does_not_raise(gazette_job):
    from enrich.client import ProviderError

    class _Broken:
        async def run_chain(self, messages, rebuild=None):
            raise ProviderError("all stages failed")
        async def aclose(self):
            pass

    inp = await sync_to_async(build_input)("gazette", "IUL-1")
    rec = await enrich_one(inp, _Broken())
    assert rec.status == "failed"
    assert "all stages failed" in rec.error


# --- selection gates ----------------------------------------------------

@pytest.mark.django_db
def test_a_matching_hash_and_prompt_version_is_skipped():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="h",
                                  doc_type="shopping", status="ok", prompt_version=1)
    assert list(select_keys(source="ibay", prompt_version=1)) == []


@pytest.mark.django_db
def test_a_changed_hash_re_enriches():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="NEW")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="OLD",
                                  doc_type="shopping", status="ok", prompt_version=1)
    assert list(select_keys(source="ibay", prompt_version=1)) == [("ibay", "1")]


@pytest.mark.django_db
def test_a_prompt_version_bump_re_enriches_ibay():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="h",
                                  doc_type="shopping", status="ok", prompt_version=1)
    assert list(select_keys(source="ibay", prompt_version=2)) == [("ibay", "1")]


@pytest.mark.django_db
def test_a_prompt_version_bump_does_not_backfill_gazette():
    """Spec 5.7: gazette documents are write-once. Improving the prompt
    improves only newly-ingested iulaan, by design. Without this gate a
    version bump would re-bill 51,000 documents."""
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="gazette", source_key="IUL-1",
                                  content_hash="h", doc_type="news", status="ok",
                                  prompt_version=1)
    assert list(select_keys(source="gazette", prompt_version=2)) == []


@pytest.mark.django_db
def test_stale_marked_overrides_every_gate_including_gazette():
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x", content_hash="h",
                                  stale_marked_at=timezone.now())
    EnrichedRecord.objects.create(source="gazette", source_key="IUL-1",
                                  content_hash="h", doc_type="news", status="ok",
                                  prompt_version=1)
    assert list(select_keys(source="gazette", prompt_version=1)) == [
        ("gazette", "IUL-1")]


@pytest.mark.django_db
def test_only_stale_selects_nothing_when_nothing_is_marked():
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x", content_hash="h")
    assert list(select_keys(source="gazette", prompt_version=1,
                            only_stale=True)) == []


@pytest.mark.django_db
def test_failed_records_are_retried_up_to_the_attempt_cap():
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h")
    EnrichedRecord.objects.create(source="ibay", source_key="1", content_hash="h",
                                  doc_type="shopping", status="failed",
                                  prompt_version=1, attempts=1)
    assert list(select_keys(source="ibay", prompt_version=1)) == [("ibay", "1")]

    EnrichedRecord.objects.update(attempts=5)
    assert list(select_keys(source="ibay", prompt_version=1)) == []
```

Import `sync_to_async` at the top of that file: `from asgiref.sync import sync_to_async`.

`tests/enrich/test_command.py`:

```python
import pytest
from django.core.management import call_command
from django.utils import timezone

from search.models import SearchDocument


@pytest.mark.django_db
def test_dry_run_reports_the_count_and_spends_nothing(capsys):
    """Spec 5.7: a WHERE clause can mark 51,000 rows as easily as one, so the
    command reports what it is about to process before spending anything."""
    for i in range(3):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      content_hash="h")
    call_command("enrich_documents", "--source", "ibay", "--dry-run")
    out = capsys.readouterr().out
    assert "3" in out
    from enrich.models import EnrichedRecord
    assert EnrichedRecord.objects.count() == 0


@pytest.mark.django_db
def test_limit_is_respected(capsys):
    for i in range(10):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      content_hash="h")
    call_command("enrich_documents", "--source", "ibay", "--limit", "4", "--dry-run")
    assert "4" in capsys.readouterr().out


@pytest.mark.django_db
def test_the_command_does_not_clear_stale_marked_at(capsys, monkeypatch):
    SearchDocument.objects.create(source="ibay", source_key="1", doc_type="shopping",
                                  url="https://x", content_hash="h",
                                  stale_marked_at=timezone.now())
    call_command("enrich_documents", "--source", "ibay", "--stale", "--dry-run")
    assert SearchDocument.objects.get().stale_marked_at is not None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/enrich/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.pipeline'`

- [ ] **Step 3: Write the pipeline**

`enrich/pipeline.py`:

```python
"""Orchestration. Spec 5.4, 5.7.

Async with its own semaphore, separate from translation's, so the two
workloads never contend for the GPU or for rate limit headroom.

Idempotent and resumable: per-record try/except, an attempts counter, and a
selection query that skips anything already enriched at the current
content_hash and prompt_version.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Iterator

from asgiref.sync import sync_to_async
from django.conf import settings

from enrich.client import EnrichClient, ProviderError
from enrich.models import EnrichedRecord
from enrich.preextract import Candidates, extract_candidates
from enrich.prior import apply_confidence_gate, prior_for
from enrich.prompts import PROMPT_VERSION, build_messages
from enrich.schemas import EnrichmentOutput
from enrich.validate import ground
from search.adapters import base as adapters
from search.models import SearchDocument

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# The order the cold pass runs in. Jobs and news first: 641 documents, minutes
# and cents, and the typed facets with the most to gain. Shopping last: the
# corpus that costs the most. Spec 5.4.
COLD_PASS_ORDER = ("job", "news", "property", "shopping")


@dataclass(slots=True)
class EnrichInput:
    source: str
    source_key: str
    doc_type_prior: str
    title: str
    body: str
    scraped: dict
    candidates: Candidates
    content_hash: str


def _gazette_scraped(iulaan) -> dict:
    return {
        "office": iulaan.office.name if iulaan.office else "",
        "announcement_type": iulaan.iulaan_type.name if iulaan.iulaan_type else "",
    }


def _ibay_scraped(payload) -> dict:
    info = payload["info"]
    p = payload["product"]
    out = {
        "employer": info.get("Employer", ""),
        "position_type": info.get("Position Type", ""),
        "job_category": info.get("Job Category", ""),
        "condition": info.get("Item Condition", ""),
        "brand": info.get("Brand", ""),
        "neighborhood": info.get("Neighborhood", ""),
        "furnishing": info.get("Furnishing", ""),
        "floor": info.get("Floor", ""),
    }
    if p.product_location:
        out["location"] = p.product_location
    return {k: v for k, v in out.items() if v}


def build_input(source: str, source_key: str) -> EnrichInput | None:
    """Assemble everything the model call needs, from the adapter's raw payload.

    Reads through the adapter rather than the ORM directly so a new source
    needs no change here -- the only source-specific parts are the two
    `scraped` mappers and the prior.
    """
    adapter = adapters.get_adapter(source)
    raw = adapter.fetch_raw(source_key)
    if raw is None:
        return None
    draft = adapter.to_document(raw)
    if draft is None:
        return None

    if source == "gazette":
        iulaan = raw.payload["iulaan"]
        prior = prior_for("gazette",
                          iulaan_type=iulaan.iulaan_type.name if iulaan.iulaan_type else "")
        scraped = _gazette_scraped(iulaan)
        title = iulaan.title
    elif source == "ibay":
        prior = prior_for("ibay", categories=raw.payload["categories"])
        scraped = _ibay_scraped(raw.payload)
        title = raw.payload["product"].name
    else:
        prior = prior_for(source)
        scraped = {}
        title = draft.title_en or draft.title_dv

    # draft.text_* is what the adapter fed to the vectors: the body plus, from
    # P3, any attachment text. That is the exact text the model must see, and
    # the exact text the hash must cover.
    body = "\n".join(t for t in (draft.text_en, draft.text_dv) if t)
    body = body[: settings.ENRICH_MAX_INPUT_CHARS]

    return EnrichInput(
        source=source,
        source_key=source_key,
        doc_type_prior=prior,
        title=title,
        body=body,
        scraped=scraped,
        candidates=extract_candidates(f"{title}\n{body}"),
        content_hash=draft.content_hash
        or hashlib.sha256(body.encode()).hexdigest(),
    )


async def enrich_one(inp: EnrichInput, client) -> EnrichedRecord:
    """One document, one record. Never raises: a failure is a stored status."""
    def _messages(repair_error=None):
        return build_messages(
            source=inp.source,
            doc_type_prior=inp.doc_type_prior,
            title=inp.title,
            body=inp.body,
            candidates=inp.candidates,
            scraped=inp.scraped,
            repair_error=repair_error,
        )

    record, _ = await sync_to_async(EnrichedRecord.objects.get_or_create)(
        source=inp.source, source_key=inp.source_key,
        defaults={"content_hash": inp.content_hash,
                  "doc_type": inp.doc_type_prior},
    )
    record.attempts += 1
    record.content_hash = inp.content_hash
    record.prompt_version = PROMPT_VERSION

    try:
        payload, model_name = await client.run_chain(
            _messages(), rebuild=lambda err: _messages(repair_error=err)
        )
    except ProviderError as exc:
        record.status = "failed"
        record.error = str(exc)[:2000]
        # The prior is still a usable classification, so the document indexes
        # with scraped data and the rule-based type. Indexing never blocks on
        # enrichment (spec 5.2).
        record.doc_type = inp.doc_type_prior
        await sync_to_async(record.save)()
        return record

    out = EnrichmentOutput(**payload) if isinstance(payload, dict) else EnrichmentOutput()
    doc_type, _overridden = apply_confidence_gate(
        inp.doc_type_prior, out.doc_type, out.doc_type_confidence
    )

    attrs_model, report = ground(
        out.attrs,
        doc_type=doc_type,
        source_text=f"{inp.title}\n{inp.body}",
        candidates=inp.candidates,
        scraped=inp.scraped,
    )

    record.doc_type = doc_type
    record.doc_type_confidence = out.doc_type_confidence
    record.canonical_title_en = out.canonical_title_en[:512]
    record.canonical_title_dv = out.canonical_title_dv[:512]
    record.summary_en = out.summary_en[:240]
    record.summary_dv = out.summary_dv[:240]
    record.attrs = attrs_model.model_dump()
    record.keywords = out.keywords[:20]
    record.model_name = model_name
    record.validation = report
    record.status = "needs_review" if report["needs_review"] else "ok"
    record.error = ""
    await sync_to_async(record.save)()
    return record


def select_keys(
    *,
    source: str,
    prompt_version: int,
    doc_type: str | None = None,
    only_stale: bool = False,
    force: bool = False,
    limit: int | None = None,
) -> Iterator[tuple[str, str]]:
    """Which documents need the model. Spec 4.2, 5.7.

    Four gates, in order of precedence:
      1. stale_marked_at set  -> always, overriding everything
      2. --force              -> always
      3. content_hash changed -> always
      4. prompt_version bumped-> iBay only. Gazette is write-once (5.7), so a
         prompt improvement reaches only newly-ingested iulaan. Without this
         the next PROMPT_VERSION bump silently re-bills 51,000 documents.
    """
    qs = SearchDocument.objects.using("direct").filter(source=source)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)
    if only_stale:
        qs = qs.filter(stale_marked_at__isnull=False)

    existing = {
        (r.source_key): r
        for r in EnrichedRecord.objects.using("direct").filter(source=source).only(
            "source_key", "content_hash", "prompt_version", "status", "attempts"
        )
    }

    yielded = 0
    for doc in qs.only("source_key", "content_hash", "stale_marked_at").iterator(
        chunk_size=500
    ):
        if limit is not None and yielded >= limit:
            return

        rec = existing.get(doc.source_key)
        wanted = False

        if doc.stale_marked_at is not None or force or rec is None:
            wanted = True
        elif rec.content_hash != (doc.content_hash or ""):
            wanted = True
        elif rec.status == "failed" and rec.attempts < MAX_ATTEMPTS:
            wanted = True
        elif rec.prompt_version < prompt_version and source != "gazette":
            wanted = True

        if wanted:
            yielded += 1
            yield (source, doc.source_key)


async def run_pass(
    keys: list[tuple[str, str]], *, concurrency: int | None = None
) -> dict:
    """Run `keys` through the model with a bounded semaphore."""
    sem = asyncio.Semaphore(concurrency or settings.ENRICH_CONCURRENCY)
    client = EnrichClient()
    counts = {"ok": 0, "needs_review": 0, "failed": 0, "skipped": 0}

    async def _one(source, source_key):
        async with sem:
            inp = await sync_to_async(build_input)(source, source_key)
            if inp is None:
                counts["skipped"] += 1
                return
            rec = await enrich_one(inp, client)
            counts[rec.status] = counts.get(rec.status, 0) + 1

    try:
        await asyncio.gather(*(_one(s, k) for s, k in keys))
    finally:
        await client.aclose()
    return counts
```

- [ ] **Step 4: Write the command**

`enrich/management/commands/enrich_documents.py`:

```python
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

        # Deliberately does NOT clear stale_marked_at: reindex is the last
        # stage and the only one that clears the work ticket (spec 5.7).
        self.stdout.write("Done. Run `manage.py reindex --stale` to publish.")
```

Create `enrich/management/__init__.py` and `enrich/management/commands/__init__.py` (both empty).

- [ ] **Step 5: Run the tests**

Run: `pytest tests/enrich/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P4 task 10: enrichment pipeline and enrich_documents command"
```

---

### Task 11: Cold pass, measurement, and the recorded outcome

**Files:**
- Create: `docs/superpowers/measurements/2026-08-p4-enrichment.md`
- Test: `tests/enrich/test_end_to_end.py`

**Interfaces:**
- Consumes: everything.
- Produces: a measurements file P5 and P7 read; a recorded `needs_review` rate.

Do not skip this task. P5's response schemas are shaped by the `attrs` and `card` shapes this pass actually produces, and P7's `SpecKey` seeding depends on the `key_raw` frequency distribution it generates.

- [ ] **Step 1: Write the end-to-end test**

`tests/enrich/test_end_to_end.py`:

```python
import pytest
from django.core.management import call_command

from enrich.models import EnrichedRecord
from search.models import SearchDocument


@pytest.mark.django_db
def test_enrich_then_reindex_puts_a_typed_card_on_the_document(monkeypatch):
    """The full chain: adapter -> enrich -> overlay -> SearchDocument.card.
    This is the test that would catch a break anywhere in the seam between the
    two apps, which is the seam nothing else covers."""
    from gazette.models import Iulaan, IulaanType, Office
    t = IulaanType.objects.create(name="ވަޒީފާގެ ފުރުޞަތު")
    o = Office.objects.create(name="Ministry of Example")
    Iulaan.objects.create(
        id="IUL-1", title="Administrative Officer", office=o, iulaan_type=t,
        additional_info={}, attachments=[],
        body="Basic salary: 10,750 per month. Attendance allowance 4,400. "
             "Deadline 2026-08-31. Call 3323838.",
    )

    async def _fake_chain(self, messages, rebuild=None):
        return {
            "doc_type": "job", "doc_type_confidence": 0.95,
            "canonical_title_en": "Administrative Officer",
            "summary_en": "A GS3 post at the Ministry of Example.",
            "attrs": {
                "role": "Administrative Officer",
                "compensation": {
                    "basic_salary": 10750, "salary_state": "listed",
                    "pension_applies": True,
                    "allowances": [{"kind": "attendance",
                                    "label_raw": "Attendance allowance",
                                    "amount": 4400, "basis": "fixed_monthly"}],
                },
                "deadline": "2026-08-31",
            },
        }, "stub"

    monkeypatch.setattr("enrich.client.EnrichClient.run_chain", _fake_chain)

    call_command("reindex", "--source", "gazette")
    call_command("enrich_documents", "--source", "gazette", "--type", "job")
    call_command("reindex", "--source", "gazette")

    rec = EnrichedRecord.objects.get()
    assert rec.status == "ok"

    doc = SearchDocument.objects.get()
    assert doc.doc_type == "job"
    assert doc.title_en == "Administrative Officer"
    assert doc.card["salary_display"] == "MVR 10,750 / month"
    assert doc.card["net_estimate"]["value"] == pytest.approx(14397.50)
    assert doc.card["deadline"] == "2026-08-31"
    assert "deadline_state" not in doc.card
```

Run: `pytest tests/enrich/test_end_to_end.py -v` — expected PASS once tasks 1-10 are in.

- [ ] **Step 2: Dry-run the whole corpus and record the counts**

```bash
python manage.py enrich_documents --source gazette --cold-pass --dry-run
python manage.py enrich_documents --source ibay --cold-pass --dry-run
```

Write the per-type counts into the measurements file. If gazette's job count is far from ~306 or iBay's from ~335, the P1 adapters are classifying differently than section 5.3 predicts and that is worth understanding before spending money.

- [ ] **Step 3: Run the jobs pass off-peak, on a sample first**

Peak-hour DeepSeek rates are double. Schedule outside 01:00-04:00 and 06:00-10:00 UTC.

```bash
python manage.py enrich_documents --source gazette --type job --limit 25
```

Then read 10 of the 25 records by hand against their source iulaan. This is the only calibration step in P4 and there is no automated substitute for it — the grounding validator proves a value came from the source, not that it was put in the right field.

Record in the measurements file: how many of 25 came back `ok` / `needs_review` / `failed`, how many had a `basic_salary`, how many had at least one allowance, and how many had a wrong-field error a human could see.

- [ ] **Step 4: Run the full jobs and news pass**

```bash
python manage.py enrich_documents --source gazette --type job
python manage.py enrich_documents --source ibay --type job
python manage.py enrich_documents --source gazette --type news
python manage.py reindex --source gazette
python manage.py reindex --source ibay
```

- [ ] **Step 5: Property, then shopping**

```bash
python manage.py enrich_documents --source ibay --type property
python manage.py reindex --source ibay
python manage.py enrich_documents --source ibay --type shopping
python manage.py reindex --source ibay
```

Shopping is ~16,300 documents and the bulk of the ~$5.20 corpus cost. Run it last and off-peak.

- [ ] **Step 6: Record the outcome**

`docs/superpowers/measurements/2026-08-p4-enrichment.md`:

```markdown
# P4 enrichment, measured

Date: <fill>
Provider / model: <fill>
PROMPT_VERSION: 1

## Volume and cost

| Slice | Documents | ok | needs_review | failed | Wall clock | Cost |
|---|---|---|---|---|---|---|
| gazette job | | | | | | |
| ibay job | | | | | | |
| gazette news | | | | | | |
| ibay property | | | | | | |
| ibay shopping | | | | | | |

Spec 5.1 predicted ~$5.20 for the full 20,751-document corpus. Actual: <fill>.

## Manual calibration, 10 gazette jobs read by hand

| Iulaan | doc_type right | role right | salary right | allowances right | notes |
|---|---|---|---|---|---|

## Drop reasons, by frequency

Run:

    SELECT d->>'reason' AS reason, count(*)
    FROM enrich_enrichedrecord,
         LATERAL jsonb_array_elements(validation->'dropped') d
    GROUP BY 1 ORDER BY 2 DESC;

| reason | count | what it means |
|---|---|---|

A high `not_grounded` count on `employer` means the pre-extractor is not
offering the model enough context, not that the model is hallucinating.

## Attribute coverage, for P5 and P7

    SELECT doc_type, count(*) FILTER (WHERE attrs->'compensation'->>'basic_salary'
           IS NOT NULL) AS with_salary, count(*)
    FROM enrich_enrichedrecord GROUP BY 1;

| doc_type | documents | with a usable typed attribute |
|---|---|---|

## key_raw frequency, the input to P7's SpecKey seeding

    SELECT s->>'key_raw' AS k, count(*)
    FROM enrich_enrichedrecord,
         LATERAL jsonb_array_elements(attrs->'specs') s
    WHERE doc_type = 'shopping'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 50;

| key_raw | count |
|---|---|

## Decisions this changes

- [ ] Is the needs_review rate low enough that the admin queue is workable?
- [ ] Did any drop reason dominate in a way that means fixing preextract.py?
- [ ] Do the top 20 key_raw values look like real facets? (P7 seeds from these.)
```

- [ ] **Step 7: Commit**

```bash
jj commit -m "P4 task 11: cold pass, measurements recorded"
```

---

## Self-Review

**Spec coverage.** 4.2 → task 1. 4.3, 4.3.1, 4.3.2 → tasks 2, 3. 4.3.3 → covered in P1, referenced in task 8's icon-path test. 4.4 → `Spec` in task 2 and `key_raw` capture in task 11; the `SpecKey`/`DocumentSpec` tables themselves are P7 by design. 5.1 → task 6. 5.2 layers 0-5 → tasks 4 (0), 6 (1, 2), 7 (3, 4, 5). 5.3 → task 5. 5.4 → task 10. 5.5 → query-side translation is P2; the background title job is P8. 5.7 → task 10's `select_keys` gates and task 9's stale-clearing test. 8.1-8.5 → task 8. 3.3 → task 9.

**Known gaps, deliberate.** `DocumentSpec` rows are not written in P4 — `ShoppingAttrs.specs` is captured into `attrs` JSONB and P7 projects it into the relational table when the facet substrate exists. Writing rows into a table nothing reads would be speculative. `DocumentReport` is P5, since it needs the API endpoint to be worth having.

**Type consistency checked.** `estimate_net` returns `NetEstimate | None` and every caller in task 8 handles the `None`. `ground` returns `(model, dict)` and `enrich_one` unpacks both. `build_card(doc_type, attrs_model, *, base)` — the keyword-only `base` is used identically in task 8's tests and task 9's overlay. `select_keys` yields `(source, source_key)` tuples and `run_pass` destructures them as `(s, k)`.

**The one thing to watch during implementation.** Task 7's `_walk` prunes by leaf field name, which means adding a numeric field to a schema without adding it to the exemption set in `_walk` will silently start dropping it. When task 2's schemas change, re-read `_UNGROUNDED_STRING_FIELDS` and the `doc_type_confidence`/`pension_rate` exemption list in the same edit.
