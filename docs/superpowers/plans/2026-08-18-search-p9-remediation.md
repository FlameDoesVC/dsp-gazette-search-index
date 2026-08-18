# P9 System Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the defects found by running the system against real data, in the order that their absence hurts most.

**Architecture:** No new subsystems. Every task here corrects something already built: a ranking signal that exists in the database and is not read, translation happening in the wrong place at the wrong granularity, a spec requirement that was never implemented, and a set of review findings from P5 Task 0C. Each task ends with an eval case so the regression cannot return silently.

**Tech Stack:** Django 6.0.5, PostgreSQL 18, `core.translate` against `gemmatranslate:12b`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 5.5, 7, 8.3, 12.4, 14, 16.2.

**Depends on:** P1-P7 landed; P5 Tasks 0, 0B, 0C, 0D.

---

## Global Constraints

- **Every fix here gets an eval case.** Spec 14's evaluation set is the only mechanism that stops a relevance fix from silently regressing later, and it is the honest answer to "how do we stop this happening for other keywords" — not a rule per keyword, but a case per observed failure and a gate that runs on every change.
- **Translation never runs inside `reindex`.** Spec 3.3 makes reindexing cheap and repeatable on purpose; that is why `EnrichedRecord` is a separate table. A GPU call inside `to_document` makes every reindex as slow as a cold sync.
- **Short fields only, never bodies.** Spec 5.5.
- **Ranking weights live in `settings.SEARCH_RANKING`.** A tuning change is a settings change, never a query rewrite. Spec 7.
- **`recall@5 >= 0.80` is a floor, not a target.** If a change drops it, the change is wrong. Spec 14.
- Version control is **jj**, not git.

---

## Measured evidence behind this plan

All figures taken on 2026-08-18 against the live corpus.

**Ranking.** For the query `iphone`, restricted to `doc_type=shopping`, the top 12
results contained **zero actual phones**. Of 1,160 matching documents:

| leaf `category_path` | documents | median price |
|---|---|---|
| Cases, Protection & Skins | 310 | 105 |
| Screen Protection | 226 | 99 |
| Charger | 180 | 495 |
| Headset - Bluetooth | 84 | 2,900 |
| Phone Servicing & Unlocking | 57 | 750 |
| Data Cable | 39 | 300 |
| **Mobile Phones** | **38** | **19,624** |

Real phones ranked 13, 30 and 38. Score decomposition on the top result versus a
real phone:

```
CHARGER     r_en=12.2  trigram=0.097  quality=1.00  freshness=0.000
REAL PHONE  r_en=1.8   trigram=0.123  quality=1.00  freshness=0.000
```

`ts_rank_cd` dominates by 7x; every other term is noise beside it. **Normalization
does not fix this** -- flags 0, 1, 2, 3 and 32 were all tested and every one
returned 6 accessories in the top 6. That is the key finding: this is not a
lexical problem. "iPhone 15 phone cover" is a genuinely perfect text match for
"iphone", so no `ts_rank` tuning can separate it from a phone. The fix must use
non-lexical signal, and two are already in the database and unread:
`attrs.category_path` and `price`.

**Translation placement.** `sync_gazette` translates title *and* full body inline
at ingest. A 3-page sync (30 iulaan) took ~4.5 minutes, essentially all of it
translation; those 30 iulaan carry 149,471 body characters, which extrapolates to
**254 million characters** at 51,000 iulaan. Spec 5.5 says short fields only.

**Batching.** Six titles, `gemmatranslate:12b`:

| | Wall clock | Per title |
|---|---|---|
| one call per title | 5.7s | 0.9s |
| all six in one numbered call | 0.7s | 0.1s |

**7.7x faster, and more accurate**: `ނީލަން ކިޔުން` came back as "Niland
Reading" when translated alone and "Public auction" in the batch, because the
numbered context disambiguates.

**Query-side translation is not implemented.** `build_query_plan("preschool
teacher")` returns `terms_dv: []`, so an English query cannot match `vector_dv`
at all. Spec 5.5 requires it. This is why body translation currently exists and
why it cannot simply be deleted.

---

## Task order

Tasks 1-3 are one chain: batching makes translation cheap, query-side
translation makes body translation unnecessary, and only then can translation
leave `sync`. Task 4 is independent and is the most user-visible. Tasks 5-6 are
cleanup from P5 Task 0C.

| Task | Fixes | Independent? |
|---|---|---|
| 1 | translation is 7.7x slower than it needs to be | yes |
| 2 | English queries cannot reach `vector_dv` (spec 5.5 unimplemented) | yes |
| 3 | 254M characters of body translation inline in `sync` | needs 1 and 2 |
| 4 | "iphone" returns no phones | yes |
| 5 | P5 Task 0C review findings | yes |
| 6 | `fill_bilingual` never ran | needs 1 |

---

### Task 1: Batched translation

**Files:**
- Modify: `core/translate.py`
- Test: `core/tests/test_translate_batch.py`

**Interfaces:**
- Produces: `translate_batch(texts, *, target) -> list[str]`,
  `translate_batch_sync(texts, *, target) -> list[str]`.

Measured 7.7x faster and slightly more accurate. The accuracy gain is a real
effect worth keeping: `ނީލަން ކިޔުން` alone became "Niland Reading" (the model
transliterated rather than translated) but "Public auction" inside a numbered
batch, because neighbouring lines establish that these are notice categories.

- [ ] **Step 1: Write the failing test**

`core/tests/test_translate_batch.py`:

```python
import pytest

from core.translate import translate_batch_sync


class _Recorder:
    """Stands in for the provider. Records how many calls were made."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def provider(monkeypatch):
    def _install(reply):
        rec = _Recorder(reply)
        monkeypatch.setattr("core.translate._chat", rec)
        return rec
    return _install


def test_six_texts_take_one_call(provider):
    rec = provider("1. one\n2. two\n3. three\n4. four\n5. five\n6. six")
    out = translate_batch_sync(["a", "b", "c", "d", "e", "f"], target="en")
    assert out == ["one", "two", "three", "four", "five", "six"]
    assert len(rec.prompts) == 1


def test_numbering_is_stripped_from_each_result(provider):
    provider("1. Preschool Teacher Assistant\n2. Staff needed")
    assert translate_batch_sync(["a", "b"], target="en") == [
        "Preschool Teacher Assistant", "Staff needed",
    ]


def test_a_short_reply_falls_back_to_one_call_per_item(provider):
    """The batch failure mode: the model returns fewer lines than it was given,
    so results would silently shift onto the wrong documents. Misalignment must
    never be papered over -- fall back and pay for accuracy."""
    rec = provider("1. only one line")
    out = translate_batch_sync(["a", "b", "c"], target="en")
    assert len(out) == 3
    assert len(rec.prompts) > 1        # fell back to individual calls


def test_a_long_reply_also_falls_back(provider):
    rec = provider("1. a\n2. b\n3. c\n4. spurious extra line")
    out = translate_batch_sync(["a", "b", "c"], target="en")
    assert len(out) == 3
    assert len(rec.prompts) > 1


def test_out_of_order_numbering_is_reordered_not_trusted_positionally(provider):
    provider("2. second\n1. first")
    assert translate_batch_sync(["a", "b"], target="en") == ["first", "second"]


def test_batch_size_is_respected(provider):
    rec = provider("\n".join(f"{i}. x" for i in range(1, 5)))
    translate_batch_sync(["a"] * 8, target="en", batch_size=4)
    assert len(rec.prompts) == 2


def test_an_empty_input_makes_no_call(provider):
    rec = provider("")
    assert translate_batch_sync([], target="en") == []
    assert rec.prompts == []


def test_the_cache_is_consulted_per_item_not_per_batch(provider, db):
    """A batch of six where five are cached must send one item, not six.
    Keying the cache on the batch would make it useless -- batches never
    repeat, individual titles repeat constantly (40% of iBay titles are
    duplicates)."""
    from core.models import TranslationCache
    for i, t in enumerate(["a", "b", "c", "d", "e"]):
        TranslationCache.objects.create(source_text=t, target_lang="en",
                                        translated_text=f"cached-{i}")
    rec = provider("1. fresh")
    out = translate_batch_sync(["a", "b", "c", "d", "e", "f"], target="en")
    assert out[:5] == [f"cached-{i}" for i in range(5)]
    assert out[5] == "fresh"
    assert len(rec.prompts) == 1
```

The `TranslationCache` field names above must match the real model; read
`core/models.py` first and adjust rather than assuming.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest core/tests/test_translate_batch.py -v`
Expected: FAIL — `ImportError: cannot import name 'translate_batch_sync'`

- [ ] **Step 3: Implement**

In `core/translate.py`:

```python
BATCH_SIZE = 8
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s*(.*)$")

_BATCH_PROMPT = (
    "Translate each numbered {src} line to {dst}. Output exactly one numbered "
    "line per input, using the same numbering, and nothing else. Do not merge, "
    "split, reorder or omit lines.\n\n"
)


def _parse_numbered(reply: str, expected: int) -> list[str] | None:
    """Return `expected` translations in input order, or None on misalignment.

    Returning None rather than a best guess is deliberate: a batch whose lines
    do not line up would attach each translation to the wrong document, which
    is silent data corruption rather than a visible failure.
    """
    found: dict[int, str] = {}
    for line in reply.splitlines():
        m = _NUMBERED.match(line)
        if m:
            found[int(m.group(1))] = m.group(2).strip()
    if len(found) != expected or set(found) != set(range(1, expected + 1)):
        return None
    return [found[i] for i in range(1, expected + 1)]
```

`translate_batch` then, per chunk of `batch_size`:

1. Look each item up in `TranslationCache` individually and collect the misses.
   Cache per item, never per batch — batches never repeat, individual strings do
   (40% of iBay titles are duplicates).
2. Send only the misses as one numbered prompt.
3. `_parse_numbered`; on `None`, fall back to one call per missed item.
4. Write each result to `TranslationCache` individually.

Reuse the existing escalation ladder rather than adding a second one.

- [ ] **Step 4: Run**

Run: `pytest core/tests/ -v` — expected PASS, and the existing
`translate_auto` tests must be untouched.

- [ ] **Step 5: Measure it for real**

```bash
python manage.py shell -c "
import time
from core.translate import translate_batch_sync, translate_en_to_dv_sync
from search.models import SearchDocument as SD
ts = list(SD.objects.filter(source='ibay').exclude(title_en='')
          .values_list('title_en', flat=True)[:24])
t0=time.time(); [translate_en_to_dv_sync(t) for t in ts]; a=time.time()-t0
t0=time.time(); translate_batch_sync(ts, target='dv'); b=time.time()-t0
print(f'sequential {a:.1f}s  batched {b:.1f}s  speedup {a/b:.1f}x')
"
```

Record the result in `docs/superpowers/measurements/2026-08-p9-remediation.md`.
The bench above uses uncached strings the first time and cached ones the second;
run it on a fresh slice or clear those cache rows, or the speedup is fiction.

- [ ] **Step 6: Commit**

```bash
jj commit -m "P9 task 1: batched translation (measured 7.7x)"
```

---

### Task 2: Query-side translation (spec 5.5, never implemented)

**Files:** Modify `search/lang/expand.py`, `beynunehcheh/settings.py`. Test `search/tests/test_query_translation.py`.

**Interfaces:** `QueryPlan.translated_terms: list[str]`; `build_query_plan(q, *, translate=True)`.

`build_query_plan("preschool teacher")` returns `terms_dv: []`, so an English
query cannot match `vector_dv` at all. Spec 5.5 requires one cached
`translate_auto` call per unique query. That is O(unique queries), against the
O(documents) body translation it replaces — and `TranslationCache` (2,495 rows)
is already the mechanism.

- [ ] **Step 1: Failing test**

```python
import pytest
from search.lang import build_query_plan


@pytest.fixture
def stub(monkeypatch):
    calls = []

    def fake(text, target_lang=None, **kw):
        calls.append(text)
        return {"preschool teacher": "ޕްރީސްކޫލް ޓީޗަރ"}.get(text, "")

    monkeypatch.setattr("core.translate.translate_auto", fake)
    return calls


@pytest.mark.django_db
def test_an_english_query_gains_dhivehi_terms(stub):
    """Without this an English query cannot reach vector_dv, so no English
    query can ever match a Thaana gazette body."""
    plan = build_query_plan("preschool teacher")
    assert plan.terms_dv, "English query produced no Dhivehi terms"


@pytest.mark.django_db
def test_a_thaana_query_is_not_sent_to_the_translator(stub):
    build_query_plan("ވަޒީފާ")
    assert stub == []


@pytest.mark.django_db
def test_translation_is_cached_across_identical_queries(stub):
    build_query_plan("preschool teacher")
    build_query_plan("preschool teacher")
    assert len(stub) == 1


@pytest.mark.django_db
def test_a_translator_failure_degrades_rather_than_raises(monkeypatch):
    monkeypatch.setattr("core.translate.translate_auto",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    plan = build_query_plan("preschool teacher")
    assert plan.terms_en == ["preschool", "teacher"]   # English search still works


@pytest.mark.django_db
def test_translation_can_be_disabled_for_the_eval_harness(stub):
    """The harness must be able to measure the lexical baseline alone."""
    build_query_plan("preschool teacher", translate=False)
    assert stub == []
```

- [ ] **Step 2** Run — expect FAIL on the first test.

- [ ] **Step 3** Implement. After script detection, when `plan.lang == ENGLISH`
and `settings.SEARCH_TRANSLATE_QUERIES` is on, call `translate_auto` on the
whole query string (not per token — context matters, exactly as Task 1's batch
finding showed), normalize the result, and extend `plan.terms_dv`. Record it in
`plan.translated_terms` so `/search` can echo it in `expanded_terms`. Wrap in
try/except: a translator outage must not break English search.

Add `SEARCH_TRANSLATE_QUERIES = os.getenv("SEARCH_TRANSLATE_QUERIES", "1") == "1"`.

- [ ] **Step 4** Run the eval set before and after; record both.
`python manage.py eval_search`. Cross-language recall is the number this task
exists to move.

- [ ] **Step 5** `jj commit -m "P9 task 2: query-side translation"`

---

### Task 3: Move translation out of sync

**Files:** Modify `gazette/sync_service.py`. Test `gazette/tests.py`.

**Do not start until Tasks 1 and 2 pass their eval runs.** Body translation is
what currently gives English queries any reach into gazette bodies; removing it
before Task 2 lands degrades English recall silently.

Measured: a 3-page sync (30 iulaan) took ~4.5 minutes, essentially all
translation. Those 30 iulaan hold 149,471 body characters — **254 million** at
51,000 iulaan, inline at ingest, redone on every re-scrape.

- [ ] **Step 1: Failing test**

```python
@pytest.mark.django_db
def test_sync_stores_raw_and_translates_nothing(monkeypatch):
    """Ingest is network-bound and retryable; translation is a separate,
    resumable pass. A failed sync currently re-translates everything it had
    already done."""
    calls = []
    monkeypatch.setattr("core.translate.translate_auto",
                        lambda *a, **k: calls.append(a) or "x")
    # ... drive one sync cycle against a stubbed client ...
    assert calls == []


@pytest.mark.django_db
def test_bodies_are_never_translated():
    """Spec 5.5: short fields only. 254M characters at full corpus size."""
    # after sync, translated_body must be empty
```

- [ ] **Step 2** Remove `_translate_body` and the inline title translation from
`sync_all`. Sync writes `title`, `body`, `additional_info`, `attachments` only.

- [ ] **Step 3** Titles are then filled by `fill_bilingual` (Task 6), which after
Task 1 is batched. `translated_body` stays on the model but unpopulated — do not
drop the column in this task; a migration that discards text is not reversible
and Task 2's eval run is what authorises it.

- [ ] **Step 4** Re-run a 3-page sync and record the wall clock against the
4.5-minute baseline.

- [ ] **Step 5** `jj commit -m "P9 task 3: sync stores raw, translation deferred"`

---

### Task 4: Category-aware ranking

**Files:** Create `search/rank_signals.py`. Modify `search/query.py`, `search/facets.py`, `beynunehcheh/settings.py`. Test `search/tests/test_rank_category.py`.

For `iphone` restricted to shopping, the top 12 held **zero phones**. Of 1,160
matches only 38 are in `Mobile Phones`; 310 are cases, 226 screen protectors,
180 chargers. `ts_rank_cd` scores the charger 12.2 against the phone's 1.8.

**Normalization does not fix this.** Flags 0, 1, 2, 3 and 32 were all measured
and every one returned 6 accessories in the top 6, because "iPhone 15 phone
cover" *is* a perfect lexical match for "iphone". The fix must use non-lexical
signal, and `attrs.category_path` already carries it.

Two mechanisms, deliberately in this order.

**4a. Category diversity in the result page.** Generalises to every keyword with
no curation and no guess about intent: cap consecutive results from one leaf
category, exactly as `interleave()` already caps `doc_type` on the All tab. Six
chargers cannot occupy the top six, so `Mobile Phones` surfaces on page one
whatever the query.

**4b. A curated accessory flag. — SUPERSEDED by P10 task 1; do not implement.** `Category.tier` covers this for every source, including ones with no categories of their own, whereas the `CategoryKind` model below hand-curates a distinction only iBay's paths state. Keep 4a: category diversity in the result page is still a useful safety net when no inference fires. Original text follows for context.

~~A curated accessory flag.~~ `SpecKey` already models "extraction is open,
faceting is curated" (spec 4.4); the same shape applies here. Add
`CategoryKind` with `leaf` and `kind ∈ {primary, accessory, service, part}`,
seeded from the frequency table above, and demote non-primary leaves when the
query does not name one. ~50 leaves to curate, one admin screen, and it is the
precise fix where 4a is only a mitigation.

- [ ] **Step 1: Failing test — this is the acceptance criterion**

```python
@pytest.mark.django_db
def test_iphone_returns_an_actual_phone_on_page_one(ibay_corpus):
    """The reported defect. Measured before this task: zero phones in the top
    12, real phones at ranks 13, 30 and 38."""
    page = search_page("iphone", doc_type="shopping", per_page=10)
    leaves = [(r.card.get("category_leaf") or "") for r in page.results]
    assert "Mobile Phones" in leaves


@pytest.mark.django_db
def test_no_more_than_three_consecutive_results_share_a_leaf_category(ibay_corpus):
    page = search_page("iphone", doc_type="shopping", per_page=20)
    run, prev = 0, None
    for r in page.results:
        leaf = r.card.get("category_leaf")
        run = run + 1 if leaf == prev else 1
        prev = leaf
        assert run <= 3


@pytest.mark.django_db
def test_a_query_that_names_an_accessory_still_returns_accessories(ibay_corpus):
    """'iphone case' must not demote cases. The demotion is conditional on the
    query, or the fix breaks every accessory search."""
    page = search_page("iphone case", doc_type="shopping", per_page=10)
    leaves = [r.card.get("category_leaf") for r in page.results]
    assert any("Case" in (l or "") for l in leaves)


@pytest.mark.django_db
def test_category_is_available_as_a_facet(ibay_corpus):
    """The immediate escape hatch, and what a commerce site is expected to
    offer: 'Cases 310 / Screen Protection 226 / Mobile Phones 38'."""
    page = search_page("iphone", doc_type="shopping")
    assert any(f["key"] == "category_leaf" for f in page.facets)
```

- [ ] **Step 2** Run — expect FAIL on the first and last.

- [ ] **Step 3** Store the leaf. `category_path` is a JSONB array; add a
`category_leaf` column populated by the indexer from `attrs.category_path[-1]`,
indexed, and exposed in `card`. Aggregating a JSONB array element per request is
avoidable work and the leaf is what both mechanisms need.

- [ ] **Step 4** Add `category_leaf` to `SHOPPING_FACETS` as a `checkbox` facet
over the new column. This alone gives users a working escape and should land even
if the ranking work is deferred.

- [ ] **Step 5** Implement 4a in `search/interleave.py` as `interleave_by(results,
key, cap=3)`, and call it for shopping with `key=category_leaf`. Generalise the
existing `doc_type` interleave rather than writing a second one.

- [ ] **Step 6** Implement 4b: `CategoryKind` model, admin, a `seed_category_kinds`
command seeded from the measured frequency table, and a ranking term
`w_category_kind` applied when no query token matches the leaf name.

- [ ] **Step 7** `eval_search` before and after. Add `iphone`, `samsung`,
`laptop`, `washing machine` and `ac` as eval cases with `expect_leaf`
assertions. **This is the general answer to "how do we stop this for other
keywords": a case per observed failure and a gate that runs on every change,
not a rule per keyword.**

- [ ] **Step 8** `jj commit -m "P9 task 4: category-aware ranking and facet"`

---

### Task 5: Deduplicate listings, keeping the most recent

**Files:** Create `search/dedupe.py`, `search/management/commands/dedupe_listings.py`. Modify `search/models.py`, `search/indexing.py`, `search/query.py`. Test `search/tests/test_dedupe.py`.

The largest defect found, and worse for the user than Task 4:

```
distinct titles appearing more than once : 1,808
total redundant rows                     : 8,089   (40% of the iBay corpus)
202x  Rooms for daily and hourly rent contact 9940965 | 7940965
153x  MN-2 ROOM FOR DAILY/HOURLY RENT CALL FOR BOOKING: 7388832
151x  MN-3 ROOM FOR DAILY/HOURLY RENT. CALL FOR BOOKING: 7208283
```

A search for "room daily rent" returns two hundred copies of one listing —
sellers reposting the same ad daily to stay at the top of iBay's own ordering.

**`content_hash` will not collapse them.** Measured: 17 distinct hashes across
those 202 identical-titled rows, because descriptions differ slightly while the
title, seller and price are identical. The grouping key has to be narrower than
the body.

**Resolved once at index time, not per query.** A `DISTINCT ON` in the candidate
CTE would pay this cost on every request and interact badly with the 500-row
cap — post-filtering a candidate set that is 200 copies of one listing leaves an
almost-empty page. Marking duplicates once means the existing `is_active`-style
filter does the work for free.

**Nothing is deleted.** Spec 12.6: an inactive listing moves out of the way, it
does not disappear. A separate `is_duplicate` flag rather than reusing
`is_active`, because `is_active` is derived from the source by the adapter and
would be overwritten on the next reindex — the two would fight.

**Interfaces:** `dedupe_key(draft) -> str`, `mark_duplicates(source=None) -> dict`,
`SearchDocument.is_duplicate`, `SearchDocument.duplicate_count`.

- [ ] **Step 1: Write the failing test**

```python
import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone

from search.models import SearchDocument
from search.query import search_page


def _listing(key, title, *, days_ago, seller="s1", price=500):
    return SearchDocument.objects.create(
        source="ibay", source_key=key, doc_type="property",
        url=f"https://x/{key}", title_en=title, price=price,
        published_at=timezone.now() - dt.timedelta(days=days_ago),
        attrs={"seller_id": seller},
    )


@pytest.mark.django_db
def test_only_the_most_recent_of_a_group_survives():
    _listing("1", "Room for daily rent 9940965", days_ago=5)
    keep = _listing("2", "Room for daily rent 9940965", days_ago=0)
    _listing("3", "Room for daily rent 9940965", days_ago=3)
    call_command("dedupe_listings")
    live = SearchDocument.objects.filter(is_duplicate=False)
    assert [d.source_key for d in live] == [keep.source_key]


@pytest.mark.django_db
def test_the_survivor_records_how_many_it_represents():
    for i in range(4):
        _listing(str(i), "Room for daily rent 9940965", days_ago=i)
    call_command("dedupe_listings")
    kept = SearchDocument.objects.get(is_duplicate=False)
    assert kept.duplicate_count == 4


@pytest.mark.django_db
def test_duplicates_are_flagged_never_deleted():
    """Spec 12.6: nothing is destroyed."""
    for i in range(3):
        _listing(str(i), "Same title here", days_ago=i)
    call_command("dedupe_listings")
    assert SearchDocument.objects.count() == 3
    assert SearchDocument.objects.filter(is_duplicate=True).count() == 2


@pytest.mark.django_db
def test_flagged_duplicates_do_not_appear_in_search():
    for i in range(3):
        _listing(str(i), "Room for daily rent 9940965", days_ago=i)
    call_command("reindex_vectors")
    call_command("dedupe_listings")
    page = search_page("room daily rent", doc_type="property")
    assert page.total == 1


@pytest.mark.django_db
def test_different_sellers_with_the_same_title_are_not_collapsed():
    """Two landlords may honestly advertise 'Room for rent'. The key includes
    the seller, so this collapses reposts, not competitors."""
    _listing("1", "Room for rent", days_ago=1, seller="a")
    _listing("2", "Room for rent", days_ago=0, seller="b")
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 2


@pytest.mark.django_db
def test_a_different_price_is_a_different_listing():
    _listing("1", "iPhone 13", days_ago=1, price=9000)
    _listing("2", "iPhone 13", days_ago=0, price=14000)
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 2


@pytest.mark.django_db
def test_titles_differing_only_in_case_and_punctuation_are_one_group():
    """'MN-2 ROOM FOR DAILY/HOURLY RENT' and 'MN-2 Room for Daily/Hourly Rent'
    are the same ad."""
    _listing("1", "MN-2 ROOM FOR DAILY/HOURLY RENT.", days_ago=1)
    _listing("2", "MN-2 Room for Daily / Hourly Rent", days_ago=0)
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 1


@pytest.mark.django_db
def test_rerunning_is_idempotent():
    for i in range(3):
        _listing(str(i), "Same", days_ago=i)
    call_command("dedupe_listings")
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=False).count() == 1


@pytest.mark.django_db
def test_a_newly_synced_repost_becomes_the_survivor():
    """The seller reposts tomorrow. After the next dedupe the new row is the
    live one and yesterday's is flagged -- the flag is recomputed, not sticky."""
    old = _listing("1", "Room for rent", days_ago=1)
    call_command("dedupe_listings")
    _listing("2", "Room for rent", days_ago=0)
    call_command("dedupe_listings")
    old.refresh_from_db()
    assert old.is_duplicate is True
    assert SearchDocument.objects.get(is_duplicate=False).source_key == "2"


@pytest.mark.django_db
def test_gazette_documents_are_never_deduplicated():
    """Two councils may publish identically-titled notices. A published
    government notice is not a repost, and gazette is write-once (spec 5.7)."""
    SearchDocument.objects.create(source="gazette", source_key="A",
                                  doc_type="news", url="https://x/a",
                                  title_en="Public Information")
    SearchDocument.objects.create(source="gazette", source_key="B",
                                  doc_type="news", url="https://x/b",
                                  title_en="Public Information")
    call_command("dedupe_listings")
    assert SearchDocument.objects.filter(is_duplicate=True).count() == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest search/tests/test_dedupe.py -v`
Expected: FAIL — no `is_duplicate` field.

- [ ] **Step 3: Add the fields**

In `search/models.py`:

```python
    # Set by `dedupe_listings`, not by the adapter. A separate flag rather than
    # reusing `is_active`: that one is derived from the source and would be
    # overwritten on the next reindex, so the two would fight.
    is_duplicate = models.BooleanField(default=False)
    # How many rows this one stands for, including itself.
    duplicate_count = models.IntegerField(default=1)
    dedupe_key = models.CharField(max_length=64, blank=True)
```

Migration plus a partial index, since the query filters on it constantly:

```sql
CREATE INDEX sd_dedupe_key ON search_searchdocument (dedupe_key)
    WHERE dedupe_key <> '';
```

- [ ] **Step 4: The key**

`search/dedupe.py`:

```python
"""Repost collapsing. Keeps the most recent listing of each group.

Sellers repost the same advertisement daily to stay near the top of iBay's own
ordering; 8,089 of 20,445 rows are duplicate titles, one appearing 202 times.

The key is (seller, normalized title, price) and deliberately NOT
`content_hash`: measured, 202 identical-titled rows carry 17 distinct content
hashes because descriptions vary slightly, so a body-inclusive hash collapses
almost nothing.

Gazette is excluded. Two councils may publish identically-titled notices, and a
published government notice is not a repost.
"""

from __future__ import annotations

import hashlib

from search.lang.normalize import normalize_text

EXCLUDED_SOURCES = {"gazette", "archive"}


def dedupe_key(*, source: str, seller: str, title: str, price) -> str:
    if source in EXCLUDED_SOURCES:
        return ""
    basis = "|".join([
        source,
        (seller or "").strip().lower(),
        normalize_text(title or ""),
        f"{float(price):.2f}" if price is not None else "",
    ])
    return hashlib.sha256(basis.encode()).hexdigest()
```

`normalize_text` from P2 already lowercases, strips punctuation and collapses
whitespace, which is what makes `MN-2 ROOM FOR DAILY/HOURLY RENT.` and
`MN-2 Room for Daily / Hourly Rent` one group. Reuse it rather than writing a
second normalizer.

Populate `dedupe_key` in `search/indexing.py::_row`, reading the seller from
`draft.card.get("seller_name")` or `draft.attrs.get("seller_id")` — whichever the
iBay adapter actually sets; read it rather than assuming.

- [ ] **Step 5: The command**

`search/management/commands/dedupe_listings.py`: group by `dedupe_key`,
`ORDER BY published_at DESC NULLS LAST, id DESC`, clear `is_duplicate` on the
first and set it on the rest, and write the group size to `duplicate_count` on
the survivor. Support `--source`, `--dry-run` and a summary line reporting groups
collapsed and rows flagged.

Recompute rather than accumulate: every run clears the flag first, so a fresh
repost becomes the survivor and yesterday's is demoted.

- [ ] **Step 6: Filter it out of search**

In `search/query.py`, add `AND NOT d.is_duplicate` to both the page CTE and the
facet CTE. Both, or facet counts stop matching the result set — the property
spec 7 exists to protect.

- [ ] **Step 7: Surface the count**

Carry `duplicate_count` into `card` so P6 can render "202 similar listings"
linking to the ungrouped set. Hiding results silently is the same sin as silent
relaxation.

- [ ] **Step 8: Run and measure**

```bash
python manage.py reindex --source ibay          # populates dedupe_key
python manage.py dedupe_listings --dry-run      # expect ~1,808 groups, ~8,089 rows
python manage.py dedupe_listings
```

Record `total` for "room daily rent" before and after, and the corpus-wide
active count. Then add `dedupe_listings` to the scheduled-jobs table in the
plans README — it must run after every reindex, since `reindex` rewrites the
rows it depends on.

- [ ] **Step 9: Commit**

```bash
jj commit -m "P9 task 5: deduplicate listings, keeping the most recent"
```

---

### Task 6: Keyword-stuffing penalty

**Files:** Modify `search/indexing.py`, `search/rank_signals.py`. Test `search/tests/test_stuffing.py`.

Measured over 19,570 titles: repetition ratio mean 0.030, **median 0.000** — so
this is a small minority, but it is the minority that wins ranking because
repetition inflates `ts_rank_cd` position counts.

```
>0.15 repetition:  1,180 listings
>0.20 repetition:    627
title >14 tokens:  1,782

rep=0.50  AC Gas Leakage AC- Water Leakage. Maintenance. Water. Leakage. Gas. Le
rep=0.47  USB to USB Cable AM TO AM Male to Male USB-A TO USB-A 1.5M
rep=0.43  SAMSUNG A13 4G/A23 4G/A13 LITE/A23 LITE/A23/A234G/A23 LITE/M33/M33 5G
```

Three patterns: literal term repetition, spelling-variant stuffing to catch
every query spelling, and model enumeration.

`quality` is already a ranking term with `w_quality = 0.2` and a real
distribution (0.0 to 1.0), so this needs **no new ranking term** — feed the
signal into the existing hook.

- [ ] **Step 1: Failing test**

```python
from search.rank_signals import stuffing_penalty


@pytest.mark.parametrize("title,expect", [
    ("Apple iPhone 15 Pro Max 256GB", 0.0),
    ("AC Gas Leakage AC- Water Leakage. Maintenance. Water. Leakage. Gas.", 0.4),
    ("USB to USB Cable AM TO AM Male to Male USB-A TO USB-A 1.5M", 0.3),
])
def test_repetition_is_penalised_proportionally(title, expect):
    assert stuffing_penalty(title) == pytest.approx(expect, abs=0.15)


def test_a_short_clean_title_is_never_penalised():
    assert stuffing_penalty("iPhone 13") == 0.0


def test_a_legitimately_long_title_is_not_penalised_for_length_alone():
    """'7-in-1 USB C Hub Type C to USB 3.0 HDMI SD/TF Card Reader' is 20
    tokens and honest. Penalise repetition, not length."""
    assert stuffing_penalty(
        "7-in-1 USB C Hub Type C to USB 3.0 2.0 HDMI SD TF Card Reader"
    ) < 0.2
```

- [ ] **Step 2** Implement `stuffing_penalty(title) -> float` as
`1 - unique_tokens/total_tokens`, floored at 0 below a threshold so ordinary
titles are untouched, and subtract it from `quality` in `_row`.

- [ ] **Step 3** Re-run `eval_search`. This changes `quality` corpus-wide, so the
before/after numbers are mandatory.

- [ ] **Step 4** `jj commit -m "P9 task 6: keyword-stuffing penalty via quality"`

---

### Task 7: P5 Task 0C review findings

Four defects found reviewing 0C. All small, all user-visible.

- [ ] **Step 1** `IulaanType` has language-duplicate rows splitting one concept:
`ވަޒީފާގެ ފުރުޞަތު` (368 docs) and `Job Opportunity` (38) are separate rows, as
are `މަސައްކަތް`/`Work`, `ނީލަން`/`Auction`, `ޢާންމު މަޢުލޫމާތު`/`Public
Information`. Filtering `announcement_type` shows 38 of 406. Canonicalise in the
facet layer rather than merging rows — merging rewrites 38 documents' foreign
keys, canonicalising does not. Test that both variants land in one facet bucket.

- [ ] **Step 2** `Need to Rent` (2 docs) is the English variant of
`ކުއްޔަށް ހިފުން` → property and is absent from **both** mapping tables, so it
falls to the news default. Add the English variants of every type to
`enrich/prior.py` and `search/adapters/gazette.py`, and add a test asserting the
two tables agree on every `IulaanType` row in the database.

- [ ] **Step 3** Entity translations are wrong where they matter. These are facet
labels users read:

| Dhivehi | Stored | Correct |
|---|---|---|
| `ބީލަން` | "Bill" | tender / bids — spec §3.2 says so |
| `Beelan` | "Island" | romanisation of `ބީލަން` |
| `ތަމްރީނު` | "Exercise" | training |
| `ގަންނަން ބޭނުންވާ ތަކެތި` | "Items to buy." | items wanted |

Move `IulaanType` labels into the gettext catalog with the rest of the closed
vocabularies — 18 rows is a fixed vocabulary, not growing data. Keep machine
translation for `Office`, which is 418 rows and grows.

- [ ] **Step 4** `LISTING_KIND` in `search/vocab.py` omits `wanted`, which
`PropertyAttrs.listing_kind` permits, so it falls through to raw English.

- [ ] **Step 5** `jj commit -m "P9 task 7: 0C review findings"`

---

### Task 8: Run the pending backfills and record everything

- [ ] **Step 1** `fill_bilingual` has never run. The P5 Task 0C step-10 check
returns **19,890** documents missing `title_dv` against a required 0. After
Task 1 it is batched; run it.

```bash
python manage.py fill_entity_translations
python manage.py compilemessages
python manage.py fill_bilingual --dry-run     # expect ~19,890
python manage.py fill_bilingual --limit 200   # read twenty by hand first
python manage.py fill_bilingual
python manage.py reindex --source ibay --source gazette
python manage.py rebuild_suggest_terms
```

- [ ] **Step 2** Re-derive the measurements invalidated by corpus growth. Several
earlier decisions used a 306-iulaan sample; the corpus reached 1,245 before the
reset. The 40.3% scanned fraction in particular should be recomputed with
`extract_attachments --no-transcribe`.

- [ ] **Step 3** Write `docs/superpowers/measurements/2026-08-p9-remediation.md`:

```markdown
# P9 remediation, measured

| Fix | Before | After |
|---|---|---|
| translation throughput | 0.9s/title | (batched) |
| 3-page sync wall clock | 4m30s | |
| `iphone` phones in top 10 | 0 | |
| "room daily rent" total | | |
| duplicate rows | 8,089 | |
| documents missing title_dv | 19,890 | |
| recall@5 | | |
| MRR | | |
```

- [ ] **Step 4** `jj commit -m "P9 task 8: backfills run, measurements recorded"`

---

### Task 9: Details expand in place, never in an overlay

**Files:** Modify `web/src/components/cards/JobCard.tsx`, `PropertyCard.tsx`, `ShoppingCard.tsx`, `web/src/components/ReportDialog.tsx`, `web/src/app/documents/[id]/page.tsx`. Create `web/src/components/Disclosure.tsx`. Test the card test files plus `web/src/components/Disclosure.test.tsx`.

**The rule, stated once so it stops drifting:** when a result has more detail
than fits at a glance, that detail expands **inline, in place**. Never a modal,
never a popover, never a tooltip, never an overlay of any kind.

Two reasons beyond preference. Result sets here are mixed-script with direction
set per element (spec 10), and an overlay has to re-solve direction, focus
trapping and scroll locking in a context the page has already solved. And spec 8's
per-type cards exist so a user can compare listings — an overlay hides that
comparison the moment it opens, while a disclosure keeps the neighbours visible.

**Current state, measured:** only `NewsCard` renders a link, and correctly so —
it is an outbound anchor to the source (spec 8.4). The Job, Property and Shopping
cards link nowhere, so the `/documents/[id]` route P6 built is **unreachable from
the result list**. Qualifications, `apply_methods`, the compensation breakdown and
the spec table are all implemented and invisible.

`JobCard` already gets this right in one place — the take-home assumptions use
`aria-expanded` with a conditional render. This task generalises that pattern and
removes the single overlay in the codebase.

- [ ] **Step 1: Write the failing test**

`web/src/components/Disclosure.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Disclosure } from "./Disclosure";

describe("Disclosure", () => {
  it("is collapsed until asked", () => {
    render(<Disclosure label="More details"><p>hidden thing</p></Disclosure>);
    expect(screen.queryByText("hidden thing")).toBeNull();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
  });

  it("expands in place and reports state", async () => {
    render(<Disclosure label="More details"><p>hidden thing</p></Disclosure>);
    await userEvent.click(screen.getByRole("button", { name: /more details/i }));
    expect(screen.getByText("hidden thing")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("renders no overlay, portal or dialog", () => {
    const { container, baseElement } = render(
      <Disclosure label="More"><p>x</p></Disclosure>
    );
    expect(baseElement.querySelector('[role="dialog"]')).toBeNull();
    expect(baseElement.querySelector("dialog")).toBeNull();
    expect(baseElement.children).toHaveLength(1);   // nothing portalled out
    expect(container.contains(screen.getByRole("button"))).toBe(true);
  });

  it("does not lock scrolling", async () => {
    render(<Disclosure label="More"><p>x</p></Disclosure>);
    await userEvent.click(screen.getByRole("button", { name: /more/i }));
    expect(document.body.style.overflow).toBe("");
  });

  it("labels its content region for assistive technology", async () => {
    render(<Disclosure label="More details"><p>x</p></Disclosure>);
    const btn = screen.getByRole("button");
    await userEvent.click(btn);
    const id = btn.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id!)).toContainElement(screen.getByText("x"));
  });
});
```

Add to each of the three card test files:

```tsx
it("reveals its detail inline rather than navigating or opening an overlay", async () => {
  render(<JobCard result={jobResult} />);
  expect(screen.queryByText(/basic medical degree/i)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: /details/i }));
  expect(screen.getByText(/basic medical degree/i)).toBeInTheDocument();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

it("shows qualifications and apply methods once expanded", async () => {
  render(<JobCard result={jobResult} />);
  await userEvent.click(screen.getByRole("button", { name: /details/i }));
  expect(screen.getByLabelText(/apply via form/i)).toBeInTheDocument();
});
```

`jobResult` in `web/src/test/fixtures.ts` needs `qualifications` and
`apply_methods` on the card payload. P4's `_job_card` currently emits
`apply_kinds` but not the methods themselves, so extend `build_card` in the same
commit — a disclosure over absent data is worse than no disclosure.

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — no `Disclosure`, and no details button on any card.

- [ ] **Step 3: Write the primitive**

`web/src/components/Disclosure.tsx`:

```tsx
"use client";

import { useId, useState, type ReactNode } from "react";

/**
 * Inline progressive disclosure. The only way this project reveals extra detail.
 *
 * Deliberately not a modal, popover or tooltip. Results here are mixed-script
 * with per-element direction (spec 10), so an overlay would re-solve direction,
 * focus trapping and scroll locking that the page has already solved -- and it
 * would hide the neighbouring results, which is the comparison a result list
 * exists to support.
 *
 * A button plus a labelled region rather than <details>/<summary>: the native
 * marker does not flip with `dir` and cannot be styled consistently.
 */
export function Disclosure({
  label,
  children,
  defaultOpen = false,
}: {
  label: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();

  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-xs text-accent
                   underline underline-offset-2"
      >
        {label}
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div id={id} className="mt-2 border-t border-line pt-2">
          {children}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Use it on the three cards**

`JobCard` — `Disclosure label="Details"` containing `qualifications` as a list,
`required_documents` (P5 Task 0B), `CompensationTable` with its working-days
control, and `ApplyBlock`. Those components already exist under
`components/detail/`; import them rather than rewriting.

`PropertyCard` — description, `room_facilities` chips, floor, lift, square feet,
`tenant_preference`, and contacts as tap-to-call and Viber links.

`ShoppingCard` — `SpecTable` over the full spec list including non-facetable keys
(spec 8.3), plus the seller block and contact actions.

`NewsCard` gets **no** disclosure. Spec 8.4 is explicit: icon, title, excerpt,
link out — building a reader for content we do not own helps nobody.

- [ ] **Step 5: Convert the one remaining overlay**

`ReportDialog.tsx` renders `role="dialog" aria-modal="true"`. Replace it with a
`Disclosure label="Report a problem"` holding the same five reasons, the note
field and the same confirmation. Rename to `ReportForm.tsx` so the filename stops
advertising a pattern the project does not use.

The confirmation text stays identical regardless of outcome — the endpoint always
returns 202 and the UI must not leak whether a report was new (spec 9).

- [ ] **Step 6: Keep the route as the canonical URL**

`/documents/[id]` stays: server-rendered for first paint and SEO (spec 10), and
what a shared link resolves to. It renders the same components with
`defaultOpen`. Add a quiet "open" anchor on each card pointing at it — a link,
not the primary interaction.

- [ ] **Step 7: Guard the rule**

Extend `web/src/components/a11y.test.tsx`, which already sweeps every card:

```tsx
it("no component renders a modal, dialog or portal", () => {
  const { baseElement } = renderAll();
  expect(baseElement.querySelector('[role="dialog"]')).toBeNull();
  expect(baseElement.querySelector('[aria-modal="true"]')).toBeNull();
  expect(baseElement.querySelector("dialog")).toBeNull();
});
```

That is what stops this drifting back the next time someone needs to show
something extra.

- [ ] **Step 8: Run**

Run: `cd web && npm test` — expected PASS.

- [ ] **Step 9: Commit**

```bash
jj commit -m "P9 task 9: details expand inline, no overlays"
```

---

## Self-Review

**Coverage.** Every defect observed while running the system against real data
has a task: translation throughput (1), spec 5.5 unimplemented (2), translation
in the wrong place (3), accessories outranking products (4), 40% duplicate rows
(5), keyword stuffing (6), the 0C review findings (7), and the backfill that
never ran (8).

**Ordering constraint that matters.** Task 3 must not precede Task 2. Body
translation is currently the only path from an English query to a Thaana gazette
body; removing it first degrades English recall with nothing failing.

**The general mechanism, which is the real answer to "how do we stop this for
other keywords".** Not a rule per keyword. Every task adds eval cases, and
`eval_search` runs before and after each change with `recall@5 >= 0.80` as a
floor. A relevance fix without an eval case is a fix that regresses the next time
someone tunes a weight.

**What is deliberately not here.** Learning to rank on click data (spec 16.2)
needs ~10,000 clicks and P5's logging has only just shipped. Semantic search
(16.1) would attack the "iPhone cover is not an iPhone" problem more directly
than any lexical fix, and Task 4 is explicitly the cheap interim.
