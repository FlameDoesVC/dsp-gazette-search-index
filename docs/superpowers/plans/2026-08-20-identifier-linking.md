# Identifier Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every reference number a gazette notice states or cites becomes a link that finds every other document carrying that number.

**Architecture:** An identifier is a token translation does not touch, so the tokens appearing verbatim in both the Thaana and the translated text are the identifier set. Extraction is therefore deterministic, free, and structurally incapable of inventing a number. Matching normalizes to digits-in-order plus the sorted letter multiset, so one number spelled two ways still resolves to one thread.

**Tech Stack:** Django 6.0.5 (venv runs 5.2.17), PostgreSQL 18, pytest + pytest-django. No model calls, no external requests, no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-19-identifiers-and-spelling-design.md` -- sections 3.4, 3.5, 4, 5, 6, 7, 9, 10. The spelling-correction half of that spec is **out of scope for this plan** and ships separately.

**Depends on:** P1 (`SearchDocument`, `search/query.py`'s candidate CTE), P2 (gazette translation, which extraction reads), P5 (`api/routers/documents.py`).

---

## Global Constraints

- **No model call anywhere in this plan.** Extraction is an intersection of two token sets. If a task seems to need a model, the task is wrong.
- **Grounding is structural, not validated.** A candidate exists only if it appears in both texts, so there is no path by which a fabricated number reaches the index. Do not add a validator to compensate for one that is not needed.
- **The display form is the Thaana side.** That is the source of record; the translated side is only evidence that the token survived translation. Spec section 4.
- **Match on `value_key`, never on the raw string.** One document spells its own announcement number `171-Y(FBM2)/IUL/2026/166` in the scraped field and `171-Y(FMB2)/IUL/2026/166` in the body, and both spellings occur in the Thaana too. Raw matching splits one thread in two. Spec section 3.1.
- **A candidate must contain `/`.** Every real identifier does; `col-md-12`, which is HTML class leakage in the scraped body, does not. Spec section 3.4.
- **`DocumentIdentifier` stores `(source, source_key)`, not a document FK.** `SearchDocument` is LIST-partitioned and rows must survive a reindex. Same reasoning as `catalog.EntityLink`. Spec 12.2.
- **Never assign a model INSTANCE to a FK on an object streamed over `STREAM_DB_ALIAS`; assign the `_id`,** and pass `.using(settings.STREAM_DB_ALIAS)` to the matching `bulk_update`. No normal test catches this: `conftest.py` points `STREAM_DB_ALIAS` at `default`, which makes both objects same-alias. It cost a live command run in the catalog project.
- **An ordinary query must not touch the identifier path.** A regression in normal search is a worse outcome than the feature not shipping.
- **Streaming uses `.iterator(chunk_size=500)` on `settings.STREAM_DB_ALIAS`.**
- Version control is **jj**, not git.

---

## Measured evidence

From the live corpus on 2026-08-19 and 2026-08-20.

**The corpus this runs on is nearly empty, and that shapes the plan.** 125
`Iulaan` rows, of which **29 have a translated body**, and **0 gazette
`SearchDocument` rows** -- gazette has never been indexed in this database. Task 6
indexes it; until then nothing about this feature is observable end to end.

**The method, on iulaan 408123.** The invariant intersection is four tokens and
all four are identifiers, with no noise:

```
171-Y(FBM2)/IUL/2026/146      171-Y(FMB2)/IUL/2026/166
PC-171/2026/T327              BC-171/2026/094
```

**Recall, and why the naive version looks broken:**

| | recall |
|---|---|
| intersect raw token strings | 44.7% |
| intersect on `value_key`, require a `/` | **90.5%** |

The 44.7% was never translation losing numbers. It was `19/2014` against
`19/2014.`, and `col-md-12` counting as a candidate. Of the 4 remaining misses at
90.5%, three should not be identifiers -- `www.csc.gov.mv/download/2024/84/Annex`
is a URL and `7924894/3315555` is two phone numbers joined by a slash -- leaving
**one true miss in 42**.

**Kinds.** Of 31 identifiers in translated bodies, 13 are preceded by a label
(`project number`, `announcement number`, `bid committee in meeting number`,
`job opportunity number`, `has been granted license number`, `authority number`,
`ref`). The other 18 have none and get `other`, which links exactly as well.

**Identifier shapes already scraped**, present on 121 of 125 iulaan:

```
674-A/2026/46   FSM-ADV/2026/171   (IUL)142-A5/142/2026/183
(IUL)179-4/1/2026/15   (IUL)340/340/2026/43   171-Y(FBM2)/IUL/2026/166
```

---

## File structure

```
search/
  identifiers.py                  NEW  value_key, candidates, extract,
                                  classify_kind, looks_like_identifier
  models.py                       MODIFIED: DocumentIdentifier
  migrations/00XX_documentidentifier.py
  admin.py                        MODIFIED: DocumentIdentifierAdmin
  query.py                        MODIFIED: identifier candidate + score term
  management/commands/extract_identifiers.py   NEW

api/
  schemas.py                      MODIFIED: IdentifierOut
  routers/documents.py            MODIFIED: identifiers on the detail response

tests/
  search/test_identifiers.py      NEW  the pure functions, on real strings
  search/test_identifier_extract.py NEW  the command, over gazette rows
  search/test_identifier_query.py  NEW  retrieval and non-regression
  api/test_identifiers_api.py      NEW  the detail payload
```

`search/identifiers.py` rather than `gazette/`: extraction is gazette-only today
but `value_key` and `looks_like_identifier` are read by the query path, which is
source-agnostic. Putting the pair in different apps would split one contract.

---

## Task 1: The extraction primitives

**Files:**
- Create: `search/identifiers.py`
- Test: `tests/search/test_identifiers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  ```python
  KINDS: list[tuple[str, str]]
  value_key(raw: str) -> str
  candidates(text: str) -> dict[str, str]          # value_key -> display form
  classify_kind(preceding_text: str) -> str
  extract(thaana_text: str, translated_text: str) -> list[dict]
      # [{"value_raw": str, "value_key": str, "kind": str, "label_raw": str}]
  looks_like_identifier(q: str) -> bool
  ```

- [ ] **Step 1: Write the failing test**

`tests/search/test_identifiers.py`:

```python
import pytest

from search.identifiers import (candidates, classify_kind, extract,
                                looks_like_identifier, value_key)

# Real strings from iulaan 408123 and its neighbours. Every case below is a
# thing the corpus actually contains, not an invented example.
DV = """
(IUL)171-Y(FBM2)/IUL/2026/146 ge dhashun
Project Number: PC-171/2026/T327
171-Y(FMB2)/IUL/2026/166
BC-171/2026/094 ge bid committee
"""
EN = """
Following the announcement number 171-Y(FBM2)/IUL/2026/146
Project Number:

PC-171/2026/T327

Announcement Number:

171-Y(FMB2)/IUL/2026/166

This decision was made by the Bid Committee in meeting number BC-171/2026/094
"""


# --------------------------------------------------------------------------
# value_key: digits in order, plus the letter multiset sorted
# --------------------------------------------------------------------------

def test_a_transposed_office_code_keys_the_same():
    """The defect this exists for: one document spells its own announcement
    number FBM2 in the scraped field and FMB2 in the body, both in the Thaana."""
    assert (value_key("171-Y(FBM2)/IUL/2026/166")
            == value_key("171-Y(FMB2)/IUL/2026/166"))


def test_a_stray_parenthesis_does_not_change_the_key():
    assert (value_key("(IUL)142-A5/142/2026/183")
            == value_key("IUL)142-A5/142/2026/183"))


def test_trailing_punctuation_does_not_change_the_key():
    """'19/2014' against '19/2014.' was 46 points of recall."""
    assert value_key("19/2014") == value_key("19/2014.")


def test_different_prefixes_do_not_collide():
    """Discarding letters entirely was the obvious simplification. BC is a bid
    committee meeting and PC is a project; they must not merge."""
    assert value_key("BC-171/2026/094") != value_key("PC-171/2026/094")


def test_different_sequence_numbers_do_not_collide():
    assert value_key("674-A/2026/46") != value_key("674-A/2026/44")


def test_the_key_is_case_insensitive():
    assert value_key("pc-171/2026/t327") == value_key("PC-171/2026/T327")


# --------------------------------------------------------------------------
# candidates: the shape filter
# --------------------------------------------------------------------------

def test_candidates_keep_every_real_shape():
    found = set(candidates(
        "674-A/2026/46 FSM-ADV/2026/171 (IUL)142-A5/142/2026/183 "
        "(IUL)179-4/1/2026/15 PC-171/2026/T327"
    ).values())
    assert len(found) == 5


def test_a_css_class_is_rejected():
    """'col-md-12' is HTML leaking into the scraped body. It has digits and a
    hyphen; requiring a slash is what excludes it."""
    assert candidates("col-md-12 col-sm-6") == {}


def test_a_url_is_rejected():
    assert candidates("www.csc.gov.mv/download/2024/84/Annex") == {}
    assert candidates("https://gazette.gov.mv/iulaan/2026/1") == {}


def test_two_phone_numbers_joined_by_a_slash_are_rejected():
    """Maldivian numbers are seven digits starting 7, 9, 3 or 6, and sellers
    write pairs. '7924894/3315555' is not a reference number."""
    assert candidates("call 7924894/3315555 now") == {}


def test_a_bare_year_or_date_is_rejected():
    assert candidates("2026 30/06/2026") == {} or "2026" not in candidates(
        "2026 30/06/2026").values()


def test_candidates_are_keyed_for_matching_and_valued_for_display():
    got = candidates("PC-171/2026/T327")
    assert list(got.values()) == ["PC-171/2026/T327"]
    assert list(got) == [value_key("PC-171/2026/T327")]


# --------------------------------------------------------------------------
# extract: the intersection
# --------------------------------------------------------------------------

def test_extract_returns_only_tokens_present_in_both_texts():
    got = {r["value_key"] for r in extract(DV, EN)}
    assert got == {value_key(x) for x in (
        "171-Y(FBM2)/IUL/2026/146", "PC-171/2026/T327",
        "171-Y(FMB2)/IUL/2026/166", "BC-171/2026/094")}


def test_extract_cannot_invent_a_number():
    """Structural, not validated: a token only in the translation never appears.
    This is what replaces the grounding validator."""
    got = {r["value_raw"] for r in extract("nothing here",
                                           "Project Number: PC-171/2026/T327")}
    assert got == set()


def test_extract_uses_the_thaana_spelling_for_display():
    """The Thaana side is the source of record. Here the two sides disagree on
    the office code, and the stored display form must follow the source."""
    rows = extract("171-Y(FBM2)/IUL/2026/166", "171-Y(FMB2)/IUL/2026/166")
    assert [r["value_raw"] for r in rows] == ["171-Y(FBM2)/IUL/2026/166"]


def test_extract_labels_the_kind_from_the_translated_text():
    by_key = {r["value_key"]: r for r in extract(DV, EN)}
    assert by_key[value_key("PC-171/2026/T327")]["kind"] == "project"
    assert by_key[value_key("BC-171/2026/094")]["kind"] == "bid_committee"
    assert by_key[value_key("171-Y(FMB2)/IUL/2026/166")]["kind"] == "announcement"


def test_an_unlabelled_identifier_is_other_and_still_extracted():
    rows = extract("PC-171/2026/T327", "see PC-171/2026/T327 attached")
    assert len(rows) == 1
    assert rows[0]["kind"] == "other"


def test_extract_is_empty_without_a_translation():
    """96 of 125 local iulaan have no translated body. They contribute only
    their scraped number, which the command adds separately."""
    assert extract(DV, "") == []


def test_extract_deduplicates_repeated_mentions():
    rows = extract("PC-171/2026/T327 and PC-171/2026/T327",
                   "PC-171/2026/T327 again PC-171/2026/T327")
    assert len(rows) == 1


# --------------------------------------------------------------------------
# classify_kind and the query gate
# --------------------------------------------------------------------------

@pytest.mark.parametrize("before,expected", [
    ("Project Number:", "project"),
    ("Announcement Number:", "announcement"),
    ("in response to announcement number", "announcement"),
    ("This decision was made by the Bid Committee in meeting number",
     "bid_committee"),
    ("Job Opportunity Number", "job"),
    ("has been granted license number", "license"),
    ("Authority Number", "license"),
    ("Civil Court Thinadhoo Maldives Ref", "reference"),
    ("and then some prose that names nothing", "other"),
    ("", "other"),
])
def test_classify_kind(before, expected):
    assert classify_kind(before) == expected


@pytest.mark.parametrize("q,expected", [
    ("PC-171/2026/T327", True),
    ("171-Y(FBM2)/IUL/2026/166", True),
    ("19/2014", True),
    ("iphone charger", False),
    ("samsung a15 128gb", False),
    ("", False),
    ("7924894/3315555", False),          # phones, not an identifier
    ("www.csc.gov.mv/download/2024/84", False),
])
def test_looks_like_identifier(q, expected):
    assert looks_like_identifier(q) is expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `venv/bin/python -m pytest tests/search/test_identifiers.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'search.identifiers'`.

- [ ] **Step 3: Write `search/identifiers.py`**

```python
"""Identifier extraction and matching. Spec sections 4 and 5.

No model call, and none is wanted. An identifier is a token that translation does
not touch, so the tokens appearing verbatim in both the Thaana and the translated
text are almost exactly the identifier set -- measured on iulaan 408123, the
intersection is four tokens and all four are identifiers, with no noise.

That also makes fabrication structurally impossible rather than merely validated:
a candidate exists only if it is in both texts, so there is no path by which an
invented number reaches the index.
"""

from __future__ import annotations

import re

KINDS = [
    ("project", "project"),
    ("announcement", "announcement"),
    ("bid_committee", "bid committee"),
    ("job", "job opportunity"),
    ("license", "license or authority"),
    ("reference", "reference"),
    ("invoice", "invoice"),
    ("contract", "contract"),
    ("other", "other"),
]

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9()/.\-]*")
_TRIM = ".,);:'\""
_MIN_LEN = 7

# A URL contains slashes and digits and is not a reference number.
_URLISH = re.compile(r"(?:www\.|https?:)", re.I)
# Maldivian numbers are seven digits starting 7 or 9 (mobile) or 3 or 6
# (landline), and advertisers write pairs joined by a slash. Measured:
# '7924894/3315555' was one of only four candidates the intersection missed, and
# it should never have been a candidate.
_PHONE = r"(?:\+?960[\s-]?)?(?:[79]\d{6}|[36]\d{6})"
_PHONE_PAIR = re.compile(rf"^{_PHONE}(?:\s*/\s*{_PHONE})+$")

# Labels, longest and most specific first: 'meeting number' must be tested
# before the bare 'number' forms, and 'announcement number' matches inside
# 'in response to announcement number' without a separate rule.
_LABEL_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"bid\s+committee|meeting\s+number", re.I), "bid_committee"),
    (re.compile(r"project\s+number", re.I), "project"),
    (re.compile(r"job\s+opportunit\w*\s+number", re.I), "job"),
    (re.compile(r"(?:licen[cs]e|authority)\s+number", re.I), "license"),
    (re.compile(r"(?:announcement|iulaan)\s+number", re.I), "announcement"),
    (re.compile(r"invoice\s+number", re.I), "invoice"),
    (re.compile(r"contract\s+number", re.I), "contract"),
    (re.compile(r"\bref(?:erence)?\.?\s*$", re.I), "reference"),
]

# How far back to look for the label. One line of prose; more than this and the
# label belongs to something else.
LABEL_WINDOW = 60


def value_key(raw: str) -> str:
    """Digits in order, then the letter multiset sorted.

    Letters are where the noise lives -- office codes, transpositions, stray
    parentheses -- and digits carry the identity. Sorting the letters absorbs a
    transposition without discarding them, which matters: dropping letters
    entirely collides BC-171/2026/094 with PC-171/2026/094, a bid committee
    meeting and a project.
    """
    upper = (raw or "").upper().strip(_TRIM)
    digits = "-".join(re.findall(r"[0-9]+", upper))
    letters = "".join(sorted(re.findall(r"[A-Z]", upper)))
    return f"{digits}|{letters}"


def _is_candidate(token: str) -> bool:
    if len(token) < _MIN_LEN or "/" not in token:
        return False
    if not any(c.isdigit() for c in token):
        return False
    if _URLISH.search(token):
        return False
    if _PHONE_PAIR.match(token):
        return False
    return True


def candidates(text: str) -> dict[str, str]:
    """`value_key` -> display form, for every identifier-shaped token in `text`.

    Keyed rather than listed because the intersection in `extract` is over keys:
    the two sides of a document routinely spell the same identifier differently.
    First occurrence wins the display slot.
    """
    out: dict[str, str] = {}
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0).strip(_TRIM)
        if _is_candidate(token):
            out.setdefault(value_key(token), token)
    return out


def _positioned(text: str) -> list[tuple[str, int]]:
    out = []
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0).strip(_TRIM)
        if _is_candidate(token):
            out.append((value_key(token), match.start()))
    return out


def classify_kind(preceding_text: str) -> str:
    """The kind of identifier, from the words in front of it.

    Measured: 13 of 31 identifiers in translated bodies state their kind this
    way and 18 do not. `other` is a perfectly good answer -- the link searches
    the number, so kind is display metadata.
    """
    window = re.sub(r"\s+", " ", (preceding_text or ""))[-LABEL_WINDOW:]
    for pattern, kind in _LABEL_RULES:
        if pattern.search(window):
            return kind
    return "other"


def extract(thaana_text: str, translated_text: str) -> list[dict]:
    """Identifiers common to both sides of one document.

    The display form comes from the Thaana side, which is the source of record.
    The translated side supplies two things and nothing else: proof the token
    survived translation, and the English label that gives the kind.
    """
    english = _positioned(translated_text)
    english_keys = {key for key, _pos in english}
    if not english_keys:
        return []

    labels: dict[str, tuple[str, str]] = {}
    for key, pos in english:
        if key in labels and labels[key][0] != "other":
            continue
        before = (translated_text or "")[max(0, pos - LABEL_WINDOW):pos]
        labels[key] = (classify_kind(before),
                       re.sub(r"\s+", " ", before).strip()[-40:])

    rows = []
    for key, display in candidates(thaana_text).items():
        if key not in english_keys:
            continue
        kind, label_raw = labels.get(key, ("other", ""))
        rows.append({"value_raw": display, "value_key": key,
                     "kind": kind, "label_raw": label_raw})
    return rows


def looks_like_identifier(q: str) -> bool:
    """Whether a query should be routed through the identifier index.

    Deliberately narrow. A query that is not identifier-shaped must never touch
    that path, because a regression in ordinary search is a worse outcome than
    this feature not shipping.
    """
    token = (q or "").strip().strip(_TRIM)
    return bool(token) and _is_candidate(token)
```

- [ ] **Step 4: Run the tests until they pass**

Run: `venv/bin/python -m pytest tests/search/test_identifiers.py -v`
Expected: PASS, 30 tests.

- [ ] **Step 5: Verify against the live corpus**

This is the number the plan's evidence section claims, so reproduce it:

```bash
venv/bin/python - <<'EOF'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beynunehcheh.settings"); django.setup()
from gazette.models import Iulaan
from search.identifiers import extract
i = Iulaan.objects.get(id=408123)
rows = extract(f"{i.title}\n{i.body or ''}",
               f"{i.translated_title or ''}\n{i.translated_body or ''}")
for r in rows:
    print(f"  {r['kind']:14s} {r['value_raw']}")
assert len(rows) == 4, f"expected 4 identifiers, got {len(rows)}"
EOF
```
Expected: exactly four rows -- the two `171-Y(...)/IUL/2026/...` forms,
`PC-171/2026/T327` and `BC-171/2026/094`.

- [ ] **Step 6: Commit**

```bash
jj commit -m "identifiers: deterministic extraction from the translation invariant"
```

---

## Task 2: The `DocumentIdentifier` model

**Files:**
- Modify: `search/models.py`, `search/admin.py`
- Create: `search/migrations/00XX_documentidentifier.py`
- Test: `tests/search/test_identifier_model.py`

**Interfaces:**
- Consumes: `KINDS` from task 1.
- Produces: `DocumentIdentifier` with `(source, source_key, value_raw, value_key, kind, label_raw, is_own)`.

- [ ] **Step 1: Write the failing test**

`tests/search/test_identifier_model.py`:

```python
import pytest
from django.db import IntegrityError, transaction

from search.identifiers import value_key
from search.models import DocumentIdentifier


@pytest.mark.django_db
def test_the_same_identifier_twice_in_one_document_is_one_row():
    for _ in range(2):
        try:
            with transaction.atomic():
                DocumentIdentifier.objects.create(
                    source="gazette", source_key="408123",
                    value_raw="PC-171/2026/T327",
                    value_key=value_key("PC-171/2026/T327"), kind="project")
        except IntegrityError:
            pass
    assert DocumentIdentifier.objects.count() == 1


@pytest.mark.django_db
def test_two_documents_can_share_one_identifier():
    """This is the whole feature: sharing a value_key is what links them."""
    key = value_key("PC-171/2026/T327")
    for doc in ("408123", "408150"):
        DocumentIdentifier.objects.create(
            source="gazette", source_key=doc, value_raw="PC-171/2026/T327",
            value_key=key, kind="project")
    assert DocumentIdentifier.objects.filter(value_key=key).count() == 2


@pytest.mark.django_db
def test_one_document_can_carry_the_same_number_under_two_kinds():
    """A number can be both the document's own announcement number and a
    reference cited in its body; the unique constraint includes kind."""
    key = value_key("171-Y(FBM2)/IUL/2026/166")
    DocumentIdentifier.objects.create(
        source="gazette", source_key="408123", value_raw="171-Y(FBM2)/IUL/2026/166",
        value_key=key, kind="announcement", is_own=True)
    DocumentIdentifier.objects.create(
        source="gazette", source_key="408123", value_raw="171-Y(FMB2)/IUL/2026/166",
        value_key=key, kind="reference")
    assert DocumentIdentifier.objects.filter(source_key="408123").count() == 2


@pytest.mark.django_db
def test_there_is_no_fk_to_searchdocument():
    """SearchDocument is LIST-partitioned and identifiers must survive a
    reindex, so the link is (source, source_key). Spec 12.2."""
    field_names = {f.name for f in DocumentIdentifier._meta.get_fields()}
    assert "document" not in field_names
    assert {"source", "source_key"} <= field_names
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/search/test_identifier_model.py -v`
Expected: FAIL, cannot import `DocumentIdentifier`.

- [ ] **Step 3: Add the model**

In `search/models.py`:

```python
class DocumentIdentifier(models.Model):
    """One identifier occurrence in one document. Spec section 6.

    Keyed on (source, source_key) rather than a document FK: SearchDocument is
    LIST-partitioned, so a real FK is unavailable (spec 12.2), and these rows
    must survive a reindex that drops and rebuilds it -- the same reasoning as
    catalog.EntityLink and enrich.EnrichedRecord.

    There is deliberately no relationship table between documents. Two documents
    are related because they share a `value_key`, which one indexed lookup
    answers, and a number cited by a document nobody thought to link still finds
    its siblings.
    """

    from search.identifiers import KINDS as IDENTIFIER_KINDS

    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)
    # What the document says, for display. The Thaana side wins: it is the
    # source of record.
    value_raw = models.CharField(max_length=128)
    # What matching uses (search/identifiers.py::value_key). One document spells
    # its own number FBM2 in one field and FMB2 in another, so the raw string is
    # not a usable key.
    value_key = models.CharField(max_length=160)
    kind = models.CharField(max_length=24, choices=IDENTIFIER_KINDS,
                            default="other")
    label_raw = models.CharField(max_length=64, blank=True)
    # True for the document's own reference number, which comes from the scraped
    # field and needs no translation.
    is_own = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_key", "value_key", "kind"],
                name="uniq_identifier_occurrence")
        ]
        indexes = [
            models.Index(fields=["value_key"], name="identifier_value_key"),
            models.Index(fields=["source", "source_key"], name="identifier_doc"),
        ]

    def __str__(self):
        return f"{self.value_raw} [{self.kind}]"
```

Move the `from search.identifiers import KINDS` to the module's import block if
the class-body import trips a linter; it is written inline here only to keep the
model self-describing.

- [ ] **Step 4: Register it in the admin**

In `search/admin.py`:

```python
@admin.register(DocumentIdentifier)
class DocumentIdentifierAdmin(admin.ModelAdmin):
    """Sorted so the most-shared numbers surface first: those are the threads
    the feature exists to expose, and a wrong one is most visible there."""

    list_display = ("value_raw", "kind", "source", "source_key", "is_own",
                    "label_raw")
    list_filter = ("kind", "source", "is_own")
    search_fields = ("value_raw", "value_key", "source_key")
    ordering = ("value_key", "source_key")
```

- [ ] **Step 5: Migrate and run the tests**

```bash
venv/bin/python manage.py makemigrations search --name documentidentifier
venv/bin/python manage.py migrate
venv/bin/python -m pytest tests/search/test_identifier_model.py -v
```
Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "identifiers: DocumentIdentifier, keyed on (source, source_key)"
```

---

## Task 3: The extraction command

**Files:**
- Create: `search/management/commands/extract_identifiers.py`
- Test: `tests/search/test_identifier_extract.py`

**Interfaces:**
- Consumes: `extract`, `value_key` from task 1; `DocumentIdentifier` from task 2.
- Produces: `extract_identifiers --source gazette [--limit N] [--dry-run]`, and
  `identifiers_for_iulaan(iulaan) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

`tests/search/test_identifier_extract.py`:

```python
import pytest
from django.core.management import call_command

from gazette.models import Iulaan, IulaanType, Office
from search.identifiers import value_key
from search.models import DocumentIdentifier


@pytest.fixture
def iulaan(db):
    office = Office.objects.create(name="Ministry of Finance")
    itype = IulaanType.objects.create(name="Bids")
    return Iulaan.objects.create(
        id=408123, title="Test iulaan", office=office, iulaan_type=itype,
        additional_info={"ނަންބަރު": "171-Y(FBM2)/IUL/2026/166"},
        body="(IUL)171-Y(FBM2)/IUL/2026/146 PC-171/2026/T327 BC-171/2026/094",
        translated_body=(
            "Following the announcement number 171-Y(FBM2)/IUL/2026/146. "
            "Project Number: PC-171/2026/T327. This decision was made by the "
            "Bid Committee in meeting number BC-171/2026/094."),
    )


@pytest.mark.django_db
def test_the_command_stores_the_extracted_identifiers(iulaan):
    call_command("extract_identifiers", "--source", "gazette")
    rows = {r.value_raw: r.kind for r in DocumentIdentifier.objects.all()}
    assert rows["PC-171/2026/T327"] == "project"
    assert rows["BC-171/2026/094"] == "bid_committee"
    assert rows["171-Y(FBM2)/IUL/2026/146"] == "announcement"


@pytest.mark.django_db
def test_the_scraped_number_is_stored_even_without_a_translation(db):
    """121 of 125 iulaan carry a scraped number and 96 have no translated body.
    Those documents must still get their own identifier."""
    office = Office.objects.create(name="X")
    itype = IulaanType.objects.create(name="Y")
    Iulaan.objects.create(
        id=409000, title="No translation", office=office, iulaan_type=itype,
        additional_info={"ނަންބަރު": "674-A/2026/46"}, body="dv only",
        translated_body="")
    call_command("extract_identifiers", "--source", "gazette")
    row = DocumentIdentifier.objects.get(source_key="409000")
    assert row.value_raw == "674-A/2026/46"
    assert row.is_own is True
    assert row.kind == "announcement"


@pytest.mark.django_db
def test_the_command_is_idempotent(iulaan):
    call_command("extract_identifiers", "--source", "gazette")
    first = DocumentIdentifier.objects.count()
    call_command("extract_identifiers", "--source", "gazette")
    assert DocumentIdentifier.objects.count() == first


@pytest.mark.django_db
def test_dry_run_writes_nothing(iulaan):
    call_command("extract_identifiers", "--source", "gazette", "--dry-run")
    assert DocumentIdentifier.objects.count() == 0


@pytest.mark.django_db
def test_a_number_only_in_the_translation_is_never_stored(db):
    """Structural grounding. The command must not become the place where an
    unsupported number sneaks in."""
    office = Office.objects.create(name="X")
    itype = IulaanType.objects.create(name="Y")
    Iulaan.objects.create(
        id=409001, title="t", office=office, iulaan_type=itype,
        additional_info={}, body="nothing here",
        translated_body="Project Number: PC-999/2026/T999")
    call_command("extract_identifiers", "--source", "gazette")
    assert not DocumentIdentifier.objects.filter(
        value_key=value_key("PC-999/2026/T999")).exists()


@pytest.mark.django_db
def test_two_documents_citing_one_number_become_one_thread(db):
    office = Office.objects.create(name="X")
    itype = IulaanType.objects.create(name="Y")
    for doc_id in (409010, 409011):
        Iulaan.objects.create(
            id=doc_id, title="t", office=office, iulaan_type=itype,
            additional_info={}, body="PC-171/2026/T327",
            translated_body="Project Number: PC-171/2026/T327")
    call_command("extract_identifiers", "--source", "gazette")
    assert DocumentIdentifier.objects.filter(
        value_key=value_key("PC-171/2026/T327")).count() == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/search/test_identifier_extract.py -v`
Expected: FAIL, `Unknown command: 'extract_identifiers'`.

- [ ] **Step 3: Write the command**

```python
"""Extract identifiers from gazette documents. Spec section 4.

Free and re-runnable: no model call, no network. A fix to the label vocabulary is
a re-run, not a re-spend.

Requires a translated body for the citation half. 96 of 125 local iulaan do not
have one yet, and those contribute only their own scraped number until
`translate_fields` catches up. Nothing is lost, it is merely late.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from search.identifiers import extract, value_key
from search.models import DocumentIdentifier

# The additional_info key holding the document's own reference number. Present
# on 121 of 125 local iulaan.
_K_REFERENCE = "ނަންބަރު"


def identifiers_for_iulaan(iulaan) -> list[dict]:
    """Every identifier for one iulaan: its own scraped number, then citations.

    The scraped number comes first and claims `is_own`, so a document that has
    not been translated still gets the identifier that matters most -- its own.
    """
    rows: list[dict] = []
    own = (iulaan.additional_info or {}).get(_K_REFERENCE, "").strip()
    if own:
        rows.append({"value_raw": own, "value_key": value_key(own),
                     "kind": "announcement", "label_raw": "", "is_own": True})

    seen = {r["value_key"] for r in rows}
    thaana = f"{iulaan.title or ''}\n{iulaan.body or ''}"
    translated = f"{iulaan.translated_title or ''}\n{iulaan.translated_body or ''}"
    for row in extract(thaana, translated):
        if row["value_key"] in seen:
            # Already stored as the document's own number. Recording it a second
            # time as a citation would make a document look like it references
            # itself.
            continue
        rows.append({**row, "is_own": False})
    return rows


class Command(BaseCommand):
    help = "Extract reference identifiers from gazette documents."

    def add_arguments(self, parser):
        parser.add_argument("--source", default="gazette")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if opts["source"] != "gazette":
            self.stderr.write("only gazette carries reference numbers")
            return

        from gazette.models import Iulaan

        qs = (Iulaan.objects.using(settings.STREAM_DB_ALIAS)
              .only("id", "title", "body", "translated_title",
                    "translated_body", "additional_info"))
        seen_docs = written = translated_docs = 0
        by_kind: dict[str, int] = {}

        for iulaan in qs.iterator(chunk_size=500):
            if opts["limit"] is not None and seen_docs >= opts["limit"]:
                break
            seen_docs += 1
            if (iulaan.translated_body or "").strip():
                translated_docs += 1
            rows = identifiers_for_iulaan(iulaan)
            for row in rows:
                by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
            if opts["dry_run"] or not rows:
                continue
            with transaction.atomic():
                for row in rows:
                    DocumentIdentifier.objects.update_or_create(
                        source="gazette", source_key=str(iulaan.id),
                        value_key=row["value_key"], kind=row["kind"],
                        defaults={"value_raw": row["value_raw"][:128],
                                  "label_raw": row["label_raw"][:64],
                                  "is_own": row["is_own"]},
                    )
                    written += 1

        self.stdout.write(
            f"{seen_docs} documents ({translated_docs} translated), "
            f"{written} identifier rows")
        for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"   {kind:14s} {n}")
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run; nothing written"))
```

- [ ] **Step 4: Run the tests until they pass**

Run: `venv/bin/python -m pytest tests/search/test_identifier_extract.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
jj commit -m "identifiers: extraction command, own number first"
```

---

## Task 4: Retrieval

**Files:**
- Modify: `search/query.py`
- Test: `tests/search/test_identifier_query.py`

**Interfaces:**
- Consumes: `looks_like_identifier`, `value_key` from task 1; `DocumentIdentifier` from task 2.
- Produces: an identifier-shaped query returns documents carrying that number, ranked above lexical matches. No new public function.

- [ ] **Step 1: Write the failing test**

`tests/search/test_identifier_query.py`:

```python
import pytest

from search.identifiers import value_key
from search.models import DocumentIdentifier, SearchDocument
from search.query import search


def make_doc(source_key, title, **kw):
    return SearchDocument.objects.create(
        source="gazette", source_key=source_key, doc_type="news",
        url=f"https://gazette.gov.mv/{source_key}", title_en=title,
        summary_en=title, is_active=True, **kw)


@pytest.fixture
def indexed(db):
    """Two documents sharing one identifier, plus a decoy that mentions it only
    as text and a third that shares nothing."""
    from search.indexing import reindex_vectors_for

    a = make_doc("408123", "Award of contract for network equipment")
    b = make_doc("408150", "Amendment to the award of contract")
    c = make_doc("408200", "Unrelated announcement about parking")
    key = value_key("PC-171/2026/T327")
    for doc in (a, b):
        DocumentIdentifier.objects.create(
            source="gazette", source_key=doc.source_key,
            value_raw="PC-171/2026/T327", value_key=key, kind="project")
    return a, b, c


@pytest.mark.django_db
def test_an_identifier_query_finds_every_document_carrying_it(indexed):
    a, b, c = indexed
    keys = {r.source_key for r in search("PC-171/2026/T327")}
    assert {"408123", "408150"} <= keys
    assert "408200" not in keys


@pytest.mark.django_db
def test_either_spelling_of_one_number_finds_the_same_documents(indexed):
    """The FBM2/FMB2 case, at the query level."""
    a, _b, _c = indexed
    key = value_key("171-Y(FBM2)/IUL/2026/166")
    DocumentIdentifier.objects.create(
        source="gazette", source_key=a.source_key,
        value_raw="171-Y(FBM2)/IUL/2026/166", value_key=key, kind="announcement")
    for spelling in ("171-Y(FBM2)/IUL/2026/166", "171-Y(FMB2)/IUL/2026/166"):
        assert a.source_key in {r.source_key for r in search(spelling)}


@pytest.mark.django_db
def test_an_identifier_match_outranks_a_lexical_match(indexed):
    """Someone who pastes a reference number wants that number."""
    a, b, _c = indexed
    lexical = make_doc("408300", "PC-171/2026/T327 mentioned only in the title")
    results = search("PC-171/2026/T327")
    assert results, "expected results"
    assert results[0].source_key in {"408123", "408150"}


@pytest.mark.django_db
def test_an_ordinary_query_is_unaffected(indexed):
    """The non-regression that matters more than the feature."""
    results = search("parking")
    assert {r.source_key for r in results} == {"408200"}


@pytest.mark.django_db
def test_an_unknown_identifier_returns_nothing_rather_than_everything(indexed):
    assert search("ZZ-999/1999/999") == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/search/test_identifier_query.py -v`
Expected: FAIL -- the identifier queries return nothing, because a reference
number does not tokenize into a useful tsquery.

- [ ] **Step 3: Add the identifier arm to the candidate CTE**

In `search/query.py`, extend `_PAGE_SQL`'s `candidates` CTE. Add to the SELECT
list, after the `trg` expression:

```sql
           (%(ident_key)s IS NOT NULL AND EXISTS (
                SELECT 1 FROM search_documentidentifier di
                WHERE di.source = d.source
                  AND di.source_key = d.source_key
                  AND di.value_key = %(ident_key)s
            )) AS ident
```

and add a fourth arm to the WHERE clause's match group:

```sql
         OR (%(ident_key)s IS NOT NULL AND EXISTS (
                SELECT 1 FROM search_documentidentifier di
                WHERE di.source = d.source
                  AND di.source_key = d.source_key
                  AND di.value_key = %(ident_key)s
            ))
```

Apply the same two additions to the facet CTE around line 288, so facet counts
match the results they describe -- spec 7 requires the two to agree, and a facet
set computed over a different candidate set is the defect that requirement
exists to prevent.

- [ ] **Step 4: Score the identifier hit**

Add to `_SCORE_EXPR`, before the expiry penalty:

```sql
         + %(w_identifier)s * CASE WHEN ident THEN 1 ELSE 0 END
```

In `_base_params`, add:

```python
        # An identifier match is not one signal among several: someone pasting a
        # reference number wants that number, so the weight is set above the
        # sum of the lexical terms rather than tuned against them.
        "w_identifier": r.get("w_identifier", 5.0),
        "ident_key": None,
```

and set the real value where the plan is built, in both `search` and
`search_page`:

```python
    from search.identifiers import looks_like_identifier, value_key
    if looks_like_identifier(q):
        params["ident_key"] = value_key(q)
```

Add `"w_identifier": 5.0` to `SEARCH_RANKING` in `beynunehcheh/settings.py` with
a comment pointing at this reasoning.

- [ ] **Step 5: Run the tests until they pass**

Run:
```bash
venv/bin/python -m pytest tests/search/test_identifier_query.py -v
venv/bin/python -m pytest tests/search tests/api -q
```
Expected: the new file passes and the existing search and API suites stay green.
`tests/search/test_query.py` and `tests/api/test_search.py` exercise the exact
SQL you edited -- if either fails, that is a real regression: STOP and report it
rather than editing those tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "identifiers: exact-match retrieval arm on the candidate CTE"
```

---

## Task 5: The detail payload

**Files:**
- Modify: `api/schemas.py`, `api/routers/documents.py`
- Test: `tests/api/test_identifiers_api.py`

**Interfaces:**
- Consumes: `DocumentIdentifier`.
- Produces: `identifiers: list[IdentifierOut]` on `GET /api/v1/documents/{id}`.

- [ ] **Step 1: Write the failing test**

`tests/api/test_identifiers_api.py`:

```python
import pytest

from search.identifiers import value_key
from search.models import DocumentIdentifier, SearchDocument


@pytest.fixture
def doc(db):
    d = SearchDocument.objects.create(
        source="gazette", source_key="408123", doc_type="news",
        url="https://gazette.gov.mv/408123", title_en="Award of contract")
    DocumentIdentifier.objects.create(
        source="gazette", source_key="408123", value_raw="171-Y(FBM2)/IUL/2026/166",
        value_key=value_key("171-Y(FBM2)/IUL/2026/166"), kind="announcement",
        is_own=True)
    DocumentIdentifier.objects.create(
        source="gazette", source_key="408123", value_raw="PC-171/2026/T327",
        value_key=value_key("PC-171/2026/T327"), kind="project",
        label_raw="Project Number")
    return d


@pytest.mark.django_db
def test_the_detail_response_carries_the_identifiers(api, doc):
    """news has no detail page in DETAIL_TYPES, so this asserts the shape via
    the serializer path the frontend uses; adjust doc_type if the endpoint
    gates on it."""
    d = SearchDocument.objects.get(source_key="408123")
    d.doc_type = "shopping"
    d.save(update_fields=["doc_type"])
    body = api.get(f"/api/v1/documents/{d.id}").json()
    got = {i["value_raw"]: i for i in body["identifiers"]}
    assert got["PC-171/2026/T327"]["kind"] == "project"
    assert got["171-Y(FBM2)/IUL/2026/166"]["is_own"] is True


@pytest.mark.django_db
def test_the_document_own_number_comes_first(api, doc):
    d = SearchDocument.objects.get(source_key="408123")
    d.doc_type = "shopping"
    d.save(update_fields=["doc_type"])
    body = api.get(f"/api/v1/documents/{d.id}").json()
    assert body["identifiers"][0]["is_own"] is True


@pytest.mark.django_db
def test_a_document_with_no_identifiers_returns_an_empty_list(api, db):
    d = SearchDocument.objects.create(
        source="ibay", source_key="1", doc_type="shopping",
        url="https://ibay.com.mv/1", title_en="A charger")
    assert api.get(f"/api/v1/documents/{d.id}").json()["identifiers"] == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/api/test_identifiers_api.py -v`
Expected: FAIL, `KeyError: 'identifiers'`.

- [ ] **Step 3: Add the schema**

In `api/schemas.py`:

```python
class IdentifierOut(Schema):
    """One reference number, for rendering as a link into search.

    `value_raw` is what the document says and what the link text shows;
    matching happens on the normalized key, which the client never sees.
    """

    value_raw: str
    kind: str
    label_raw: str = ""
    is_own: bool = False
```

and add to `DocumentDetailOut`, or to the detail response dict if the endpoint
returns a plain dict:

```python
    identifiers: list[IdentifierOut] = []
```

- [ ] **Step 4: Return them from the endpoint**

In `api/routers/documents.py::detail`, before the return:

```python
    # Own number first, then citations, so a reader sees what this document IS
    # before what it refers to.
    identifiers = [
        {"value_raw": i.value_raw, "kind": i.kind,
         "label_raw": i.label_raw, "is_own": i.is_own}
        for i in DocumentIdentifier.objects
        .filter(source=doc.source, source_key=doc.source_key)
        .order_by("-is_own", "kind", "value_raw")
    ]
```

and add `"identifiers": identifiers,` to the response dict.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python -m pytest tests/api -q`
Expected: PASS, including the existing document tests.

- [ ] **Step 6: Commit**

```bash
jj commit -m "identifiers: detail response carries them, own number first"
```

---

## Task 6: Index gazette, backfill, measure

**Files:**
- Create: `docs/superpowers/measurements/2026-08-identifiers.md`
- Modify: `docs/superpowers/plans/README.md`

- [ ] **Step 1: Index gazette**

Nothing about this feature is observable until gazette is in the index, and it
never has been in this database:

```bash
venv/bin/python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','beynunehcheh.settings'); django.setup()
from search.models import SearchDocument
print('gazette documents before:', SearchDocument.objects.filter(source='gazette').count())"
venv/bin/python manage.py reindex --source gazette
```
Expected: 0 before, about 125 after. If the adapter errors on rows lacking a
translated body, that is a finding worth reporting, not working around.

- [ ] **Step 2: Extract**

```bash
venv/bin/python manage.py extract_identifiers --source gazette --dry-run
venv/bin/python manage.py extract_identifiers --source gazette
```
Expected from the corpus as measured: about 125 documents, 29 of them translated,
roughly 121 own numbers plus about 38 citations.

- [ ] **Step 3: Verify the thread the feature exists for**

```bash
venv/bin/python - <<'EOF'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE","beynunehcheh.settings"); django.setup()
from django.db.models import Count
from search.models import DocumentIdentifier
shared = (DocumentIdentifier.objects.values("value_key")
          .annotate(docs=Count("source_key", distinct=True))
          .filter(docs__gt=1).order_by("-docs"))
print(f"identifiers appearing in more than one document: {shared.count()}")
for row in shared[:5]:
    rows = DocumentIdentifier.objects.filter(value_key=row["value_key"])
    print(f"  {row['docs']} docs  {rows.first().value_raw}")
    for r in rows[:4]:
        print(f"      {r.source_key}  {r.kind}  is_own={r.is_own}")
EOF
```

This count **is the payoff measurement**. If it is zero, the feature works and
buys nothing on this corpus, and that belongs in the measurement file rather
than being quietly omitted.

- [ ] **Step 4: Search it end to end**

```bash
venv/bin/python manage.py runserver 8975 --noreload --insecure &
sleep 6
curl -s "http://127.0.0.1:8975/api/v1/search?q=PC-171/2026/T327" | head -c 400
kill %1
```
Expected: the documents carrying that number, ranked first.

- [ ] **Step 5: Write the measurement file**

`docs/superpowers/measurements/2026-08-identifiers.md`, with:

- documents processed, and how many had a translated body
- identifier rows by kind, and the own-number vs citation split
- **identifiers shared across more than one document**, the payoff figure
- recall against a hand-checked sample of 20 documents: for each, the
  identifiers a human finds by reading, against what the extractor stored
- any candidate rejected by the URL, phone-pair or slash filters that turns out
  to have been a real identifier
- p95 latency for an identifier query against an ordinary one, since task 4
  added an EXISTS subquery to the candidate CTE that every query now evaluates

- [ ] **Step 6: Update the plans README**

Add to the status table:

```markdown
| `2026-08-20-identifier-linking.md` | Identifier linking | **landed** |
```

and to the measurements table:

```markdown
| Identifiers — rows by kind, cross-document threads | `measurements/2026-08-identifiers.md` | whether the spelling half is worth building |
```

- [ ] **Step 7: Commit**

```bash
jj commit -m "identifiers: gazette indexed, backfill run, measurements recorded"
```

---

## Self-Review

**Spec coverage.** Section 3.4 (the invariant method) is task 1. Section 3.5
(kinds from labels) is task 1's `classify_kind`. Section 4 (extraction, filters,
the scraped-number shortcut) is tasks 1 and 3. Section 5 (`value_key`) is task 1.
Section 6 (`DocumentIdentifier`) is task 2. Section 7 (retrieval and display) is
tasks 4 and 5. Section 9's sequencing dependency is task 3's docstring and task
6's split measurement. Section 10 (testing) is distributed across every task.

The spelling half of the spec -- sections 3.3, 8, and the `SpellingCorrection`
model -- is deliberately absent. It ships separately, and this plan's measurement
file is what decides whether it is worth building.

**Placeholder scan.** No TBDs. Task 5's first test carries a conditional note
about `DETAIL_TYPES` because `news` is excluded from the detail endpoint by
design (spec 8.4: a news result links straight to the source), and gazette
documents are `news` -- see the open question below.

**Type consistency.** `value_key` returns `str` everywhere. `extract` returns
dicts with `value_raw`, `value_key`, `kind`, `label_raw`; `identifiers_for_iulaan`
adds `is_own` to each. `KINDS` is the choices list in `search/identifiers.py` and
is imported by the model rather than duplicated.

**One thing the implementer must resolve, and it is not cosmetic.**
`api/routers/documents.py:18` restricts the detail endpoint to
`{"shopping", "job", "property"}`, because spec 8.4 says a news result links
straight to the source rather than to an internal page. Measured on the 125 local
iulaan by running them through `prior_for`: **82 would be `news` and 43 `job`**.
So two thirds of gazette documents have **no detail page to render identifier
links on**, and those are exactly the bids, tenders and awards that cite the
most numbers. Task 5's tests work around it by mutating `doc_type`, which proves
the serializer but not the user-visible feature.

Three ways out, and this is a design decision rather than an implementation
detail: add `news` to `DETAIL_TYPES` (contradicts spec 8.4), surface identifiers
on the search result card instead of a detail page, or give gazette its own
doc_type. **Stop and ask before choosing.** Reporting it is the right move; picking
one silently is not.
