# P7 Dynamic Shopping Facets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filters that change with the query — voltage, amperage and wattage ranges for "power supply"; brand, storage and condition checkboxes for "iphone" — from the same code path over different data.

**Architecture:** An open attribute space in a relational side table (`DocumentSpec`), a curated registry that decides what may become a filter (`SpecKey`), and a discovery pass that aggregates the side table over the candidate set from P5 and scores the survivors. Extraction is open — a deterministic unit-pattern regex plus the model may produce any `key_raw` — but faceting is curated, which is what stops the attribute space degenerating into thousands of junk facets while still letting new product categories arrive with no schema change.

**Tech Stack:** Django 6.0.5, PostgreSQL 18, pytest + pytest-django. No frontend work: P6's `FacetPanel` already renders whatever ordered list the API returns.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 4.4, 8.3, 9, 12.

**Depends on:** P4 (`ShoppingAttrs.specs`, the `key_raw` frequency table in the P4 measurements file), P5 (the candidate CTE, `FacetOut`, `filter_sql`).

---

## Global Constraints

- **Extraction is open; faceting is curated.** Anything may become a `DocumentSpec` row. Only a `SpecKey` with `is_facetable = True` becomes a filter. Spec 4.4.
- **The registry is category-scoped, not global.** `Type` means `Guest House` for property, `LED` for televisions, `Laptop/Notebook` for computers and `Action and Adventure` for video games — one key, four unrelated vocabularies. Spec 4.4.
- **`value_aliases` is required, not optional.** `Apple (iPhone)` (999 rows) and `Apple` (111) are the same brand and must collapse into one checkbox, or the most common filter in the corpus is wrong. Spec 4.4.
- **The deterministic extractor runs before the model.** A regex over `<number><unit>` against the `SpecKey` unit vocabulary cannot hallucinate a voltage; the model's job is only to assign semantic keys to numbers already found. Spec 4.4.
- **Discard any key whose values are effectively constant** across the result set. A filter that cannot partition the results is dead UI, and this is the check most implementations skip. Spec 8.3.
- **At most 8 facets plus a "more filters" group.** Spec 8.3.
- **Universal shopping facets are always available** and are not subject to the discovery thresholds. Spec 8.3.
- **FKs to `SearchDocument` need `db_constraint=False`.** It is partitioned. Spec 12.2.
- **No silent truncation.** When discovery drops a key, the reason is available in the admin, not swallowed.
- Version control is **jj**, not git.

---

## File Structure

```
search/
  models.py                      MODIFIED: SpecKey, DocumentSpec
  migrations/000X_specs.py
  specs/
    __init__.py
    extract.py                   unit-pattern extractor over the SpecKey vocabulary
    normalize.py                 alias collapsing, multi-value splitting
    project.py                   attrs + ProductInfo -> DocumentSpec rows
    discovery.py                 the six-step scoring pass
  facets.py                      MODIFIED: merge dynamic facets into the ordered list
  filters.py                     MODIFIED: spec-backed filter keys
  query.py                       MODIFIED: call discovery for shopping
  admin.py                       MODIFIED: the promotion queue
  management/commands/
    sync_specs.py
    seed_spec_keys.py

enrich/preextract.py             MODIFIED: unit vocabulary comes from SpecKey
tests/search/specs/...
```

---

### Task 1: `SpecKey` and `DocumentSpec`

**Files:**
- Modify: `search/models.py`, `search/admin.py`
- Create: `search/migrations/000X_specs.py`, `search/specs/__init__.py`
- Test: `tests/search/specs/test_models.py`

**Interfaces:**
- Produces: `SpecKey`, `DocumentSpec`, `SpecKey.resolve_value(raw) -> str`, `SpecKey.matches_unit(u) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/search/specs/__init__.py` — empty.

`tests/search/specs/test_models.py`:

```python
import pytest
from django.db import IntegrityError

from search.models import DocumentSpec, SearchDocument, SpecKey


@pytest.mark.django_db
def test_speckey_is_unique_by_key():
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V")
    with pytest.raises(IntegrityError):
        SpecKey.objects.create(key="voltage", label_en="Volts", datatype="numeric")


@pytest.mark.django_db
def test_a_new_key_is_not_facetable_until_promoted():
    """Spec 4.4: extraction is open, faceting is curated. A key arrives
    invisible and a human makes it a filter."""
    k = SpecKey.objects.create(key="colour", label_en="Colour", datatype="enum")
    assert k.is_facetable is False


@pytest.mark.django_db
def test_value_aliases_collapse_variants():
    """'Apple (iPhone)' appears 999 times and 'Apple' 111. They are the same
    brand and must be one checkbox, or the most common filter in the corpus is
    wrong."""
    k = SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                               value_aliases={"Apple (iPhone)": "Apple",
                                              "APPLE": "Apple"})
    assert k.resolve_value("Apple (iPhone)") == "Apple"
    assert k.resolve_value("APPLE") == "Apple"
    assert k.resolve_value("Nokia") == "Nokia"
    assert k.resolve_value("  Apple (iPhone)  ") == "Apple"


@pytest.mark.django_db
def test_unit_aliases_match_case_insensitively():
    k = SpecKey.objects.create(key="voltage", label_en="Voltage",
                               datatype="numeric", unit="V",
                               unit_aliases=["volt", "volts", "v"])
    assert k.matches_unit("V") and k.matches_unit("volts") and k.matches_unit("VOLT")
    assert not k.matches_unit("A")


@pytest.mark.django_db
def test_categories_scope_a_key_to_where_it_means_something():
    """'Type' means Guest House for property, LED for televisions and
    Laptop/Notebook for computers. One key, four vocabularies -- so the
    registry is category-scoped, not global."""
    k = SpecKey.objects.create(key="type", label_en="Type", datatype="enum",
                               categories=["Televisions", "Computers"])
    assert "Televisions" in k.categories
    assert "Housing & Real Estate" not in k.categories


@pytest.mark.django_db
def test_documentspec_stores_numeric_and_text_values_separately():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="voltage",
                                value_num=24, unit="V")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="brand",
                                value_text="Apple")
    assert DocumentSpec.objects.filter(document_id=doc.id).count() == 2


@pytest.mark.django_db
def test_documentspec_survives_a_document_that_no_longer_exists():
    """SearchDocument is partitioned, so the FK carries db_constraint=False.
    A dangling spec row must be inert, not a 500."""
    DocumentSpec.objects.create(document_id=999999, key_raw="voltage",
                                value_num=24, unit="V")
    assert DocumentSpec.objects.count() == 1


@pytest.mark.django_db
def test_a_spec_row_is_unique_per_document_key_and_value():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="voltage",
                                value_num=24, unit="V")
    with pytest.raises(IntegrityError):
        DocumentSpec.objects.create(document_id=doc.id, key_raw="voltage",
                                    value_num=24, unit="V")
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/specs/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'SpecKey'`.

- [ ] **Step 3: Write the models**

Append to `search/models.py`:

```python
class SpecKey(models.Model):
    """The curated facet registry. Spec 4.4.

    Extraction is open -- the unit extractor and the LLM may produce any
    key_raw -- but only a key promoted here with is_facetable=True becomes a
    filter. Everything else is stored, shown on the detail page, and queued for
    one-click promotion. That asymmetry is what keeps the attribute space from
    degenerating into thousands of junk facets while still letting a new
    product category arrive without a schema change.
    """

    DATATYPES = [("numeric", "numeric"), ("enum", "enum"), ("bool", "bool")]
    WIDGETS = [("range", "range"), ("checkbox", "checkbox"), ("toggle", "toggle")]

    key = models.CharField(max_length=64, unique=True)
    label_en = models.CharField(max_length=64)
    label_dv = models.CharField(max_length=64, blank=True)
    datatype = models.CharField(max_length=16, choices=DATATYPES)
    unit = models.CharField(max_length=16, blank=True)
    unit_aliases = models.JSONField(default=list, blank=True)
    value_aliases = models.JSONField(default=dict, blank=True)
    widget = models.CharField(max_length=16, choices=WIDGETS, default="checkbox")
    # Leaf categories where this key is meaningful. Empty means "anywhere",
    # which is right for `brand` and wrong for `Type`.
    categories = models.JSONField(default=list, blank=True)
    priority = models.IntegerField(default=100)
    is_facetable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "key"]

    def __str__(self):
        return self.key

    def resolve_value(self, raw: str) -> str:
        v = (raw or "").strip()
        return self.value_aliases.get(v, v)

    def matches_unit(self, u: str) -> bool:
        u = (u or "").strip().lower()
        if not u:
            return False
        if u == (self.unit or "").lower():
            return True
        return u in {a.lower() for a in self.unit_aliases}


class DocumentSpec(models.Model):
    """One row per extracted attribute. Spec 4.4.

    Relational rather than JSONB because facet discovery is an aggregation
    over the candidate set, and GROUP BY on indexed columns beats unnesting a
    JSONB array on every request. Volume is small: ~20,000 products times ~4
    specs, under 100,000 rows.
    """

    # SearchDocument is LIST-partitioned, so a real FK constraint is not
    # available (spec 12.2). A dangling row is inert; sync_specs prunes them.
    document = models.ForeignKey("search.SearchDocument", on_delete=models.DO_NOTHING,
                                 db_constraint=False, related_name="specs")
    key = models.ForeignKey(SpecKey, null=True, blank=True,
                            on_delete=models.SET_NULL, related_name="values")
    key_raw = models.CharField(max_length=64)
    value_num = models.FloatField(null=True, blank=True)
    value_text = models.CharField(max_length=128, blank=True)
    unit = models.CharField(max_length=16, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "key_raw", "value_num", "value_text"],
                name="uniq_documentspec_value",
            )
        ]
        indexes = [
            models.Index(fields=["document"], name="docspec_document"),
            models.Index(fields=["key", "value_text"], name="docspec_key_text"),
            models.Index(fields=["key", "value_num"], name="docspec_key_num"),
            models.Index(fields=["key_raw"], name="docspec_key_raw"),
        ]

    def __str__(self):
        return f"{self.key_raw}={self.value_num or self.value_text}{self.unit}"
```

`UniqueConstraint` over nullable columns: Postgres treats NULLs as distinct, so `(doc, 'voltage', 24, '')` and a second identical row would both insert. Add `nulls_distinct=False` (Django 5.0+, Postgres 15+) to the constraint; if the Django version rejects it, coalesce in the projection instead by writing `value_num=None` as `value_text=''` and never both.

Run: `python manage.py makemigrations search && python manage.py migrate`

- [ ] **Step 4: Run the tests**

Run: `pytest tests/search/specs/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P7 task 1: SpecKey and DocumentSpec"
```

---

### Task 2: The unit-pattern extractor and value normalization

**Files:**
- Create: `search/specs/extract.py`, `search/specs/normalize.py`
- Modify: `enrich/preextract.py`
- Test: `tests/search/specs/test_extract.py`, `tests/search/specs/test_normalize.py`

**Interfaces:**
- Produces: `unit_vocabulary() -> list[str]`, `extract_units(text) -> list[dict]`, `normalize_value(key, raw) -> list[str]`, `split_multivalue` (re-exported from `enrich.preextract`), `parse_bool(raw) -> bool | None`.

The real listing `KICO METAL POWER SUPPLY 24V-5A-120W / 7884445` carries its entire spec sheet as a compact title string. The regex is cheaper than the model and it cannot invent a voltage.

- [ ] **Step 1: Write the failing test**

`tests/search/specs/test_extract.py`:

```python
import pytest

from search.models import SpecKey
from search.specs.extract import extract_units, unit_vocabulary


@pytest.fixture
def keys(db):
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V", unit_aliases=["volt", "volts", "v"],
                           widget="range", is_facetable=True)
    SpecKey.objects.create(key="current", label_en="Current", datatype="numeric",
                           unit="A", unit_aliases=["amp", "amps", "a"],
                           widget="range", is_facetable=True)
    SpecKey.objects.create(key="power", label_en="Power", datatype="numeric",
                           unit="W", unit_aliases=["watt", "watts", "w"],
                           widget="range", is_facetable=True)
    SpecKey.objects.create(key="storage_gb", label_en="Storage", datatype="numeric",
                           unit="GB", unit_aliases=["gb", "gigabyte"],
                           widget="range", is_facetable=True)


@pytest.mark.django_db
def test_the_vocabulary_comes_from_the_registry(keys):
    """P4 hardcoded a list; P7 replaces it with the curated one so adding a
    unit is an admin row, not a deploy."""
    vocab = unit_vocabulary()
    assert "V" in vocab and "GB" in vocab
    assert vocab == sorted(vocab, key=len, reverse=True), (
        "longest-first, or 'A' shadows 'mAh'"
    )


@pytest.mark.django_db
def test_a_compact_spec_title(keys):
    got = extract_units("KICO METAL POWER SUPPLY 24V-5A-120W / 7884445")
    assert {(u["key"], u["value"]) for u in got} == {
        ("voltage", 24.0), ("current", 5.0), ("power", 120.0)
    }


@pytest.mark.django_db
def test_the_trailing_phone_number_is_not_a_spec(keys):
    got = extract_units("POWER SUPPLY 24V / 7884445")
    assert 7884445.0 not in [u["value"] for u in got]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text,key,value",
    [
        ("128GB storage", "storage_gb", 128.0),
        ("128 GB", "storage_gb", 128.0),
        ("24 volts DC", "voltage", 24.0),
        ("5 amps", "current", 5.0),
        ("1.5W", "power", 1.5),
    ],
)
def test_alias_and_spacing_variants(keys, text, key, value):
    got = extract_units(text)
    assert (key, value) in {(u["key"], u["value"]) for u in got}


@pytest.mark.django_db
def test_a_unit_with_no_registered_key_is_still_captured_as_key_raw(keys):
    """Extraction is open: an unregistered unit becomes a key_raw row so it can
    surface in the promotion queue. It just is not facetable."""
    got = extract_units("5000mAh battery")
    assert got and got[0]["key"] is None
    assert got[0]["key_raw"] == "mah"


@pytest.mark.django_db
def test_a_bare_year_is_not_a_spec(keys):
    assert extract_units("Model year 2019") == []


@pytest.mark.django_db
def test_extraction_is_deduplicated(keys):
    got = extract_units("24V power supply, 24V input")
    assert len([u for u in got if u["key"] == "voltage"]) == 1


@pytest.mark.django_db
def test_no_registry_rows_yields_no_units_rather_than_crashing(db):
    assert extract_units("24V-5A") == []
```

`tests/search/specs/test_normalize.py`:

```python
import pytest

from search.models import SpecKey
from search.specs.normalize import normalize_value, parse_bool


@pytest.fixture
def brand(db):
    return SpecKey.objects.create(
        key="brand", label_en="Brand", datatype="enum",
        value_aliases={"Apple (iPhone)": "Apple", "SAMSUNG": "Samsung"},
    )


@pytest.fixture
def facilities(db):
    return SpecKey.objects.create(key="room_facilities", label_en="Facilities",
                                  datatype="enum")


@pytest.mark.django_db
def test_aliases_collapse(brand):
    assert normalize_value(brand, "Apple (iPhone)") == ["Apple"]
    assert normalize_value(brand, "SAMSUNG") == ["Samsung"]


@pytest.mark.django_db
def test_a_multi_value_string_becomes_independent_values(facilities):
    """'Air Conditioning, Fans, Towels' appears 1,137 times and must become
    three checkboxes, not one. Spec 4.4."""
    assert normalize_value(facilities, "Air Conditioning, Fans, Towels") == [
        "Air Conditioning", "Fans", "Towels"
    ]


@pytest.mark.django_db
def test_aliases_apply_after_splitting(brand):
    assert normalize_value(brand, "Apple (iPhone), Nokia") == ["Apple", "Nokia"]


@pytest.mark.django_db
def test_empty_and_whitespace_yield_nothing(brand):
    assert normalize_value(brand, "") == []
    assert normalize_value(brand, "   ") == []


@pytest.mark.django_db
def test_a_value_too_long_for_the_column_is_dropped_not_truncated(brand):
    """A truncated brand is a wrong brand."""
    assert normalize_value(brand, "x" * 500) == []


@pytest.mark.parametrize(
    "raw,expected",
    [("Yes", True), ("yes", True), ("true", True), ("1", True), ("Available", True),
     ("No", False), ("false", False), ("0", False), ("None", False),
     ("maybe", None), ("", None)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/specs/ -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the extractor**

`search/specs/extract.py`:

```python
"""The deterministic unit-pattern extractor. Spec 4.4.

Numeric specs often live in the title, not in a field: the real listing
`KICO METAL POWER SUPPLY 24V-5A-120W / 7884445` carries its entire spec sheet
as a compact string. So this runs over title and description before the model
does, and the model's job is only to assign semantic keys to what it missed.
Cheaper, and it cannot hallucinate a voltage.

The vocabulary comes from SpecKey, so adding a unit is an admin row rather than
a deploy. That is the P4-to-P7 change: enrich/preextract.py's UNIT_VOCAB
constant is replaced by unit_vocabulary().
"""

from __future__ import annotations

import re

from search.models import SpecKey

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Units not tied to a registered key. Captured as key_raw so they surface in
# the promotion queue instead of being silently discarded.
_UNREGISTERED = ["mAh", "kWh", "GHz", "MHz", "sqft", "inch", "kg", "ml", "cm", "mm"]


def unit_vocabulary() -> list[str]:
    """Every unit token, longest first so 'mAh' wins over 'A'."""
    tokens: set[str] = set(_UNREGISTERED)
    for k in SpecKey.objects.exclude(unit="").only("unit", "unit_aliases"):
        tokens.add(k.unit)
        tokens.update(a for a in k.unit_aliases if a)
    return sorted(tokens, key=lambda t: (-len(t), t.lower()))


def _key_index() -> dict[str, SpecKey]:
    """unit token (lowercased) -> the SpecKey that owns it."""
    index: dict[str, SpecKey] = {}
    for k in SpecKey.objects.exclude(unit="").only(
        "id", "key", "unit", "unit_aliases", "datatype"
    ):
        index.setdefault(k.unit.lower(), k)
        for a in k.unit_aliases:
            index.setdefault(a.lower(), k)
    return index


def _pattern(vocab: list[str]) -> re.Pattern:
    alt = "|".join(re.escape(u) for u in vocab)
    # Leading guard rejects '...445GB' inside a longer digit run; trailing
    # guard rejects 'Vodafone' matching the 'V' unit.
    return re.compile(rf"(?<![A-Za-z\d])({_NUM})\s*({alt})(?![A-Za-z])", re.I)


def extract_units(text: str) -> list[dict]:
    """Returns [{key, key_raw, value, unit}] with key None when unregistered."""
    if not text:
        return []
    vocab = unit_vocabulary()
    if not vocab:
        return []

    index = _key_index()
    seen: set[tuple[str, float]] = set()
    out: list[dict] = []

    for m in _pattern(vocab).finditer(text):
        raw_num, raw_unit = m.group(1), m.group(2)
        value = float(raw_num.replace(",", ""))
        spec_key = index.get(raw_unit.lower())
        key_name = spec_key.key if spec_key else raw_unit.lower()

        if (key_name, value) in seen:
            continue
        seen.add((key_name, value))

        out.append({
            "key": spec_key.key if spec_key else None,
            "key_id": spec_key.id if spec_key else None,
            "key_raw": key_name,
            "value": value,
            "unit": spec_key.unit if spec_key else raw_unit,
        })
    return out
```

- [ ] **Step 4: Write the normalizer**

`search/specs/normalize.py`:

```python
"""Value normalization. Spec 4.4."""

from __future__ import annotations

from enrich.preextract import split_multivalue
from search.models import SpecKey

MAX_VALUE_LEN = 128

_TRUE = {"yes", "true", "1", "available", "included", "有"}
_FALSE = {"no", "false", "0", "none", "not available", "n/a"}


def normalize_value(key: SpecKey, raw: str) -> list[str]:
    """Split, alias-collapse, and drop anything unusable.

    Splitting first and aliasing second matters: 'Apple (iPhone), Nokia' must
    become two values, both of which then pass through the alias table.
    """
    if not raw or not raw.strip():
        return []

    parts = split_multivalue(raw) or [raw.strip()]
    out: list[str] = []
    for p in parts:
        v = key.resolve_value(p)
        if not v or len(v) > MAX_VALUE_LEN:
            # Truncating a brand produces a wrong brand, so drop it instead.
            continue
        if v not in out:
            out.append(v)
    return out


def parse_bool(raw: str) -> bool | None:
    v = (raw or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None
```

- [ ] **Step 5: Point `enrich/preextract.py` at the registry**

Replace the hardcoded `UNIT_VOCAB` list with a lazy call, keeping the old list as the fallback when the registry is empty (which it is in a fresh database, and during P4's own tests):

```python
_FALLBACK_UNIT_VOCAB = [
    "kWh", "mAh", "GHz", "MHz", "sqft", "inch", "kW", "GB", "TB", "MB",
    "kg", "ml", "cm", "mm", "V", "A", "W", "L", '"',
]


def unit_vocab() -> list[str]:
    """P7 moved this into the SpecKey registry so adding a unit is an admin
    row (spec 4.4). Falls back to the fixed list when the registry is empty."""
    try:
        from search.specs.extract import unit_vocabulary
        vocab = unit_vocabulary()
    except Exception:
        vocab = []
    return vocab or _FALLBACK_UNIT_VOCAB
```

and rebuild `_UNIT` per call rather than at import. Cache it on the `SpecKey` row count so it is not rebuilt per document:

```python
_UNIT_CACHE: tuple[int, re.Pattern] | None = None


def _unit_pattern() -> re.Pattern:
    global _UNIT_CACHE
    vocab = unit_vocab()
    fingerprint = hash(tuple(vocab))
    if _UNIT_CACHE is None or _UNIT_CACHE[0] != fingerprint:
        alt = "|".join(re.escape(u) for u in vocab)
        _UNIT_CACHE = (fingerprint, re.compile(
            rf"(?<![A-Za-z\d])({_NUM})\s*({alt})(?![A-Za-z])"))
    return _UNIT_CACHE[1]
```

The P4 tests must still pass unchanged — that is the check that the fallback works.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/search/specs/ tests/enrich/test_preextract.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P7 task 2: registry-driven unit extractor and value normalization"
```

---

### Task 3: Projection into `DocumentSpec`

**Files:**
- Create: `search/specs/project.py`, `search/management/commands/sync_specs.py`
- Test: `tests/search/specs/test_project.py`, `tests/search/specs/test_sync_command.py`

**Interfaces:**
- Produces: `specs_for_document(doc) -> list[dict]`, `sync_document_specs(doc) -> int`, `sync_specs(*, source=None, doc_type='shopping', limit=None, batch_size=500) -> dict`.

Three sources feed one table: the unit extractor over title and summary, `attrs['specs']` written by P4's enrichment, and `attrs['specs_raw']` — the iBay `ProductInfo` rows the adapter already carried through. The third is the largest by volume and the cheapest, because it is near-schema data the source gave us for free.

- [ ] **Step 1: Write the failing test**

`tests/search/specs/test_project.py`:

```python
import pytest

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.project import specs_for_document, sync_document_specs


@pytest.fixture
def registry(db):
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V", unit_aliases=["v"], widget="range",
                           is_facetable=True)
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           value_aliases={"Apple (iPhone)": "Apple"},
                           widget="checkbox", is_facetable=True)
    SpecKey.objects.create(key="room_facilities", label_en="Facilities",
                           datatype="enum", widget="checkbox")


def _doc(**kw):
    base = dict(source="ibay", source_key="1", doc_type="shopping",
                url="https://x", attrs={}, card={})
    base.update(kw)
    return SearchDocument.objects.create(**base)


@pytest.mark.django_db
def test_units_are_extracted_from_the_title(registry):
    doc = _doc(title_en="KICO METAL POWER SUPPLY 24V-5A-120W / 7884445")
    rows = specs_for_document(doc)
    assert any(r["key_raw"] == "voltage" and r["value_num"] == 24 for r in rows)


@pytest.mark.django_db
def test_enrichment_specs_are_projected(registry):
    doc = _doc(attrs={"specs": [{"key_raw": "brand", "value_text": "Apple (iPhone)"}]})
    rows = specs_for_document(doc)
    # The alias collapses at projection time, so the facet has one value.
    assert any(r["value_text"] == "Apple" for r in rows)


@pytest.mark.django_db
def test_scraped_productinfo_is_projected(registry):
    """The largest and cheapest source: ProductInfo already supplies
    near-schema data for thousands of listings (spec 4.4)."""
    doc = _doc(attrs={"specs_raw": {"Brand": "Apple (iPhone)",
                                    "Item Condition": "Used"}})
    rows = specs_for_document(doc)
    values = {(r["key_raw"], r["value_text"]) for r in rows}
    assert ("brand", "Apple") in values
    assert ("item_condition", "Used") in values


@pytest.mark.django_db
def test_a_multi_value_productinfo_field_becomes_several_rows(registry):
    doc = _doc(attrs={"specs_raw": {"Room Facilities": "Air Conditioning, Fans, Towels"}})
    rows = [r for r in specs_for_document(doc) if r["key_raw"] == "room_facilities"]
    assert len(rows) == 3


@pytest.mark.django_db
def test_a_registered_key_is_linked_and_an_unregistered_one_is_not(registry):
    doc = _doc(attrs={"specs_raw": {"Brand": "Nokia", "Warranty": "1 year"}})
    rows = {r["key_raw"]: r for r in specs_for_document(doc)}
    assert rows["brand"]["key_id"] is not None
    assert rows["warranty"]["key_id"] is None


@pytest.mark.django_db
def test_sync_is_idempotent(registry):
    doc = _doc(title_en="24V power supply",
               attrs={"specs_raw": {"Brand": "Apple (iPhone)"}})
    sync_document_specs(doc)
    first = DocumentSpec.objects.filter(document_id=doc.id).count()
    sync_document_specs(doc)
    assert DocumentSpec.objects.filter(document_id=doc.id).count() == first


@pytest.mark.django_db
def test_sync_removes_specs_that_no_longer_apply(registry):
    doc = _doc(attrs={"specs_raw": {"Brand": "Nokia"}})
    sync_document_specs(doc)
    doc.attrs = {"specs_raw": {"Brand": "Samsung"}}
    doc.save()
    sync_document_specs(doc)
    values = list(DocumentSpec.objects.filter(document_id=doc.id)
                  .values_list("value_text", flat=True))
    assert values == ["Samsung"]


@pytest.mark.django_db
def test_a_document_with_no_specs_produces_no_rows(registry):
    assert specs_for_document(_doc(title_en="A thing")) == []
```

`tests/search/specs/test_sync_command.py`:

```python
import pytest
from django.core.management import call_command

from search.models import DocumentSpec, SearchDocument, SpecKey


@pytest.mark.django_db
def test_sync_specs_streams_a_whole_source(capsys):
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True)
    for i in range(5):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      attrs={"specs_raw": {"Brand": "Nokia"}})
    call_command("sync_specs", "--source", "ibay")
    assert DocumentSpec.objects.count() == 5


@pytest.mark.django_db
def test_sync_specs_prunes_rows_for_deleted_documents():
    DocumentSpec.objects.create(document_id=999999, key_raw="brand",
                                value_text="Ghost")
    call_command("sync_specs", "--prune")
    assert DocumentSpec.objects.count() == 0


@pytest.mark.django_db
def test_limit_is_respected(capsys):
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum")
    for i in range(10):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      attrs={"specs_raw": {"Brand": "Nokia"}})
    call_command("sync_specs", "--source", "ibay", "--limit", "3")
    assert DocumentSpec.objects.values("document_id").distinct().count() == 3
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/specs/test_project.py -v`
Expected: FAIL.

- [ ] **Step 3: Write the projector**

`search/specs/project.py`:

```python
"""Projection into the DocumentSpec side table. Spec 4.4.

Three inputs, one table:

  1. the unit extractor over title and summary  -- catches '24V-5A-120W'
  2. attrs['specs']      -- what P4's enrichment assigned semantic keys to
  3. attrs['specs_raw']  -- iBay ProductInfo, already near-schema and free

Source 3 is the largest by volume, which is the strongest argument for the
whole typed-attribute design: `Item Condition` (7,098), `Type` (4,194),
`Brand` (2,313) and friends are structure the source gave us and that a
language model should not be paid to re-derive.
"""

from __future__ import annotations

import logging
import re

from django.db import transaction

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.extract import extract_units
from search.specs.normalize import normalize_value

logger = logging.getLogger(__name__)

_SLUG = re.compile(r"[^a-z0-9]+")

# Keys carried on SearchDocument columns or already faceted statically. Storing
# them again would double-count in discovery.
SKIP_KEYS = {"price", "location", "source", "doc_type"}


def slugify_key(raw: str) -> str:
    return _SLUG.sub("_", (raw or "").strip().lower()).strip("_")[:64]


def _registry() -> dict[str, SpecKey]:
    return {k.key: k for k in SpecKey.objects.all()}


def specs_for_document(doc: SearchDocument, registry=None) -> list[dict]:
    registry = registry if registry is not None else _registry()
    rows: list[dict] = []
    seen: set[tuple[str, float | None, str]] = set()

    def push(key_raw, *, value_num=None, value_text="", unit=""):
        key_raw = slugify_key(key_raw)
        if not key_raw or key_raw in SKIP_KEYS:
            return
        ident = (key_raw, value_num, value_text)
        if ident in seen:
            return
        seen.add(ident)
        spec_key = registry.get(key_raw)
        rows.append({
            "key_id": spec_key.id if spec_key else None,
            "key_raw": key_raw,
            "value_num": value_num,
            "value_text": value_text,
            "unit": unit,
        })

    # 1. the deterministic extractor over whatever text we have
    text = " ".join(t for t in (doc.title_en, doc.title_dv, doc.title_latin,
                                doc.summary_en) if t)
    for u in extract_units(text):
        push(u["key_raw"], value_num=u["value"], unit=u["unit"])

    # 2. enrichment output
    for s in (doc.attrs.get("specs") or []):
        if not isinstance(s, dict):
            continue
        key_raw = slugify_key(s.get("key_raw", ""))
        spec_key = registry.get(key_raw)
        if s.get("value_num") is not None:
            push(key_raw, value_num=float(s["value_num"]), unit=s.get("unit", ""))
        elif s.get("value_text"):
            values = (normalize_value(spec_key, s["value_text"]) if spec_key
                      else [s["value_text"][:128]])
            for v in values:
                push(key_raw, value_text=v)

    # 3. scraped ProductInfo
    for raw_key, raw_value in (doc.attrs.get("specs_raw") or {}).items():
        key_raw = slugify_key(raw_key)
        spec_key = registry.get(key_raw)
        values = (normalize_value(spec_key, str(raw_value)) if spec_key
                  else [v[:128] for v in _split_plain(str(raw_value))])
        for v in values:
            push(key_raw, value_text=v)

    return rows


def _split_plain(raw: str) -> list[str]:
    from enrich.preextract import split_multivalue
    return split_multivalue(raw) or ([raw.strip()] if raw.strip() else [])


def sync_document_specs(doc: SearchDocument, registry=None) -> int:
    """Replace this document's spec rows. Idempotent by construction."""
    rows = specs_for_document(doc, registry)
    with transaction.atomic():
        DocumentSpec.objects.filter(document_id=doc.id).delete()
        if rows:
            DocumentSpec.objects.bulk_create(
                [DocumentSpec(document_id=doc.id, **r) for r in rows],
                batch_size=500,
            )
    return len(rows)


def sync_specs(*, source=None, doc_type="shopping", limit=None,
               batch_size=500) -> dict:
    registry = _registry()
    qs = SearchDocument.objects.using("direct")
    if source:
        qs = qs.filter(source=source)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)

    counts = {"documents": 0, "specs": 0}
    for doc in qs.only(
        "id", "title_en", "title_dv", "title_latin", "summary_en", "attrs"
    ).iterator(chunk_size=batch_size):
        if limit is not None and counts["documents"] >= limit:
            break
        counts["specs"] += sync_document_specs(doc, registry)
        counts["documents"] += 1
    return counts


def prune_orphans() -> int:
    """Spec rows whose document no longer exists. The FK is db_constraint=False
    because SearchDocument is partitioned, so nothing cascades for us."""
    live = set(SearchDocument.objects.using("direct").values_list("id", flat=True))
    orphans = [
        s.id for s in DocumentSpec.objects.using("direct").only("id", "document_id")
        .iterator(chunk_size=1000) if s.document_id not in live
    ]
    DocumentSpec.objects.filter(id__in=orphans).delete()
    return len(orphans)
```

`search/management/commands/sync_specs.py`:

```python
from django.core.management.base import BaseCommand

from search.specs.project import prune_orphans, sync_specs


class Command(BaseCommand):
    help = "Project attrs and scraped ProductInfo into the DocumentSpec table."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--type", dest="doc_type", default="shopping")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--prune", action="store_true",
                            help="Also delete rows whose document is gone.")

    def handle(self, *args, **opts):
        if opts["prune"]:
            n = prune_orphans()
            self.stdout.write(f"pruned {n} orphan spec rows")
        counts = sync_specs(source=opts["source"], doc_type=opts["doc_type"],
                            limit=opts["limit"])
        self.stdout.write(self.style.SUCCESS(
            f"{counts['documents']} documents, {counts['specs']} spec rows"
        ))
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/search/specs/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P7 task 3: project attrs and ProductInfo into DocumentSpec"
```

---

### Task 4: Seed the registry from measured frequency

**Files:**
- Create: `search/management/commands/seed_spec_keys.py`, `search/specs/seed_data.py`
- Test: `tests/search/specs/test_seed.py`

**Interfaces:**
- Produces: `SEED_KEYS: list[dict]`, `seed_spec_keys(*, promote=False) -> dict`, `candidate_keys(limit=50) -> list[dict]`.

The seed list is not invented. It comes from the `ProductInfo` frequency table in spec 4.4 and the `key_raw` frequency query in `docs/superpowers/measurements/2026-08-p4-enrichment.md`. Read that file before writing this task; if it is missing, run the query in it first.

- [ ] **Step 1: Write the failing test**

`tests/search/specs/test_seed.py`:

```python
import pytest
from django.core.management import call_command

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.seed_data import SEED_KEYS
from search.specs.project import candidate_keys


def test_the_seed_list_covers_the_measured_top_productinfo_keys():
    """Spec 4.4 lists these with their corpus counts. If a key here is absent,
    the most common filters in the corpus are not facetable on day one."""
    keys = {k["key"] for k in SEED_KEYS}
    assert {"item_condition", "type", "neighborhood", "brand", "room_facilities",
            "lift", "floor", "furnishing", "bedrooms", "bathrooms",
            "ideal_tenants", "square_feet", "position_type", "job_category",
            "employer", "salary_range", "apply_before"} <= keys


def test_every_seed_entry_is_well_formed():
    for k in SEED_KEYS:
        assert k["datatype"] in {"numeric", "enum", "bool"}
        assert k["widget"] in {"range", "checkbox", "toggle"}
        assert k["label_en"]
        assert isinstance(k.get("categories", []), list)


def test_type_is_category_scoped_because_it_means_four_different_things():
    t = next(k for k in SEED_KEYS if k["key"] == "type")
    assert t["categories"], "an unscoped `Type` merges Guest House with LED"


def test_brand_carries_the_apple_alias():
    b = next(k for k in SEED_KEYS if k["key"] == "brand")
    assert b["value_aliases"].get("Apple (iPhone)") == "Apple"


@pytest.mark.django_db
def test_seeding_is_idempotent():
    call_command("seed_spec_keys")
    n = SpecKey.objects.count()
    call_command("seed_spec_keys")
    assert SpecKey.objects.count() == n


@pytest.mark.django_db
def test_seeding_does_not_overwrite_a_curated_row():
    """An admin who set is_facetable or priority by hand must not lose it on
    the next deploy."""
    call_command("seed_spec_keys")
    k = SpecKey.objects.get(key="brand")
    k.priority = 1
    k.is_facetable = False
    k.save()
    call_command("seed_spec_keys")
    k.refresh_from_db()
    assert k.priority == 1 and k.is_facetable is False


@pytest.mark.django_db
def test_candidate_keys_ranks_unpromoted_keys_by_frequency():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    doc2 = SearchDocument.objects.create(source="ibay", source_key="2",
                                         doc_type="shopping", url="https://x")
    for d in (doc, doc2):
        DocumentSpec.objects.create(document_id=d.id, key_raw="warranty",
                                    value_text="1 year")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="colour",
                                value_text="black")

    got = candidate_keys()
    assert got[0]["key_raw"] == "warranty"
    assert got[0]["documents"] == 2
    assert got[0]["distinct_values"] == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/specs/test_seed.py -v`
Expected: FAIL.

- [ ] **Step 3: Write the seed data**

`search/specs/seed_data.py`:

```python
"""Initial registry rows. Spec 4.4.

Not invented: every key below appears in the ProductInfo frequency table in
spec 4.4 with its corpus count, or in the key_raw frequency query recorded in
docs/superpowers/measurements/2026-08-p4-enrichment.md. Read that file before
adding to this list -- a key nobody's data contains is a filter nobody sees.
"""

SEED_KEYS = [
    # --- shopping, from ProductInfo ---
    {"key": "item_condition", "label_en": "Condition", "label_dv": "ޙާލަތު",
     "datatype": "enum", "widget": "checkbox", "priority": 10,
     "is_facetable": True, "value_aliases": {}, "categories": []},
    {"key": "brand", "label_en": "Brand", "label_dv": "ބްރޭންޑް",
     "datatype": "enum", "widget": "checkbox", "priority": 20,
     "is_facetable": True, "categories": [],
     # 999 rows say 'Apple (iPhone)' and 111 say 'Apple'. One checkbox.
     "value_aliases": {"Apple (iPhone)": "Apple", "APPLE": "Apple",
                       "Samsung (Galaxy)": "Samsung"}},
    {"key": "type", "label_en": "Type", "label_dv": "ބާވަތް",
     "datatype": "enum", "widget": "checkbox", "priority": 30,
     "is_facetable": True, "value_aliases": {},
     # Scoped, because `Type` means Guest House for property, LED for
     # televisions, Laptop/Notebook for computers and Action and Adventure for
     # video games. One key, four unrelated vocabularies.
     "categories": ["Televisions", "Computers", "Mobile Phones",
                    "Video Games", "Audio"]},

    # --- units, for the extractor ---
    {"key": "voltage", "label_en": "Voltage", "label_dv": "ވޯލްޓޭޖް",
     "datatype": "numeric", "unit": "V", "unit_aliases": ["v", "volt", "volts"],
     "widget": "range", "priority": 40, "is_facetable": True,
     "value_aliases": {}, "categories": []},
    {"key": "current", "label_en": "Current", "label_dv": "ކަރަންޓް",
     "datatype": "numeric", "unit": "A", "unit_aliases": ["a", "amp", "amps"],
     "widget": "range", "priority": 41, "is_facetable": True,
     "value_aliases": {}, "categories": []},
    {"key": "power", "label_en": "Power", "label_dv": "ބާރު",
     "datatype": "numeric", "unit": "W", "unit_aliases": ["w", "watt", "watts"],
     "widget": "range", "priority": 42, "is_facetable": True,
     "value_aliases": {}, "categories": []},
    {"key": "storage_gb", "label_en": "Storage", "label_dv": "ސްޓޯރޭޖް",
     "datatype": "numeric", "unit": "GB", "unit_aliases": ["gb", "gigabyte"],
     "widget": "range", "priority": 21, "is_facetable": True,
     "value_aliases": {}, "categories": ["Mobile Phones", "Computers"]},
    {"key": "screen_size", "label_en": "Screen size", "label_dv": "ސްކްރީން",
     "datatype": "numeric", "unit": "inch", "unit_aliases": ["inch", "inches", '"'],
     "widget": "range", "priority": 22, "is_facetable": True,
     "value_aliases": {}, "categories": ["Televisions", "Computers",
                                         "Mobile Phones"]},
    {"key": "battery_mah", "label_en": "Battery", "label_dv": "ބެޓެރީ",
     "datatype": "numeric", "unit": "mAh", "unit_aliases": ["mah"],
     "widget": "range", "priority": 23, "is_facetable": True,
     "value_aliases": {}, "categories": ["Mobile Phones"]},

    # --- property, from ProductInfo ---
    {"key": "neighborhood", "label_en": "Neighbourhood", "label_dv": "އަވަށް",
     "datatype": "enum", "widget": "checkbox", "priority": 11,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "room_facilities", "label_en": "Facilities", "label_dv": "ވަޞީލަތްތައް",
     "datatype": "enum", "widget": "checkbox", "priority": 50,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "lift", "label_en": "Lift", "label_dv": "ލިފްޓް",
     "datatype": "bool", "widget": "toggle", "priority": 60,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "floor", "label_en": "Floor", "label_dv": "ފަންގިފިލާ",
     "datatype": "enum", "widget": "checkbox", "priority": 61,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "furnishing", "label_en": "Furnishing", "label_dv": "ފަރުނީޗަރު",
     "datatype": "enum", "widget": "checkbox", "priority": 52,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "bedrooms", "label_en": "Bedrooms", "label_dv": "ކޮޓަރި",
     "datatype": "numeric", "widget": "checkbox", "priority": 12,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "bathrooms", "label_en": "Bathrooms", "label_dv": "ފާޚާނާ",
     "datatype": "numeric", "widget": "checkbox", "priority": 13,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "ideal_tenants", "label_en": "Tenants", "label_dv": "ކުއްޔަށްހިފާ",
     "datatype": "enum", "widget": "checkbox", "priority": 53,
     "is_facetable": True, "value_aliases": {},
     "categories": ["Housing & Real Estate"]},
    {"key": "square_feet", "label_en": "Square feet", "label_dv": "އަކަފޫޓު",
     "datatype": "numeric", "unit": "sqft", "unit_aliases": ["sqft", "sq ft"],
     "widget": "range", "priority": 54, "is_facetable": True,
     "value_aliases": {}, "categories": ["Housing & Real Estate"]},

    # --- jobs, from ProductInfo ---
    {"key": "position_type", "label_en": "Position type",
     "label_dv": "ވަޒީފާގެ ބާވަތް", "datatype": "enum", "widget": "checkbox",
     "priority": 14, "is_facetable": True, "value_aliases": {},
     "categories": ["Jobs"]},
    {"key": "job_category", "label_en": "Job category", "label_dv": "ދާއިރާ",
     "datatype": "enum", "widget": "checkbox", "priority": 15,
     "is_facetable": True, "value_aliases": {}, "categories": ["Jobs"]},
    {"key": "employer", "label_en": "Employer", "label_dv": "ވަޒީފާދޭ ފަރާތް",
     "datatype": "enum", "widget": "checkbox", "priority": 16,
     "is_facetable": True, "value_aliases": {}, "categories": ["Jobs"]},
    {"key": "salary_range", "label_en": "Salary range", "label_dv": "މުސާރަ",
     "datatype": "enum", "widget": "checkbox", "priority": 17,
     "is_facetable": False, "value_aliases": {}, "categories": ["Jobs"]},
    {"key": "apply_before", "label_en": "Apply before", "label_dv": "ސުންގަޑި",
     "datatype": "enum", "widget": "checkbox", "priority": 18,
     "is_facetable": False, "value_aliases": {}, "categories": ["Jobs"]},
]
```

`salary_range` and `apply_before` are seeded but not facetable: the structured `net_estimate` range and the computed `deadline_state` are strictly better filters, and offering both would put two salary controls on one page.

- [ ] **Step 4: Write the seeder and the candidate query**

`search/management/commands/seed_spec_keys.py`:

```python
from django.core.management.base import BaseCommand

from search.models import SpecKey
from search.specs.seed_data import SEED_KEYS


class Command(BaseCommand):
    help = "Create the initial SpecKey registry rows. Never overwrites curation."

    def handle(self, *args, **opts):
        created = 0
        for entry in SEED_KEYS:
            # get_or_create, not update_or_create: an admin who changed
            # is_facetable or priority by hand must not lose it on deploy.
            _, was_created = SpecKey.objects.get_or_create(
                key=entry["key"], defaults=entry
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(
            f"{created} created, {len(SEED_KEYS) - created} already present"
        ))
```

Append `candidate_keys` to `search/specs/project.py`:

```python
def candidate_keys(limit: int = 50) -> list[dict]:
    """Unpromoted key_raw values ranked by how many documents carry them.

    This is the admin promotion queue's data source (spec 4.4): the frequency
    ranking is what turns an open attribute space into a manageable list of
    one-click decisions.
    """
    from django.db.models import Count

    rows = (
        DocumentSpec.objects.filter(key__isnull=True)
        .values("key_raw")
        .annotate(documents=Count("document_id", distinct=True),
                  distinct_values=Count("value_text", distinct=True))
        .order_by("-documents")[:limit]
    )
    return list(rows)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/search/specs/test_seed.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P7 task 4: seed the SpecKey registry from measured frequency"
```

---

### Task 5: The discovery algorithm

**Files:**
- Create: `search/specs/discovery.py`
- Test: `tests/search/specs/test_discovery.py`

**Interfaces:**
- Produces: `discover_facets(cte, params, cursor, *, max_facets=8) -> list[dict]`, `score(coverage, entropy) -> float`, `normalized_entropy(counts) -> float`, `dominant_category(rows) -> str | None`, and the tunables `MIN_DOCUMENTS = 8`, `MIN_COVERAGE = 0.05`, `MAX_FACETS = 8`, `CATEGORY_DOMINANCE = 0.70`.

The six steps from spec 8.3, in order. Step 3 — discard keys whose values are effectively constant — is the one most implementations skip, and it is the difference between a filter panel and a wall of dead checkboxes.

- [ ] **Step 1: Write the failing test**

`tests/search/specs/test_discovery.py`:

```python
import pytest

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.specs.discovery import (
    MAX_FACETS, dominant_category, normalized_entropy, score,
)
from search.query import search_page


# --- the pure scoring parts ---------------------------------------------

def test_entropy_is_zero_for_a_constant_distribution():
    assert normalized_entropy([100]) == 0.0
    assert normalized_entropy([50, 0, 0]) == 0.0


def test_entropy_is_one_for_a_uniform_distribution():
    assert normalized_entropy([10, 10, 10, 10]) == pytest.approx(1.0)


def test_entropy_is_between_for_a_skewed_distribution():
    e = normalized_entropy([90, 5, 5])
    assert 0.0 < e < 1.0


def test_score_rewards_both_coverage_and_distinctiveness():
    assert score(1.0, 1.0) > score(1.0, 0.2)
    assert score(1.0, 0.5) > score(0.2, 0.5)


def test_dominant_category_needs_a_supermajority():
    rows = [{"category": "Mobile Phones"}] * 8 + [{"category": "Computers"}] * 2
    assert dominant_category(rows) == "Mobile Phones"
    rows = [{"category": "Mobile Phones"}] * 6 + [{"category": "Computers"}] * 4
    assert dominant_category(rows) is None


# --- the integrated pass -------------------------------------------------

@pytest.fixture
def power_supplies(db):
    """A candidate set that should surface voltage, amperage and wattage."""
    SpecKey.objects.create(key="voltage", label_en="Voltage", datatype="numeric",
                           unit="V", unit_aliases=["v"], widget="range",
                           is_facetable=True, priority=40)
    SpecKey.objects.create(key="current", label_en="Current", datatype="numeric",
                           unit="A", unit_aliases=["a"], widget="range",
                           is_facetable=True, priority=41)
    SpecKey.objects.create(key="power", label_en="Power", datatype="numeric",
                           unit="W", unit_aliases=["w"], widget="range",
                           is_facetable=True, priority=42)
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True, priority=20)
    SpecKey.objects.create(key="warranty", label_en="Warranty", datatype="enum",
                           widget="checkbox", is_facetable=False)

    keys = {k.key: k for k in SpecKey.objects.all()}
    for i in range(30):
        doc = SearchDocument.objects.create(
            source="ibay", source_key=f"ps{i}", doc_type="shopping",
            url="https://x", title_en=f"power supply unit {i}",
            attrs={"category_path": ["Electronics"]},
        )
        DocumentSpec.objects.create(document_id=doc.id, key=keys["voltage"],
                                    key_raw="voltage", value_num=12 + (i % 4) * 6,
                                    unit="V")
        DocumentSpec.objects.create(document_id=doc.id, key=keys["current"],
                                    key_raw="current", value_num=1 + (i % 5),
                                    unit="A")
        DocumentSpec.objects.create(document_id=doc.id, key=keys["power"],
                                    key_raw="power", value_num=60 + (i % 3) * 60,
                                    unit="W")
        # Constant across the whole set: must be discarded (spec 8.3 step 3).
        DocumentSpec.objects.create(document_id=doc.id, key=keys["brand"],
                                    key_raw="brand", value_text="KICO")
        # Not facetable: must never appear.
        DocumentSpec.objects.create(document_id=doc.id, key=keys["warranty"],
                                    key_raw="warranty", value_text="1 year")
        if i < 3:
            # Sparse: under the 8-document floor.
            DocumentSpec.objects.create(document_id=doc.id, key_raw="colour",
                                        value_text="black")
    from django.core.management import call_command
    call_command("reindex_vectors")


@pytest.mark.django_db
def test_power_supply_surfaces_its_unit_ranges(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    keys = [f["key"] for f in page.facets]
    assert {"voltage", "current", "power"} <= set(keys)


@pytest.mark.django_db
def test_a_constant_valued_key_is_discarded(power_supplies):
    """Every result is brand KICO. A filter that cannot partition the results
    is dead UI, and this is the check most implementations skip."""
    page = search_page("power supply", doc_type="shopping")
    assert "brand" not in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_a_sparse_key_is_discarded(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    assert "colour" not in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_a_non_facetable_key_never_appears(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    assert "warranty" not in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_universal_facets_survive_the_thresholds(power_supplies):
    """Price, condition, location and source are always available and are not
    subject to discovery. Spec 8.3."""
    page = search_page("power supply", doc_type="shopping")
    assert "price" in [f["key"] for f in page.facets]


@pytest.mark.django_db
def test_at_most_eight_dynamic_facets(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    dynamic = [f for f in page.facets if f.get("dynamic")]
    assert len(dynamic) <= MAX_FACETS


@pytest.mark.django_db
def test_a_numeric_facet_carries_a_ten_bucket_histogram(power_supplies):
    page = search_page("power supply", doc_type="shopping")
    v = next(f for f in page.facets if f["key"] == "voltage")
    assert v["widget"] == "range"
    assert v["unit"] == "V"
    assert len(v["histogram"]) == 10


@pytest.mark.django_db
def test_an_enum_facet_is_capped_at_twelve_values(db):
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True)
    key = SpecKey.objects.get(key="brand")
    for i in range(40):
        doc = SearchDocument.objects.create(source="ibay", source_key=f"p{i}",
                                            doc_type="shopping", url="https://x",
                                            title_en=f"phone {i}")
        DocumentSpec.objects.create(document_id=doc.id, key=key, key_raw="brand",
                                    value_text=f"Brand{i % 20}")
    from django.core.management import call_command
    call_command("reindex_vectors")
    page = search_page("phone", doc_type="shopping")
    brand = next(f for f in page.facets if f["key"] == "brand")
    assert len(brand["values"]) == 12


@pytest.mark.django_db
def test_a_category_supermajority_overrides_the_scoring_order(db):
    """Spec 8.3 step 5: this is what makes a phone search reliably lead with
    brand and storage instead of whatever happened to be dense that day."""
    SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                           widget="checkbox", is_facetable=True, priority=1,
                           categories=["Mobile Phones"])
    SpecKey.objects.create(key="weight", label_en="Weight", datatype="numeric",
                           unit="kg", widget="range", is_facetable=True,
                           priority=90, categories=["Mobile Phones"])
    keys = {k.key: k for k in SpecKey.objects.all()}
    for i in range(20):
        doc = SearchDocument.objects.create(
            source="ibay", source_key=f"m{i}", doc_type="shopping",
            url="https://x", title_en=f"iphone {i}",
            attrs={"category_path": ["Electronics", "Mobile Phones"]})
        DocumentSpec.objects.create(document_id=doc.id, key=keys["brand"],
                                    key_raw="brand",
                                    value_text=["Apple", "Samsung"][i % 2])
        DocumentSpec.objects.create(document_id=doc.id, key=keys["weight"],
                                    key_raw="weight", value_num=0.1 + i * 0.01,
                                    unit="kg")
    from django.core.management import call_command
    call_command("reindex_vectors")

    page = search_page("iphone", doc_type="shopping")
    dynamic = [f["key"] for f in page.facets if f.get("dynamic")]
    # weight has higher entropy (20 distinct values vs 2) and would win on raw
    # score; the curated priority for the dominant category must beat it.
    assert dynamic.index("brand") < dynamic.index("weight")


@pytest.mark.django_db
def test_discovery_runs_only_for_shopping(power_supplies):
    page = search_page("power supply", doc_type="job")
    assert not [f for f in page.facets if f.get("dynamic")]


@pytest.mark.django_db
def test_an_empty_result_set_produces_no_dynamic_facets(power_supplies):
    page = search_page("zzzznothing", doc_type="shopping")
    assert page.facets == [] or not [f for f in page.facets if f.get("dynamic")]
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/specs/test_discovery.py -v`
Expected: FAIL.

- [ ] **Step 3: Write the discovery pass**

`search/specs/discovery.py`:

```python
"""Dynamic facet discovery. Spec 8.3.

Runs over the candidate set from section 7, after retrieval and before
pagination. Six steps:

  1. aggregate DocumentSpec joined to the candidate CTE, is_facetable only
  2. discard keys present in fewer than 8 results or under 5% of them
  3. discard keys whose values are effectively constant
  4. score the survivors by coverage x distinctiveness (normalized entropy)
  5. when >=70% of candidates share one leaf category, the curated priority for
     that category overrides the scoring order
  6. emit at most 8, shaped by widget

So "power supply" surfaces voltage, amperage and wattage because DocumentSpec
holds 24V, 5A and 120W parsed out of titles, while "iphone" surfaces brand,
storage and condition. Same code path, different data.
"""

from __future__ import annotations

import math
from collections import Counter

from search.models import SpecKey

MIN_DOCUMENTS = 8
MIN_COVERAGE = 0.05
MAX_FACETS = 8
CATEGORY_DOMINANCE = 0.70
ENUM_TOP_N = 12
HISTOGRAM_BUCKETS = 10
# Below this, a key's values are effectively constant and the filter cannot
# partition anything.
MIN_ENTROPY = 0.05


def normalized_entropy(counts: list[int]) -> float:
    """Shannon entropy over the value distribution, scaled to [0, 1].

    Zero means one value dominates completely -- a dead filter. One means the
    values are evenly spread, which partitions the results best.
    """
    total = sum(counts)
    if total <= 0:
        return 0.0
    present = [c for c in counts if c > 0]
    if len(present) <= 1:
        return 0.0
    h = -sum((c / total) * math.log2(c / total) for c in present)
    return h / math.log2(len(present))


def score(coverage: float, entropy: float) -> float:
    return coverage * entropy


def dominant_category(rows: list[dict]) -> str | None:
    """The leaf category shared by at least 70% of the candidate set."""
    cats = [r.get("category") for r in rows if r.get("category")]
    if not cats:
        return None
    top, n = Counter(cats).most_common(1)[0]
    return top if n / len(rows) >= CATEGORY_DOMINANCE else None


_CANDIDATE_CATEGORY_SQL = """
SELECT COALESCE(d.attrs -> 'category_path' ->> -1, '') AS category, count(*)
FROM candidates d GROUP BY 1
"""

_TOTAL_SQL = "SELECT count(*) FROM candidates"

_KEY_STATS_SQL = """
SELECT s.key_id,
       count(DISTINCT s.document_id) AS documents,
       count(DISTINCT COALESCE(s.value_text, s.value_num::text)) AS distinct_values
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = ANY(%(facetable_ids)s)
GROUP BY 1
"""

_ENUM_SQL = """
SELECT s.value_text, count(DISTINCT s.document_id) AS n
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_text <> ''
GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %(top_n)s
"""

_ENUM_DISTRIBUTION_SQL = """
SELECT count(DISTINCT s.document_id) AS n
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_text <> ''
GROUP BY s.value_text
"""

_NUMERIC_SQL = """
SELECT min(s.value_num), max(s.value_num), count(DISTINCT s.document_id)
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_num IS NOT NULL
"""

_HISTOGRAM_SQL = """
SELECT width_bucket(s.value_num, %(lo)s, %(hi)s, %(buckets)s) AS b,
       count(DISTINCT s.document_id)
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_num IS NOT NULL
GROUP BY 1 ORDER BY 1
"""

_BOOL_SQL = """
SELECT count(DISTINCT s.document_id)
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND lower(s.value_text) IN ('true','yes','1')
"""


def discover_facets(cte: str, params: dict, cur, *,
                    max_facets: int = MAX_FACETS) -> list[dict]:
    """Returns dynamic facet entries in emit order, each shaped like FacetOut
    plus `dynamic: True` so P6 can tell them apart if it ever needs to."""
    facetable = list(SpecKey.objects.filter(is_facetable=True))
    if not facetable:
        return []
    by_id = {k.id: k for k in facetable}

    cur.execute(cte + _TOTAL_SQL, params)
    (total,) = cur.fetchone()
    if not total:
        return []

    cur.execute(cte + _CANDIDATE_CATEGORY_SQL, params)
    category_rows = [{"category": c, "n": n} for c, n in cur.fetchall()]
    expanded = [{"category": r["category"]} for r in category_rows
                for _ in range(r["n"])]
    dominant = dominant_category(expanded)

    # Step 1 and 2: aggregate, then apply the sparsity floors.
    cur.execute(cte + _KEY_STATS_SQL,
                {**params, "facetable_ids": list(by_id)})
    stats = []
    for key_id, documents, distinct_values in cur.fetchall():
        if documents < MIN_DOCUMENTS:
            continue
        coverage = documents / total
        if coverage < MIN_COVERAGE:
            continue
        if distinct_values <= 1:
            # Step 3, cheap form: literally one value. The entropy check below
            # catches the subtler 'one value in 98% of rows' case.
            continue
        stats.append({"key": by_id[key_id], "documents": documents,
                      "coverage": coverage, "distinct": distinct_values})

    # Steps 3 and 4: build each candidate and score it.
    scored = []
    for st in stats:
        key = st["key"]
        entry = _build(cur, cte, params, key, st, total)
        if entry is None:
            continue
        if entry["_entropy"] < MIN_ENTROPY:
            continue
        scored.append(entry)

    # Step 5: a category supermajority replaces the ordering with that
    # category's curated priority.
    if dominant:
        scoped = [e for e in scored
                  if not e["_key"].categories or dominant in e["_key"].categories]
        rest = [e for e in scored if e not in scoped]
        scoped.sort(key=lambda e: (e["_key"].priority, -e["_score"]))
        rest.sort(key=lambda e: -e["_score"])
        ordered = scoped + rest
    else:
        ordered = sorted(scored, key=lambda e: -e["_score"])

    # Step 6.
    out = []
    for entry in ordered[:max_facets]:
        entry.pop("_entropy", None)
        entry.pop("_score", None)
        entry.pop("_key", None)
        out.append(entry)
    return out


def _shell(key: SpecKey) -> dict:
    return {"key": key.key, "label": key.label_en, "label_dv": key.label_dv,
            "widget": key.widget, "unit": key.unit, "values": [],
            "min": None, "max": None, "histogram": [], "count_true": None,
            "dynamic": True}


def _build(cur, cte, params, key: SpecKey, st: dict, total: int) -> dict | None:
    entry = _shell(key)
    p = {**params, "key_id": key.id}

    if key.datatype == "numeric":
        cur.execute(cte + _NUMERIC_SQL, p)
        lo, hi, n = cur.fetchone()
        if lo is None or not n or float(lo) == float(hi):
            return None      # a single value is not a range
        entry["min"], entry["max"] = float(lo), float(hi)
        cur.execute(cte + _HISTOGRAM_SQL,
                    {**p, "lo": float(lo), "hi": float(hi),
                     "buckets": HISTOGRAM_BUCKETS})
        counts = {int(b): int(c) for b, c in cur.fetchall() if b is not None}
        counts[HISTOGRAM_BUCKETS] = (counts.get(HISTOGRAM_BUCKETS, 0)
                                     + counts.pop(HISTOGRAM_BUCKETS + 1, 0))
        width = (float(hi) - float(lo)) / HISTOGRAM_BUCKETS
        entry["histogram"] = [
            {"from": float(lo) + width * (i - 1),
             "to": float(lo) + width * i,
             "count": counts.get(i, 0)}
            for i in range(1, HISTOGRAM_BUCKETS + 1)
        ]
        entry["_entropy"] = normalized_entropy(
            [b["count"] for b in entry["histogram"]]
        )

    elif key.datatype == "bool":
        cur.execute(cte + _BOOL_SQL, p)
        (true_n,) = cur.fetchone()
        if not true_n or true_n == st["documents"]:
            return None      # all-true is as dead a filter as all-false
        entry["count_true"] = int(true_n)
        entry["_entropy"] = normalized_entropy(
            [true_n, st["documents"] - true_n]
        )

    else:
        cur.execute(cte + _ENUM_SQL, {**p, "top_n": ENUM_TOP_N})
        rows = cur.fetchall()
        if not rows:
            return None
        entry["values"] = [
            {"value": v, "label": v, "count": int(n)} for v, n in rows
        ]
        # Entropy over the FULL distribution, not the top 12: a key with one
        # dominant value and a long tail would look diverse from the top slice
        # alone and would be a bad filter anyway.
        cur.execute(cte + _ENUM_DISTRIBUTION_SQL, p)
        entry["_entropy"] = normalized_entropy([int(n) for (n,) in cur.fetchall()])

    entry["_key"] = key
    entry["_score"] = score(st["coverage"], entry["_entropy"])
    return entry
```

- [ ] **Step 4: Wire it into the query**

In `search/query.py`'s `compute_facets`, append after the static loop:

```python
    # Dynamic shopping facets append to the same ordered list, which is why
    # the API returns a list and not a map (spec 9). Universal facets are
    # computed above and are not subject to the discovery thresholds.
    if doc_type == "shopping":
        from search.specs.discovery import discover_facets
        with connection.cursor() as cur:
            out.extend(discover_facets(cte, params, cur))
```

In `search/filters.py`, extend `facet_def` so a spec-backed key resolves too:

```python
def facet_def(doc_type: str | None, key: str) -> FacetDef | None:
    for f in FACETS.get(doc_type or "all", ALL_FACETS):
        if f.key == key:
            return f
    return _spec_facet_def(key)


def _spec_facet_def(key: str) -> FacetDef | None:
    """A promoted SpecKey is a valid filter key even though it is not in the
    static registry. Still a whitelist -- is_facetable is the gate."""
    from search.models import SpecKey

    sk = SpecKey.objects.filter(key=key, is_facetable=True).first()
    if sk is None:
        return None
    return FacetDef(key=sk.key, label_en=sk.label_en, label_dv=sk.label_dv,
                    widget=sk.widget, storage="spec", path=sk.key, unit=sk.unit)
```

and add a `storage == "spec"` branch to `filter_sql`:

```python
        elif d.storage == "spec":
            sub = ("EXISTS (SELECT 1 FROM search_documentspec sp "
                   "JOIN search_speckey sk ON sk.id = sp.key_id "
                   "WHERE sp.document_id = d.id AND sk.key = %({p}_key)s AND {cond})")
            params[f"{p}_key"] = d.key
            if f.op == "range":
                conds = []
                if f.lo is not None:
                    conds.append(f"sp.value_num >= %({p}_lo)s")
                    params[f"{p}_lo"] = f.lo
                if f.hi is not None:
                    conds.append(f"sp.value_num <= %({p}_hi)s")
                    params[f"{p}_hi"] = f.hi
                cond = " AND ".join(conds) or "TRUE"
            elif f.op == "bool":
                cond = ("lower(sp.value_text) IN ('true','yes','1')"
                        if f.values[0] else
                        "lower(sp.value_text) NOT IN ('true','yes','1')")
            else:
                cond = f"sp.value_text = ANY(%({p})s)"
                params[p] = list(f.values)
            clauses.append(sub.format(p=p, cond=cond))
```

Order matters: put this branch before the generic `attrs_array` and `eq` branches, since `storage` is what distinguishes it.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/search/ -v`
Expected: PASS, including P5's facet tests unchanged.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P7 task 5: dynamic facet discovery"
```

---

### Task 6: The admin promotion queue

**Files:**
- Modify: `search/admin.py`
- Test: `tests/search/specs/test_admin.py`

**Interfaces:**
- Produces: `SpecKeyAdmin` with a `promote` action, and a `candidates` changelist view over `candidate_keys()`.

- [ ] **Step 1: Write the failing test**

`tests/search/specs/test_admin.py`:

```python
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from search.models import DocumentSpec, SearchDocument, SpecKey


@pytest.fixture
def staff(db):
    user = User.objects.create_superuser("admin", "a@example.com", "pw")
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_the_candidate_queue_ranks_by_document_count(staff):
    for i in range(3):
        doc = SearchDocument.objects.create(source="ibay", source_key=str(i),
                                            doc_type="shopping", url="https://x")
        DocumentSpec.objects.create(document_id=doc.id, key_raw="warranty",
                                    value_text="1 year")
    doc = SearchDocument.objects.create(source="ibay", source_key="x",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="colour",
                                value_text="black")

    r = staff.get(reverse("admin:search_speckey_candidates"))
    assert r.status_code == 200
    body = r.content.decode()
    assert body.index("warranty") < body.index("colour")


@pytest.mark.django_db
def test_promoting_a_key_creates_it_and_links_existing_rows(staff):
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="warranty",
                                value_text="1 year")

    r = staff.post(reverse("admin:search_speckey_candidates"),
                   {"promote": "warranty"}, follow=True)
    assert r.status_code == 200

    key = SpecKey.objects.get(key="warranty")
    assert key.is_facetable is True
    assert DocumentSpec.objects.get(key_raw="warranty").key_id == key.id


@pytest.mark.django_db
def test_promotion_infers_the_datatype_from_the_stored_values(staff):
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key_raw="weight",
                                value_num=1.5, unit="kg")
    staff.post(reverse("admin:search_speckey_candidates"), {"promote": "weight"})
    key = SpecKey.objects.get(key="weight")
    assert key.datatype == "numeric" and key.widget == "range"
    assert key.unit == "kg"


@pytest.mark.django_db
def test_demoting_a_key_removes_it_from_facets_without_deleting_data(staff):
    key = SpecKey.objects.create(key="brand", label_en="Brand", datatype="enum",
                                 widget="checkbox", is_facetable=True)
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    DocumentSpec.objects.create(document_id=doc.id, key=key, key_raw="brand",
                                value_text="Apple")
    staff.post(reverse("admin:search_speckey_changelist"),
               {"action": "demote", "_selected_action": [key.id]})
    key.refresh_from_db()
    assert key.is_facetable is False
    assert DocumentSpec.objects.count() == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/specs/test_admin.py -v`
Expected: FAIL — no `admin:search_speckey_candidates` URL.

- [ ] **Step 3: Write the admin**

Append to `search/admin.py`:

```python
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from search.models import DocumentSpec, SpecKey
from search.specs.project import candidate_keys


@admin.register(SpecKey)
class SpecKeyAdmin(admin.ModelAdmin):
    list_display = ("key", "label_en", "datatype", "widget", "unit",
                    "is_facetable", "priority")
    list_filter = ("is_facetable", "datatype", "widget")
    list_editable = ("is_facetable", "priority")
    search_fields = ("key", "label_en", "label_dv")
    actions = ("promote", "demote")

    def get_urls(self):
        return [
            path("candidates/", self.admin_site.admin_view(self.candidates_view),
                 name="search_speckey_candidates"),
        ] + super().get_urls()

    def candidates_view(self, request):
        """The promotion queue. Spec 4.4.

        Extraction is open, so unpromoted key_raw values accumulate. Ranking
        them by document count turns an unbounded attribute space into a short
        list of one-click decisions.
        """
        if request.method == "POST" and request.POST.get("promote"):
            key_raw = request.POST["promote"]
            key = _promote(key_raw)
            self.message_user(
                request,
                f"Promoted {key.key} as {key.datatype}/{key.widget}. "
                f"Run `sync_specs` to relink historical rows.",
                messages.SUCCESS,
            )
            return HttpResponseRedirect(
                reverse("admin:search_speckey_candidates")
            )

        return render(request, "admin/search/speckey_candidates.html", {
            **self.admin_site.each_context(request),
            "title": "Spec key promotion queue",
            "rows": candidate_keys(limit=100),
        })

    @admin.action(description="Promote to a facet")
    def promote(self, request, queryset):
        queryset.update(is_facetable=True)

    @admin.action(description="Demote (stop faceting, keep the data)")
    def demote(self, request, queryset):
        # Never deletes DocumentSpec rows: the detail-page spec table still
        # shows them, and re-promoting must not require a re-sync.
        queryset.update(is_facetable=False)


def _promote(key_raw: str) -> SpecKey:
    rows = DocumentSpec.objects.filter(key_raw=key_raw, key__isnull=True)
    numeric = rows.filter(value_num__isnull=False).exists()
    unit = ""
    if numeric:
        first = rows.exclude(unit="").first()
        unit = first.unit if first else ""

    key, _ = SpecKey.objects.get_or_create(
        key=key_raw,
        defaults={
            "label_en": key_raw.replace("_", " ").title(),
            "datatype": "numeric" if numeric else "enum",
            "widget": "range" if numeric else "checkbox",
            "unit": unit,
            "is_facetable": True,
        },
    )
    rows.update(key=key)
    return key
```

`search/templates/admin/search/speckey_candidates.html`:

```html
{% extends "admin/base_site.html" %}
{% block content %}
<p>Unpromoted attribute keys, ranked by how many documents carry them.
   Promoting a key makes it a filter; the data is already stored either way.</p>
<table>
  <thead><tr><th>Key</th><th>Documents</th><th>Distinct values</th><th></th></tr></thead>
  <tbody>
  {% for row in rows %}
    <tr>
      <td>{{ row.key_raw }}</td>
      <td>{{ row.documents }}</td>
      <td>{{ row.distinct_values }}</td>
      <td>
        <form method="post">{% csrf_token %}
          <button name="promote" value="{{ row.key_raw }}">Promote</button>
        </form>
      </td>
    </tr>
  {% empty %}
    <tr><td colspan="4">Nothing waiting.</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/search/specs/test_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P7 task 6: admin promotion queue"
```

---

### Task 7: Backfill and measure

**Files:**
- Create: `docs/superpowers/measurements/2026-08-p7-facets.md`
- Test: `tests/search/specs/test_latency.py`

- [ ] **Step 1: Seed, backfill, sync**

```bash
python manage.py seed_spec_keys
python manage.py enrich_documents --source ibay --type shopping   # if not done in P4
python manage.py reindex --source ibay
python manage.py sync_specs --source ibay --type shopping --prune
```

Record the row count: `SELECT count(*) FROM search_documentspec;`. Spec 4.4 predicts under 100,000. If it is far above, a multi-value field is splitting too aggressively and `split_multivalue` needs a narrower separator set.

- [ ] **Step 2: Write the latency test**

`tests/search/specs/test_latency.py`:

```python
import time

import pytest
from django.core.management import call_command

from search.models import DocumentSpec, SearchDocument, SpecKey
from search.query import search_page


@pytest.mark.django_db
@pytest.mark.slow
def test_discovery_stays_inside_the_facet_budget():
    """Discovery is N statements over a 500-row CTE. If this regresses it is
    the per-key statements, not the CTE -- and spec 16.4's Meilisearch
    re-entry condition names 'dynamic facet discovery proving too slow' as one
    of its two triggers."""
    for i in range(20):
        SpecKey.objects.create(key=f"spec{i}", label_en=f"Spec {i}",
                               datatype="numeric" if i % 2 else "enum",
                               unit="V" if i % 2 else "",
                               widget="range" if i % 2 else "checkbox",
                               is_facetable=True, priority=i)
    keys = list(SpecKey.objects.all())

    docs = SearchDocument.objects.bulk_create([
        SearchDocument(source="ibay", source_key=str(i), doc_type="shopping",
                       url=f"https://x/{i}", title_en=f"power supply {i}",
                       price=100 + i % 900, attrs={"category_path": ["Electronics"]})
        for i in range(20_000)
    ], batch_size=2000)
    DocumentSpec.objects.bulk_create([
        DocumentSpec(document_id=d.id, key=k, key_raw=k.key,
                     value_num=(i % 50) if k.datatype == "numeric" else None,
                     value_text="" if k.datatype == "numeric" else f"V{i % 7}")
        for i, d in enumerate(docs) for k in keys[:4]
    ], batch_size=5000)
    call_command("reindex_vectors")

    timings = []
    for _ in range(10):
        t = time.perf_counter()
        page = search_page("power supply", doc_type="shopping")
        timings.append((time.perf_counter() - t) * 1000)
        assert page.facets

    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]
    print(f"\nshopping search with discovery p95={p95:.0f}ms")
    assert p95 < 600, f"p95 {p95:.0f}ms exceeds the 600ms budget"
```

- [ ] **Step 3: Eyeball the two named queries**

The spec names two: "power supply" must surface voltage, amperage and wattage ranges; "iphone" must surface brand, storage and condition checkboxes. Run both against the real corpus and record what actually came back, in order.

```bash
python manage.py shell -c "
from search.query import search_page
for q in ['power supply', 'iphone', 'washing machine', 'ac', 'laptop']:
    p = search_page(q, doc_type='shopping')
    print(q, '->', [(f['key'], f['widget']) for f in p.facets if f.get('dynamic')])
"
```

- [ ] **Step 4: Record**

`docs/superpowers/measurements/2026-08-p7-facets.md`:

```markdown
# P7 dynamic facets, measured

Date: <fill>

## Volume

| | Count |
|---|---|
| SpecKey rows | |
| SpecKey facetable | |
| DocumentSpec rows | |
| Documents with at least one spec | |
| Average specs per shopping document | |

Spec 4.4 predicted ~20,000 products x ~4 specs, under 100,000 rows. Actual: <fill>.

## The two named queries

Spec 8.3 names these specifically. If they do not work, the feature does not.

| Query | Dynamic facets returned, in order | Right? |
|---|---|---|
| power supply | | expect voltage, current, power ranges |
| iphone | | expect brand, storage, condition |
| washing machine | | |
| ac | | |
| laptop | | |

## Discovery cost

| | p50 | p95 |
|---|---|---|
| shopping search, static facets only | | |
| shopping search, with discovery | | |
| delta | | |

Budget 600ms p95. Spec 16.4's Meilisearch re-entry condition is this table
missing target.

## Rejection reasons

Instrument discover_facets to log why each facetable key was dropped, then:

| Reason | Keys dropped |
|---|---|
| below MIN_DOCUMENTS (8) | |
| below MIN_COVERAGE (5%) | |
| single distinct value | |
| below MIN_ENTROPY | |
| beyond MAX_FACETS (8) | |

A key dropped for entropy on every query is a key that should be demoted.

## Promotion queue, top 20 candidates

| key_raw | documents | distinct values | promote? |
|---|---|---|---|

## Decisions this changes

- [ ] Are MIN_DOCUMENTS=8 and MIN_COVERAGE=5% right at this corpus size?
- [ ] Does MIN_ENTROPY=0.05 kill anything useful, or let dead filters through?
- [ ] Does the 70% category dominance rule ever fire on real queries?
- [ ] Meilisearch re-entry (16.4): triggered or not?
```

- [ ] **Step 5: Commit**

```bash
jj commit -m "P7 task 7: backfill, measurements recorded"
```

---

## Self-Review

**Spec coverage.** 4.4 in full: two tables (task 1), open extraction with a curated registry (tasks 1, 2, 4), category scoping (task 4's `type` entry), `value_aliases` with the Apple case (tasks 1, 2, 4), the deterministic unit extractor running before the model (task 2), multi-value splitting (task 2), and the ProductInfo projection (task 3). 8.3's six discovery steps → task 5, each with its own test, including step 3 which the plan calls out as the commonly skipped one. 8.3's universal facets → unchanged from P5 and asserted to survive discovery. 9's ordered-list contract → dynamic entries append to the same list. 12.2's `db_constraint=False` → task 1 and its dangling-row test.

**Known gaps, deliberate.** The commerce grid is P6's `ResultList` and already switches to a grid for the shopping tab; nothing here changes the frontend, which is the point of P5's ordered-list response shape. Per-category `SpecKey.priority` curation against real query logs is P8 — the logs exist from P5 but a week of traffic is worth more than a guess.

**Type consistency checked.** `discover_facets` emits entries matching `FacetOut` plus `dynamic: True`, the same shape `_enum_facet` / `_range_facet` / `_toggle_facet` produce in P5. `facet_def` returns `FacetDef` from either the static registry or a promoted `SpecKey`, so `filter_sql` needs only the new `storage == "spec"` branch. `specs_for_document` returns dicts whose keys are exactly `DocumentSpec`'s constructor kwargs.

**The one thing to watch.** `_KEY_STATS_SQL` and the per-key statements each re-execute the candidate CTE. Postgres will usually keep it materialized within one statement, but these are separate statements — so the CTE runs once per facet. At 500 candidate rows that is cheap, which is exactly why the candidate cap exists; if task 7's measurement shows discovery dominating, the fix is a temporary table for the candidate ids, not a wider CTE.
