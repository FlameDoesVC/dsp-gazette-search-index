# P8 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the loop between what users actually search for and what the engine returns — expand the evaluation set from real logs, tune ranking against it, make a zero-result page never a dead end, fill in the missing half of every bilingual field, and keep the database from growing without bound.

**Architecture:** Nothing new is invented here. Every task takes a measurement the earlier phases produced and turns it into a change: P5's zero-result query log becomes the alias table and the relaxation ladder, P2's eval set grows the cases those logs expose, P5's click log becomes the weight-tuning objective, and the archive partition applies the storage projection from spec 12.6. This is the phase where the system stops being a set of correct components and starts being tuned.

**Tech Stack:** Django 6.0.5, PostgreSQL 18, `core.translate`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` — sections 5.5, 6.5, 7, 12.6, 12.7, 14, 16.2, 16.3.

**Depends on:** P5 (logs), P6 (a frontend that shows results), P2 (`search/eval/`), P4 (`core.translate` moved and cached).

**Do not start until the API has been serving real traffic for at least a week.** Four of the six tasks read `QueryLog`, and tuning against an empty table produces a ranking fitted to nothing.

---

## Global Constraints

- **A results page is never empty with no way forward.** Spec 7.
- **Relaxation is transparent.** When the engine drops a term or lowers a threshold, the response says so; silently returning results for a different query than the one asked is worse than returning none.
- **Weights live in `settings.SEARCH_RANKING`**, tunable without a migration. A tuning run changes a setting, never a query. Spec 7.
- **The eval gate does not move to accommodate a regression.** If recall@5 drops below 0.80 the change is wrong, not the threshold. Spec 14.
- **Background translation touches short fields only, never bodies.** Spec 5.5.
- **Translation goes through `TranslationCache`.** One cached call per unique string. Spec 5.5.
- **Archiving never deletes.** An expired listing moves partition; it does not disappear. Spec 12.6.
- **No ranking change ships without an eval run before and after**, both recorded.
- Version control is **jj**, not git.

---

## File Structure

```
search/
  eval/
    queries.yaml                 MODIFIED: grown from real logs
    from_logs.py                 candidate generation out of QueryLog
    harness.py                   MODIFIED: MRR and nDCG from ClickLog
  relax.py                       the zero-result ladder
  query.py                       MODIFIED: relaxation, applied_relaxations
  models.py                      MODIFIED: TermFrequency (already in P5 as SuggestTerm)
  management/commands/
    eval_search.py               MODIFIED: reports MRR/nDCG alongside recall
    tune_ranking.py              coordinate descent over SEARCH_RANKING
    mine_aliases.py              zero-result queries -> QueryAlias candidates
    translate_fields.py          background title and summary translation
    archive_documents.py         move expired rows to the archive partition
api/
  routers/search.py              MODIFIED: surface relaxation + suggestions
  schemas.py                     MODIFIED: RelaxationOut
web/src/
  components/RelaxationNotice.tsx
  components/LangToggle.tsx      deferred from P6
tests/search/test_relax.py, test_tuning.py, test_translate_fields.py,
tests/search/test_archive.py
docs/superpowers/measurements/2026-08-p8-hardening.md
```

---

### Task 1: Grow the evaluation set from real logs

**Files:**
- Create: `search/eval/from_logs.py`
- Modify: `search/eval/queries.yaml`, `search/eval/harness.py`, `search/management/commands/eval_search.py`
- Test: `tests/search/eval/test_from_logs.py`

**Interfaces:**
- Consumes: `search.models.QueryLog`, `ClickLog`.
- Produces: `zero_result_queries(days=30, min_count=2) -> list[dict]`, `clicked_pairs(days=30, min_clicks=2) -> list[dict]`, `propose_cases(...) -> list[dict]`, and `eval_search --propose`.

The P2 eval set was written from the corpus. This one is written from what people actually typed, which is the only source that finds the queries nobody anticipated.

- [ ] **Step 1: Write the failing test**

`tests/search/eval/test_from_logs.py`:

```python
import datetime as dt

import pytest
from django.utils import timezone

from search.eval.from_logs import clicked_pairs, propose_cases, zero_result_queries
from search.models import ClickLog, QueryLog, SearchDocument


def _log(q, *, count=0, days_ago=1, lang="en"):
    log = QueryLog.objects.create(q_raw=q, result_count=count, session_hash="s",
                                  detected_lang=lang, response_lang=lang,
                                  latency_ms=10)
    QueryLog.objects.filter(id=log.id).update(
        created_at=timezone.now() - dt.timedelta(days=days_ago)
    )
    return log


@pytest.mark.django_db
def test_zero_result_queries_are_ranked_by_frequency():
    for _ in range(5):
        _log("kudhin bahattan", count=0)
    for _ in range(2):
        _log("washing machine", count=0)
    _log("iphone", count=12)

    got = zero_result_queries(min_count=2)
    assert [g["q_raw"] for g in got] == ["kudhin bahattan", "washing machine"]
    assert got[0]["n"] == 5


@pytest.mark.django_db
def test_a_one_off_typo_is_below_the_floor():
    _log("ipohne", count=0)
    assert zero_result_queries(min_count=2) == []


@pytest.mark.django_db
def test_the_window_excludes_old_queries():
    for _ in range(5):
        _log("old query", count=0, days_ago=90)
    assert zero_result_queries(days=30, min_count=2) == []


@pytest.mark.django_db
def test_clicked_pairs_become_relevance_judgements():
    """A document clicked repeatedly for one query is the cheapest ground
    truth this system will ever get. Spec 16.2."""
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x",
                                        title_en="iPhone 13")
    for _ in range(3):
        log = _log("iphone", count=10)
        ClickLog.objects.create(query_id=log.id, document_id=doc.id, position=0)

    pairs = clicked_pairs(min_clicks=2)
    assert pairs[0]["q_raw"] == "iphone"
    assert doc.id in pairs[0]["document_ids"]


@pytest.mark.django_db
def test_a_single_click_is_not_a_judgement():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x")
    log = _log("iphone", count=10)
    ClickLog.objects.create(query_id=log.id, document_id=doc.id, position=0)
    assert clicked_pairs(min_clicks=2) == []


@pytest.mark.django_db
def test_propose_cases_emits_yaml_shaped_entries():
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x",
                                        title_en="iPhone 13")
    for _ in range(3):
        log = _log("iphone", count=10)
        ClickLog.objects.create(query_id=log.id, document_id=doc.id, position=0)
    for _ in range(4):
        _log("kudhin bahattan", count=0)

    cases = propose_cases()
    kinds = {c["kind"] for c in cases}
    assert "clicked" in kinds and "zero_result" in kinds
    clicked = next(c for c in cases if c["kind"] == "clicked")
    assert clicked["q"] == "iphone"
    assert clicked["expect_ids"] == [doc.id]
    zero = next(c for c in cases if c["kind"] == "zero_result")
    # A zero-result case has no expected ids yet -- a human decides whether it
    # should have matched something, which is exactly the judgement worth
    # spending a human on.
    assert zero["expect_ids"] == []
    assert zero["needs_review"] is True


@pytest.mark.django_db
def test_proposals_are_deterministic():
    for _ in range(3):
        _log("a query", count=0)
    assert propose_cases() == propose_cases()
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/eval/test_from_logs.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write it**

`search/eval/from_logs.py`:

```python
"""Eval cases mined from real traffic. Spec 14, 16.2, 16.3.

The P2 evaluation set was written from the corpus -- it tests what we knew to
test. This module reads what people actually typed, which is the only source
that finds the queries nobody anticipated.

Two kinds of case come out:

  zero_result  a query that returned nothing. Whether it SHOULD have returned
               something is a human judgement, so these are proposals, not
               assertions.
  clicked      a query where the same document was clicked repeatedly. That is
               the cheapest relevance judgement this system will ever get, and
               it is free.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Count
from django.utils import timezone

from search.models import ClickLog, QueryLog

DEFAULT_WINDOW_DAYS = 30
MIN_ZERO_RESULT_COUNT = 2
MIN_CLICKS = 2


def _since(days: int):
    return timezone.now() - dt.timedelta(days=days)


def zero_result_queries(days: int = DEFAULT_WINDOW_DAYS,
                        min_count: int = MIN_ZERO_RESULT_COUNT) -> list[dict]:
    rows = (
        QueryLog.objects.filter(created_at__gte=_since(days), result_count=0)
        .exclude(q_raw="")
        .values("q_raw", "detected_lang")
        .annotate(n=Count("id"))
        .filter(n__gte=min_count)
        .order_by("-n", "q_raw")
    )
    return [dict(r) for r in rows]


def clicked_pairs(days: int = DEFAULT_WINDOW_DAYS,
                  min_clicks: int = MIN_CLICKS) -> list[dict]:
    rows = (
        ClickLog.objects.filter(created_at__gte=_since(days))
        .values("query__q_raw", "document_id")
        .annotate(n=Count("id"))
        .filter(n__gte=min_clicks)
        .order_by("query__q_raw", "-n")
    )
    grouped: dict[str, list[int]] = {}
    for r in rows:
        grouped.setdefault(r["query__q_raw"], []).append(r["document_id"])
    return [{"q_raw": q, "document_ids": sorted(ids)}
            for q, ids in sorted(grouped.items())]


def propose_cases(days: int = DEFAULT_WINDOW_DAYS) -> list[dict]:
    """Deterministic, so re-running produces a reviewable diff rather than a
    reshuffle."""
    cases: list[dict] = []

    for row in clicked_pairs(days):
        cases.append({
            "kind": "clicked",
            "q": row["q_raw"],
            "expect_ids": row["document_ids"],
            "needs_review": False,
        })

    for row in zero_result_queries(days):
        cases.append({
            "kind": "zero_result",
            "q": row["q_raw"],
            "lang": row["detected_lang"],
            "count": row["n"],
            "expect_ids": [],
            "needs_review": True,
        })

    return sorted(cases, key=lambda c: (c["kind"], c["q"]))
```

- [ ] **Step 4: Extend the harness and command**

Add MRR and nDCG@10 to `search/eval/harness.py` alongside the recall@5 P2 already computes. Both need `position`, which P5 records:

```python
def mrr(ranked_ids: list[int], relevant: set[int]) -> float:
    """Mean reciprocal rank of the first relevant result. Rewards getting the
    right answer to position 1 rather than merely into the top 5."""
    for i, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked_ids: list[int], relevant: set[int], k: int = 10) -> float:
    import math
    dcg = sum(1.0 / math.log2(i + 1)
              for i, doc_id in enumerate(ranked_ids[:k], start=1)
              if doc_id in relevant)
    ideal = sum(1.0 / math.log2(i + 1)
                for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0
```

Add `--propose` to `eval_search`, printing `propose_cases()` as YAML ready to paste into `queries.yaml` after review. It writes nothing automatically — a machine-generated eval set that nobody read is a machine grading its own homework.

- [ ] **Step 5: Review and merge the proposals**

```bash
python manage.py eval_search --propose > /tmp/proposed.yaml
```

Read every entry. For each `zero_result` case, decide one of three things and record which in the YAML comment:

- **should have matched** — add `expect_ids` and it becomes a failing test that ranking or the language pipeline must fix.
- **alias** — the query is a synonym or a transliteration variant; it goes to task 2 instead.
- **genuinely absent** — the corpus does not contain it. Keep the case with `expect_empty: true` so a later change that starts returning junk for it fails.

- [ ] **Step 6: Run the eval**

Run: `python manage.py eval_search`
Expected: recall@5 >= 0.80 on the pre-existing cases; the new cases record a baseline.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P8 task 1: eval set grown from real query logs"
```

---

### Task 2: Alias mining

**Files:**
- Create: `search/management/commands/mine_aliases.py`
- Modify: `search/admin.py`
- Test: `tests/search/test_mine_aliases.py`

**Interfaces:**
- Consumes: `zero_result_queries`, `search.lang` (`translit`, `keymap`, `normalize`), `SuggestTerm`.
- Produces: `propose_aliases(days=30) -> list[dict]`, `mine_aliases --apply`, a `QueryAlias` admin with a source column.

Spec 6.5 gave `QueryAlias` no population mechanism, and spec 16.3 names the zero-result log as its only sensible source. This closes that.

- [ ] **Step 1: Write the failing test**

`tests/search/test_mine_aliases.py`:

```python
import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone

from search.models import QueryAlias, QueryLog, SuggestTerm


def _zero(q, n=3, lang="latin"):
    for _ in range(n):
        log = QueryLog.objects.create(q_raw=q, result_count=0, session_hash="s",
                                      detected_lang=lang, latency_ms=1)
        QueryLog.objects.filter(id=log.id).update(
            created_at=timezone.now() - dt.timedelta(days=1)
        )


@pytest.mark.django_db
def test_a_near_miss_against_the_term_table_becomes_an_alias_proposal():
    """'ipohne' returned nothing and is one transposition from a term that
    appears 200 times. That is an alias, not a gap in the corpus."""
    from search.management.commands.mine_aliases import propose_aliases
    SuggestTerm.objects.create(term="iphone", frequency=200, script="latin",
                               doc_type="shopping")
    _zero("ipohne")

    proposals = propose_aliases()
    assert proposals[0]["alias"] == "ipohne"
    assert proposals[0]["canonical"] == "iphone"
    assert proposals[0]["source"] == "trigram"


@pytest.mark.django_db
def test_a_transliteration_variant_is_proposed_against_its_thaana_form():
    from search.management.commands.mine_aliases import propose_aliases
    SuggestTerm.objects.create(term="ވަޒީފާ", frequency=300, script="thaana",
                               doc_type="job")
    _zero("vazeefa")

    proposals = propose_aliases()
    assert any(p["canonical"] == "ވަޒީފާ" and p["source"] == "translit"
               for p in proposals)


@pytest.mark.django_db
def test_a_query_with_no_plausible_canonical_is_not_proposed():
    from search.management.commands.mine_aliases import propose_aliases
    SuggestTerm.objects.create(term="iphone", frequency=200, script="latin")
    _zero("qwertyuiop")
    assert all(p["alias"] != "qwertyuiop" for p in propose_aliases())


@pytest.mark.django_db
def test_proposals_are_not_applied_without_the_flag():
    SuggestTerm.objects.create(term="iphone", frequency=200, script="latin")
    _zero("ipohne")
    call_command("mine_aliases")
    assert QueryAlias.objects.count() == 0


@pytest.mark.django_db
def test_apply_creates_aliases_and_is_idempotent():
    SuggestTerm.objects.create(term="iphone", frequency=200, script="latin")
    _zero("ipohne")
    call_command("mine_aliases", "--apply")
    assert QueryAlias.objects.filter(alias="ipohne").exists()
    call_command("mine_aliases", "--apply")
    assert QueryAlias.objects.filter(alias="ipohne").count() == 1


@pytest.mark.django_db
def test_an_existing_alias_is_never_overwritten():
    """A curated alias outranks a mined one. A human decided; the miner is
    guessing."""
    QueryAlias.objects.create(alias="ipohne", canonical="hand-curated")
    SuggestTerm.objects.create(term="iphone", frequency=200, script="latin")
    _zero("ipohne")
    call_command("mine_aliases", "--apply")
    assert QueryAlias.objects.get(alias="ipohne").canonical == "hand-curated"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_mine_aliases.py -v`
Expected: FAIL.

- [ ] **Step 3: Write the miner**

`search/management/commands/mine_aliases.py`:

```python
"""Turn zero-result queries into QueryAlias rows. Spec 6.5, 16.3.

Spec 6.5 defines QueryAlias but never says where rows come from, and 16.3
identifies the zero-result log as the only sensible source. A query that
returned nothing and sits one edit away from a term appearing 200 times is a
spelling variant, not a gap in the corpus.

Three signals, in confidence order:

  translit  the query transliterates to a term that exists. Strongest: the
            Latin-Dhivehi and Thaana forms of one word are the same word.
  keymap    the query decodes from the Thaana keyboard layout to a real term.
  trigram   the query is close to a frequent term by similarity. Weakest, and
            the one that needs review before it is applied.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection

from search.eval.from_logs import zero_result_queries
from search.lang import keymap, normalize, translit
from search.models import QueryAlias, SuggestTerm

TRIGRAM_FLOOR = 0.55
MIN_CANONICAL_FREQUENCY = 5


def _best_trigram_match(q: str) -> tuple[str, float] | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT term, similarity(term, %s) AS sim FROM search_suggestterm "
            "WHERE term %% %s AND frequency >= %s "
            "ORDER BY sim DESC, frequency DESC LIMIT 1",
            [q, q, MIN_CANONICAL_FREQUENCY],
        )
        row = cur.fetchone()
    if not row or row[1] < TRIGRAM_FLOOR:
        return None
    return row[0], float(row[1])


def _exists(term: str) -> bool:
    return SuggestTerm.objects.filter(
        term=term, frequency__gte=MIN_CANONICAL_FREQUENCY
    ).exists()


def propose_aliases(days: int = 30) -> list[dict]:
    proposals: list[dict] = []

    for row in zero_result_queries(days=days):
        q = normalize.normalize_text(row["q_raw"]).lower()
        if not q:
            continue

        # 1. transliteration: Latin-Dhivehi -> Thaana
        for candidate in translit.translit_latin_to_dv(q):
            if _exists(candidate):
                proposals.append({"alias": row["q_raw"], "canonical": candidate,
                                  "source": "translit", "confidence": 0.9,
                                  "n": row["n"]})
                break
        else:
            # 2. keyboard layout: 'migotawq' -> 'މިގޮތައް'
            decoded = keymap.decode_keys(q) if keymap.looks_like_keys(q) else ""
            if decoded and _exists(decoded):
                proposals.append({"alias": row["q_raw"], "canonical": decoded,
                                  "source": "keymap", "confidence": 0.85,
                                  "n": row["n"]})
                continue

            # 3. trigram near-miss
            match = _best_trigram_match(q)
            if match:
                proposals.append({"alias": row["q_raw"], "canonical": match[0],
                                  "source": "trigram", "confidence": match[1],
                                  "n": row["n"]})

    return sorted(proposals, key=lambda p: (-p["n"], p["alias"]))


class Command(BaseCommand):
    help = "Propose QueryAlias rows from zero-result queries."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--apply", action="store_true",
                            help="Create the rows. Without this, prints only.")
        parser.add_argument("--min-confidence", type=float, default=0.0)

    def handle(self, *args, **opts):
        proposals = [p for p in propose_aliases(opts["days"])
                     if p["confidence"] >= opts["min_confidence"]]

        for p in proposals:
            self.stdout.write(
                f"{p['n']:>4}x  {p['alias']!r} -> {p['canonical']!r}  "
                f"({p['source']}, {p['confidence']:.2f})"
            )

        if not opts["apply"]:
            self.stdout.write(
                self.style.WARNING(f"{len(proposals)} proposals, none applied. "
                                   f"Re-run with --apply.")
            )
            return

        created = 0
        for p in proposals:
            # get_or_create: a hand-curated alias outranks a mined one.
            _, was_created = QueryAlias.objects.get_or_create(
                alias=p["alias"],
                defaults={"canonical": p["canonical"], "source": p["source"]},
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f"{created} aliases created"))
```

`QueryAlias` gains a `source` field (`manual | translit | keymap | trigram`) so the admin can tell curated rows from mined ones. Add it with a migration defaulting to `manual`.

- [ ] **Step 4: Run the tests, then mine for real**

```bash
pytest tests/search/test_mine_aliases.py -v
python manage.py mine_aliases --days 30
# read the output, then:
python manage.py mine_aliases --days 30 --apply --min-confidence 0.8
python manage.py eval_search
```

The eval run afterwards is the point: aliases that improve recall keep, ones that do not get deleted in the admin.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P8 task 2: alias mining from zero-result queries"
```

---

### Task 3: Zero-result relaxation

**Files:**
- Create: `search/relax.py`, `web/src/components/RelaxationNotice.tsx`
- Modify: `search/query.py`, `api/routers/search.py`, `api/schemas.py`
- Test: `tests/search/test_relax.py`, `tests/api/test_relax.py`

**Interfaces:**
- Produces: `RelaxationStep`, `relaxation_ladder(plan) -> list[RelaxationStep]`, `search_page(..., relax=True)` returning `SearchPage.relaxations: list[dict]`, `RelaxationOut` in the API response.

Three rungs, in order: drop the rarest term, lower the trigram threshold, suggest alternatives from the term table. Each is applied only if the one before it returned nothing.

- [ ] **Step 1: Write the failing test**

`tests/search/test_relax.py`:

```python
import pytest
from django.core.management import call_command

from search.models import SearchDocument, SuggestTerm
from search.query import search_page


@pytest.fixture
def corpus(db):
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  title_en="Samsung washing machine",
                                  summary_en="A front-load washing machine.")
    SearchDocument.objects.create(source="ibay", source_key="2",
                                  doc_type="shopping", url="https://x",
                                  title_en="Washing machine drum belt")
    SuggestTerm.objects.create(term="washing", frequency=40, script="latin",
                               doc_type="shopping")
    SuggestTerm.objects.create(term="machine", frequency=38, script="latin",
                               doc_type="shopping")
    call_command("reindex_vectors")


@pytest.mark.django_db
def test_a_matching_query_is_not_relaxed(corpus):
    page = search_page("washing machine", relax=True)
    assert page.results
    assert page.relaxations == []


@pytest.mark.django_db
def test_the_rarest_term_is_dropped_first(corpus):
    """'zanussi' appears nowhere; 'washing machine' does. Dropping the rarest
    term recovers two results instead of returning none."""
    page = search_page("zanussi washing machine", relax=True)
    assert page.results
    assert page.relaxations[0]["kind"] == "dropped_term"
    assert page.relaxations[0]["term"] == "zanussi"


@pytest.mark.django_db
def test_relaxation_is_reported_not_silent(corpus):
    """Silently answering a different question than the one asked is worse
    than returning nothing."""
    page = search_page("zanussi washing machine", relax=True)
    assert page.relaxations
    assert page.relaxations[0]["applied"] is True


@pytest.mark.django_db
def test_the_trigram_threshold_drops_when_dropping_terms_does_not_help(corpus):
    page = search_page("wshing mchine", relax=True)
    kinds = [r["kind"] for r in page.relaxations]
    assert "lowered_trigram" in kinds
    assert page.results


@pytest.mark.django_db
def test_suggestions_are_offered_when_nothing_recovers_results(corpus):
    page = search_page("qwertyuiop asdfghjkl", relax=True)
    assert page.results == []
    assert page.suggestions
    assert page.relaxations[-1]["kind"] == "suggested"


@pytest.mark.django_db
def test_relaxation_stops_as_soon_as_it_has_results(corpus):
    page = search_page("zanussi washing machine", relax=True)
    assert len(page.relaxations) == 1     # never went past rung 1


@pytest.mark.django_db
def test_relaxation_is_off_by_default(corpus):
    """The eval harness must measure the unrelaxed engine, or a ranking
    regression hides behind the safety net."""
    page = search_page("zanussi washing machine")
    assert page.results == []
    assert page.relaxations == []


@pytest.mark.django_db
def test_a_single_term_query_is_never_reduced_to_nothing(corpus):
    page = search_page("zanussi", relax=True)
    assert all(r["kind"] != "dropped_term" for r in page.relaxations)


@pytest.mark.django_db
def test_filters_are_never_relaxed(corpus):
    """A user who ticked 'Used' does not want New results. Relax the query,
    never the constraints."""
    from search.filters import parse_filters
    fs = parse_filters(["condition:Nonexistent"], "shopping")
    page = search_page("washing machine", doc_type="shopping", filters=fs,
                       relax=True)
    assert page.results == []
    assert all(r["kind"] != "dropped_filter" for r in page.relaxations)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_relax.py -v`
Expected: FAIL — `search_page() got an unexpected keyword argument 'relax'`.

- [ ] **Step 3: Write the ladder**

`search/relax.py`:

```python
"""Progressive relaxation for zero-result queries. Spec 7.

Three rungs, applied in order and only while the result set is still empty:

  1. drop the rarest term      -- 'zanussi washing machine' has no Zanussi in
                                  the corpus, but it does have washing machines
  2. lower the trigram floor   -- rescues 'wshing mchine' without loosening the
                                  threshold for queries that already work
  3. suggest alternatives      -- when nothing recovers results, the page still
                                  has a way forward

Two things are deliberately never relaxed:

  - filters. A user who ticked 'Used' does not want New results. Relax the
    query, never the constraints.
  - the last remaining term. Dropping it would return the whole corpus, which
    looks like a working search and is not one.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import connection

RELAXED_TRIGRAM_FLOOR = 0.15
MAX_SUGGESTIONS = 5


@dataclass(slots=True)
class RelaxationStep:
    kind: str            # dropped_term | lowered_trigram | suggested
    detail: dict

    def as_dict(self) -> dict:
        return {"kind": self.kind, "applied": True, **self.detail}


def term_frequencies(terms: list[str]) -> dict[str, int]:
    """Corpus frequency per term, from the suggest table P5 already builds."""
    if not terms:
        return {}
    with connection.cursor() as cur:
        cur.execute(
            "SELECT term, frequency FROM search_suggestterm WHERE term = ANY(%s)",
            [terms],
        )
        found = dict(cur.fetchall())
    return {t: found.get(t, 0) for t in terms}


def rarest_term(terms: list[str]) -> str | None:
    """The term to drop: lowest corpus frequency, ties broken by longest.

    Frequency zero means the term is in no document at all, which is exactly
    the term making the whole conjunction empty.
    """
    if len(terms) < 2:
        return None
    freq = term_frequencies(terms)
    return min(terms, key=lambda t: (freq.get(t, 0), -len(t)))


def suggestions_for(terms: list[str], limit: int = MAX_SUGGESTIONS) -> list[str]:
    if not terms:
        return []
    with connection.cursor() as cur:
        cur.execute(
            "SELECT term FROM search_suggestterm "
            "WHERE term %% ANY(%s) "
            "ORDER BY frequency DESC LIMIT %s",
            [terms, limit],
        )
        return [r[0] for r in cur.fetchall()]
```

- [ ] **Step 4: Wire it into `search_page`**

Add `relax: bool = False` to `search_page` and `relaxations: list[dict]` plus `suggestions: list[str]` to `SearchPage`. After the first query returns empty:

```python
    if relax and not results:
        from search import relax as relax_mod

        # Rung 1: drop the rarest term. Only when there is more than one, and
        # only on the language track that actually produced terms.
        all_terms = plan.terms_en + plan.terms_dv + plan.terms_latin
        victim = relax_mod.rarest_term(all_terms)
        if victim:
            reduced = _plan_without(plan, victim)
            rows = _execute_page(reduced, doc_type, filters, sort, page,
                                 per_page, candidate_limit)
            relaxations.append(
                relax_mod.RelaxationStep("dropped_term",
                                         {"term": victim}).as_dict()
            )
            results = [_to_result(r, reduced) for r in rows]
            total = rows[0][-1] if rows else 0

        # Rung 2: lower the trigram floor. Applied per-request via
        # set_config so it never leaks into another connection's queries.
        if not results:
            with connection.cursor() as cur:
                cur.execute("SELECT set_limit(%s)",
                            [relax_mod.RELAXED_TRIGRAM_FLOOR])
            rows = _execute_page(plan, doc_type, filters, sort, page, per_page,
                                 candidate_limit)
            relaxations.append(
                relax_mod.RelaxationStep(
                    "lowered_trigram",
                    {"threshold": relax_mod.RELAXED_TRIGRAM_FLOOR}
                ).as_dict()
            )
            results = [_to_result(r, plan) for r in rows]
            total = rows[0][-1] if rows else 0

        # Rung 3: nothing worked. Offer a way forward rather than a dead end.
        if not results:
            suggestions = relax_mod.suggestions_for(all_terms)
            relaxations.append(
                relax_mod.RelaxationStep("suggested",
                                         {"terms": suggestions}).as_dict()
            )
```

`set_limit` is session-scoped in Postgres and the connection is pooled, so reset it in a `finally`: `cur.execute("SELECT set_limit(%s)", [settings.SEARCH_RANKING["trigram_floor"]])`. Getting this wrong leaks a loose threshold into every subsequent request on that connection, which is a slow, invisible relevance regression — write a test that runs a relaxed query followed by a normal one on the same connection and asserts the normal one is unaffected.

Then in `api/routers/search.py`, pass `relax=True` (the API always wants the safety net; the eval harness never does) and surface `relaxations` and `suggestions` in the response. Add `RelaxationOut` to `api/schemas.py` and a `relaxations: list[RelaxationOut]` field on `SearchOut`.

- [ ] **Step 5: Surface it in the UI**

`web/src/components/RelaxationNotice.tsx` renders above the results:

- `dropped_term` → "No results for **zanussi washing machine**. Showing results for **washing machine**." with the dropped term as a link back to the strict query.
- `lowered_trigram` → "Showing close matches."
- `suggested` → "No results. Did you mean: ..." as clickable suggestions.

Add it to `SearchShell` above `ResultList`.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/search/test_relax.py tests/api/ -v && cd web && npm test`
Expected: PASS. Also re-run `python manage.py eval_search` — relaxation is off there by design, so the numbers must be unchanged.

- [ ] **Step 7: Commit**

```bash
jj commit -m "P8 task 3: zero-result progressive relaxation"
```

---

### Task 4: Ranking tuning against the eval set

**Files:**
- Create: `search/management/commands/tune_ranking.py`
- Test: `tests/search/test_tuning.py`

**Interfaces:**
- Produces: `evaluate(weights) -> dict`, `coordinate_descent(initial, grid, rounds=2) -> tuple[dict, dict]`, `tune_ranking --apply`.

Spec 16.2 names this as the cheap intermediate step before a learned model: tune the static weights against measured outcomes, which needs far less data and carries far less risk of a feedback loop that entrenches whatever ranked first on day one.

- [ ] **Step 1: Write the failing test**

`tests/search/test_tuning.py`:

```python
import pytest

from search.tuning import coordinate_descent, weight_grid


def test_the_grid_covers_each_tunable_weight():
    grid = weight_grid()
    assert {"w_en", "w_dv", "w_latin", "w_trigram", "w_same_lang",
            "w_freshness", "w_quality"} <= set(grid)
    for key, values in grid.items():
        assert len(values) >= 3, key
        assert all(v >= 0 for v in values), key


def test_coordinate_descent_finds_a_known_optimum():
    """A synthetic objective with a single peak, so the search itself is
    tested independently of any search-quality question."""
    target = {"a": 3, "b": 7}

    def objective(w):
        return -abs(w["a"] - target["a"]) - abs(w["b"] - target["b"])

    best, report = coordinate_descent(
        {"a": 0, "b": 0},
        {"a": [0, 1, 2, 3, 4], "b": [0, 5, 7, 9]},
        objective=objective,
        rounds=3,
    )
    assert best == target
    assert report["rounds"] >= 1
    assert report["improved"] is True


def test_coordinate_descent_reports_no_improvement_rather_than_thrashing():
    def objective(_w):
        return 0.5

    best, report = coordinate_descent({"a": 1}, {"a": [0, 1, 2]},
                                      objective=objective, rounds=2)
    assert best == {"a": 1}
    assert report["improved"] is False


def test_the_objective_combines_recall_mrr_and_ndcg():
    from search.tuning import combined_score
    a = combined_score({"recall_at_5": 0.9, "mrr": 0.8, "ndcg_at_10": 0.85})
    b = combined_score({"recall_at_5": 0.9, "mrr": 0.4, "ndcg_at_10": 0.85})
    assert a > b, "MRR must matter, or the tuner ignores position 1"


def test_a_candidate_below_the_recall_gate_is_rejected_outright():
    """Spec 14: the gate does not move to accommodate a regression."""
    from search.tuning import combined_score
    assert combined_score({"recall_at_5": 0.6, "mrr": 1.0,
                           "ndcg_at_10": 1.0}) == float("-inf")


@pytest.mark.django_db
def test_tune_ranking_does_not_write_settings_without_apply(capsys):
    from django.conf import settings
    from django.core.management import call_command

    before = dict(settings.SEARCH_RANKING)
    call_command("tune_ranking", "--rounds", "1")
    assert settings.SEARCH_RANKING == before
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_tuning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.tuning'`.

- [ ] **Step 3: Write it**

`search/tuning.py`:

```python
"""Static weight tuning. Spec 7, 16.2.

Coordinate descent rather than anything cleverer: there are seven weights, the
objective is one eval run each, and the search space is small enough that a
grid over one dimension at a time converges in two passes. A learned model
needs ~10,000 clicks (spec 16.2); this needs the eval set that already exists.

The recall gate is a hard floor, not a term in the objective. A weight set that
finds fewer right answers is not a better weight set no matter how well it
ranks the ones it finds.
"""

from __future__ import annotations

import copy
import itertools

RECALL_GATE = 0.80


def weight_grid() -> dict[str, list[float]]:
    return {
        "w_en": [0.5, 1.0, 1.5, 2.0],
        "w_dv": [0.5, 1.0, 1.5, 2.0],
        "w_latin": [0.25, 0.5, 1.0, 1.5],
        "w_trigram": [0.1, 0.25, 0.5, 1.0],
        "w_same_lang": [0.0, 0.25, 0.5, 1.0],
        "w_freshness": [0.0, 0.25, 0.5, 1.0],
        "w_quality": [0.0, 0.1, 0.25, 0.5],
    }


def combined_score(metrics: dict) -> float:
    """One number to maximize. Recall is a gate; MRR and nDCG are the score.

    MRR is weighted highest because 'the right answer is somewhere in the top
    five' and 'the right answer is first' are very different experiences, and
    only MRR distinguishes them.
    """
    if metrics.get("recall_at_5", 0.0) < RECALL_GATE:
        return float("-inf")
    return (0.5 * metrics.get("mrr", 0.0)
            + 0.3 * metrics.get("ndcg_at_10", 0.0)
            + 0.2 * metrics.get("recall_at_5", 0.0))


def coordinate_descent(initial: dict, grid: dict, *, objective, rounds: int = 2):
    """Walk one dimension at a time, keeping any improvement."""
    best = dict(initial)
    best_score = objective(best)
    start_score = best_score
    evaluations = 1

    for round_n in range(rounds):
        improved_this_round = False
        for key, values in grid.items():
            for value in values:
                if best.get(key) == value:
                    continue
                candidate = {**best, key: value}
                s = objective(candidate)
                evaluations += 1
                if s > best_score:
                    best, best_score = candidate, s
                    improved_this_round = True
        if not improved_this_round:
            break

    return best, {
        "rounds": round_n + 1,
        "evaluations": evaluations,
        "start_score": start_score,
        "best_score": best_score,
        "improved": best_score > start_score,
    }
```

`search/management/commands/tune_ranking.py`:

```python
"""manage.py tune_ranking [--rounds 2] [--apply]

Prints a settings block. Never writes one without --apply, and even then it
writes to a file the operator pastes -- a command that silently edits ranking
is a command that makes an unexplained relevance change on a Tuesday.
"""

from __future__ import annotations

import copy
import json

from django.conf import settings
from django.core.management.base import BaseCommand

from search.eval.harness import run_eval
from search.tuning import combined_score, coordinate_descent, weight_grid


class Command(BaseCommand):
    help = "Tune settings.SEARCH_RANKING against the evaluation set."

    def add_arguments(self, parser):
        parser.add_argument("--rounds", type=int, default=2)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--out", default="ranking_tuned.json")

    def handle(self, *args, **opts):
        base = copy.deepcopy(settings.SEARCH_RANKING)
        grid = weight_grid()
        tunable = {k: base[k] for k in grid}

        def objective(weights):
            merged = {**base, **weights}
            original = settings.SEARCH_RANKING
            settings.SEARCH_RANKING = merged
            try:
                return combined_score(run_eval())
            finally:
                settings.SEARCH_RANKING = original

        before = run_eval()
        self.stdout.write(f"before: {json.dumps(before, sort_keys=True)}")

        best, report = coordinate_descent(tunable, grid, objective=objective,
                                          rounds=opts["rounds"])

        settings.SEARCH_RANKING = {**base, **best}
        after = run_eval()
        settings.SEARCH_RANKING = base

        self.stdout.write(f"after:  {json.dumps(after, sort_keys=True)}")
        self.stdout.write(f"{report['evaluations']} evaluations, "
                          f"improved={report['improved']}")

        if not report["improved"]:
            self.stdout.write(self.style.WARNING(
                "No improvement found. Current weights kept."
            ))
            return

        block = json.dumps({**base, **best}, indent=4, sort_keys=True)
        self.stdout.write("\nSEARCH_RANKING = " + block)
        if opts["apply"]:
            with open(opts["out"], "w") as fh:
                fh.write(block)
            self.stdout.write(self.style.SUCCESS(
                f"written to {opts['out']}. Paste into settings.py and re-run "
                f"eval_search to confirm."
            ))
```

- [ ] **Step 4: Run it and record both numbers**

```bash
python manage.py eval_search                    # before
python manage.py tune_ranking --rounds 2 --apply
# paste the block into settings.py
python manage.py eval_search                    # after
```

Both go in the measurements file. A tuning run with no recorded before-number is not a tuning run.

- [ ] **Step 5: Commit**

```bash
jj commit -m "P8 task 4: ranking tuning against the eval set"
```

---

### Task 5: Ongoing translation maintenance

> **The initial backfill moved to P5 Task 0C.** The frontend cannot render a
> Dhivehi result page without it, so it is a prerequisite rather than
> hardening. Task 0C also carries the measured evidence that transliteration
> is the wrong tool for this and the `route_bilingual` invariant that stops
> the fields being filled by source assumption. What remains here is the
> recurring pass over newly-ingested documents; the command, its tests and the
> failure-mode handling are all specified in Task 0C and are not repeated.
>
> Keep from this task: the weekly cadence in the runbook, and the language
> toggle in step 4 below.

**Files:**
- Create: `search/management/commands/translate_fields.py`
- Test: `tests/search/test_translate_fields.py`

**Interfaces:**
- Consumes: `core.translate.translate_auto`, `TranslationCache`.
- Produces: `missing_translations(source=None, limit=None) -> Iterator[SearchDocument]`, `translate_document(doc) -> int`, `translate_fields --source --limit --dry-run`.

Every result already resolves its title to the response language with a fallback, but a Dhivehi user reading an English-only title is reading a fallback. This fills the other half — short fields only, never bodies.

- [ ] **Step 1: Write the failing test**

`tests/search/test_translate_fields.py`:

```python
import pytest
from django.core.management import call_command

from search.models import SearchDocument


@pytest.fixture(autouse=True)
def stub_translate(monkeypatch):
    calls = []

    def fake(text, target_lang, **kw):
        calls.append((text, target_lang))
        return f"[{target_lang}] {text}"

    monkeypatch.setattr("core.translate.translate_auto", fake)
    return calls


@pytest.mark.django_db
def test_a_missing_dhivehi_title_is_filled(stub_translate):
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  title_en="Washing machine", title_dv="")
    call_command("translate_fields", "--source", "ibay")
    doc = SearchDocument.objects.get()
    assert doc.title_dv.startswith("[dv]")


@pytest.mark.django_db
def test_a_missing_english_title_is_filled(stub_translate):
    SearchDocument.objects.create(source="gazette", source_key="1",
                                  doc_type="news", url="https://x",
                                  title_dv="ވަޒީފާގެ ފުރުޞަތު", title_en="")
    call_command("translate_fields", "--source", "gazette")
    assert SearchDocument.objects.get().title_en.startswith("[en]")


@pytest.mark.django_db
def test_an_existing_translation_is_never_overwritten(stub_translate):
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  title_en="Washing machine",
                                  title_dv="ފެންމެޝިން")
    call_command("translate_fields", "--source", "ibay")
    assert SearchDocument.objects.get().title_dv == "ފެންމެޝިން"
    assert stub_translate == []


@pytest.mark.django_db
def test_body_text_is_never_sent(stub_translate):
    """Spec 5.5: short fields only, never bodies. A gazette body averages
    5,569 characters and there are 51,000 of them."""
    SearchDocument.objects.create(source="gazette", source_key="1",
                                  doc_type="news", url="https://x",
                                  title_dv="ޚަބަރު",
                                  summary_dv="ސަރުކާރުން ބީލަން ހުޅުވާލައިފި")
    call_command("translate_fields", "--source", "gazette")
    for text, _ in stub_translate:
        assert len(text) <= 512


@pytest.mark.django_db
def test_the_vectors_are_rebuilt_so_the_new_text_is_searchable(stub_translate):
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  title_en="Washing machine")
    call_command("translate_fields", "--source", "ibay")
    doc = SearchDocument.objects.get()
    assert doc.vector_dv is not None


@pytest.mark.django_db
def test_dry_run_reports_the_count_and_calls_nothing(stub_translate, capsys):
    for i in range(4):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      title_en="Thing")
    call_command("translate_fields", "--source", "ibay", "--dry-run")
    assert "4" in capsys.readouterr().out
    assert stub_translate == []


@pytest.mark.django_db
def test_a_translation_failure_skips_the_document_and_continues(monkeypatch):
    def boom(text, target_lang, **kw):
        if "Bad" in text:
            raise RuntimeError("provider down")
        return f"[{target_lang}] {text}"

    monkeypatch.setattr("core.translate.translate_auto", boom)
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  title_en="Bad one")
    SearchDocument.objects.create(source="ibay", source_key="2",
                                  doc_type="shopping", url="https://x",
                                  title_en="Good one")
    call_command("translate_fields", "--source", "ibay")
    assert SearchDocument.objects.get(source_key="2").title_dv.startswith("[dv]")
    assert SearchDocument.objects.get(source_key="1").title_dv == ""
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_translate_fields.py -v`
Expected: FAIL.

- [ ] **Step 3: Write it**

`search/management/commands/translate_fields.py`:

```python
"""Fill the missing half of every bilingual short field. Spec 5.5.

Short fields only -- titles and summaries, capped at 512 characters. Never
bodies: a gazette body averages 5,569 characters and there are 51,000 of them,
which is a different order of cost and buys nothing the vectors do not already
have.

Every call goes through core.translate, which is cached in TranslationCache, so
re-running is nearly free and duplicate titles across listings cost one call.
"""

from __future__ import annotations

import logging

from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand
from django.db.models import Q

from search.models import SearchDocument

logger = logging.getLogger(__name__)

MAX_FIELD_CHARS = 512
PAIRS = [
    ("title_en", "title_dv", "dv"),
    ("title_dv", "title_en", "en"),
    ("summary_en", "summary_dv", "dv"),
    ("summary_dv", "summary_en", "en"),
]


def missing_translations(source=None, limit=None):
    qs = SearchDocument.objects.using("direct")
    if source:
        qs = qs.filter(source=source)
    qs = qs.filter(
        (Q(title_en="") & ~Q(title_dv="")) | (Q(title_dv="") & ~Q(title_en=""))
        | (Q(summary_en="") & ~Q(summary_dv="")) | (Q(summary_dv="") & ~Q(summary_en=""))
    ).only("id", "title_en", "title_dv", "summary_en", "summary_dv")
    if limit:
        qs = qs[:limit]
    return qs


def translate_document(doc: SearchDocument) -> int:
    from core.translate import translate_auto

    filled = 0
    for src_field, dst_field, target in PAIRS:
        src = getattr(doc, src_field)
        if not src or getattr(doc, dst_field):
            continue
        try:
            out = translate_auto(src[:MAX_FIELD_CHARS], target)
        except Exception:
            # One bad document must not stop a 20,000-row pass.
            logger.warning("translation failed for document %s field %s",
                           doc.id, src_field, exc_info=True)
            continue
        if out:
            setattr(doc, dst_field, out[:512])
            filled += 1
    return filled


class Command(BaseCommand):
    help = "Fill missing title_dv / title_en / summary_* fields."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--batch-size", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        qs = missing_translations(opts["source"], opts["limit"])

        if opts["dry_run"]:
            self.stdout.write(f"{qs.count()} documents have a missing translation")
            return

        written, batch = 0, []
        for doc in qs.iterator(chunk_size=opts["batch_size"]):
            if translate_document(doc):
                batch.append(doc)
            if len(batch) >= opts["batch_size"]:
                written += self._flush(batch)
                batch = []
        if batch:
            written += self._flush(batch)

        self.stdout.write(self.style.SUCCESS(f"{written} documents updated"))

    def _flush(self, batch) -> int:
        SearchDocument.objects.bulk_update(
            batch, ["title_en", "title_dv", "summary_en", "summary_dv"],
            batch_size=200,
        )
        # New text that is not in a vector is not searchable, which would make
        # this whole pass invisible.
        ids = [d.id for d in batch]
        SearchDocument.objects.filter(id__in=ids).update(
            vector_en=(SearchVector("title_en", weight="A", config="english")
                       + SearchVector("summary_en", weight="B", config="english")),
            vector_dv=(SearchVector("title_dv", weight="A", config="simple")
                       + SearchVector("summary_dv", weight="B", config="simple")),
        )
        return len(batch)
```

`vector_dv` in P2 is built with the dual/fili/skeleton expressions, not a plain `SearchVector`. Import P2's `_rebuild_vectors` from `search.indexing` and call it here instead of duplicating the expression — if the two drift, Dhivehi search silently degrades for exactly the documents this command touched. Replace the `.update()` above with `_rebuild_vectors(batch)`.

- [ ] **Step 4: Add the UI language toggle**

Deferred from P6. `web/src/components/LangToggle.tsx`: two buttons writing `lang` into the URL and into `localStorage`; `SearchShell` reads it and passes `lang` to `getSearch`. The query language still sets the default — the toggle only overrides it, and persists. Spec 10.

- [ ] **Step 5: Run and commit**

```bash
pytest tests/search/test_translate_fields.py -v
python manage.py translate_fields --dry-run
python manage.py translate_fields --source gazette --limit 500   # sample first
python manage.py translate_fields
jj commit -m "P8 task 5: background field translation and language toggle"
```

---

### Task 6: Archive partitioning

**Files:**
- Create: `search/management/commands/archive_documents.py`, a migration adding the archive partition
- Test: `tests/search/test_archive.py`

**Interfaces:**
- Produces: `archive_candidates(days=365) -> QuerySet`, `archive_documents --days --dry-run`, the `search_searchdocument_archive` partition.

Spec 12.6 projects ~3.2 GB at the full archive and 670 MB at 51,000 iulaan. The working set is what has to stay in `shared_buffers` on a 4 GB box, and an expired 2019 listing competing for that memory with today's results is the specific problem this solves.

- [ ] **Step 1: Write the failing test**

`tests/search/test_archive.py`:

```python
import datetime as dt

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from search.models import SearchDocument
from search.query import search_page


def _partitions():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = 'search_searchdocument' ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


@pytest.mark.django_db
def test_an_archive_partition_exists():
    assert "search_searchdocument_archive" in _partitions()


@pytest.mark.django_db
def test_an_expired_old_listing_is_a_candidate():
    old = timezone.now() - dt.timedelta(days=800)
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  published_at=old, expires_at=old,
                                  is_active=False)
    from search.archive import archive_candidates
    assert archive_candidates(days=365).count() == 1


@pytest.mark.django_db
def test_an_active_listing_is_never_a_candidate_however_old():
    old = timezone.now() - dt.timedelta(days=800)
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  published_at=old, is_active=True)
    from search.archive import archive_candidates
    assert archive_candidates(days=365).count() == 0


@pytest.mark.django_db
def test_gazette_documents_are_never_archived():
    """A published government notice does not expire, and the gazette
    partition is the write-once set whose lack of churn is a structural
    benefit (spec 5.7). Moving rows out of it would create churn for nothing."""
    old = timezone.now() - dt.timedelta(days=3000)
    SearchDocument.objects.create(source="gazette", source_key="IUL-1",
                                  doc_type="news", url="https://x",
                                  published_at=old, is_active=False)
    from search.archive import archive_candidates
    assert archive_candidates(days=365).count() == 0


@pytest.mark.django_db
def test_archiving_moves_the_row_and_does_not_delete_it():
    old = timezone.now() - dt.timedelta(days=800)
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  published_at=old, expires_at=old,
                                  is_active=False)
    call_command("archive_documents", "--days", "365")
    assert SearchDocument.objects.count() == 1

    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM search_searchdocument_archive")
        assert cur.fetchone()[0] == 1


@pytest.mark.django_db
def test_an_archived_document_is_still_retrievable_by_id():
    old = timezone.now() - dt.timedelta(days=800)
    doc = SearchDocument.objects.create(source="ibay", source_key="1",
                                        doc_type="shopping", url="https://x",
                                        published_at=old, expires_at=old,
                                        is_active=False)
    call_command("archive_documents", "--days", "365")
    assert SearchDocument.objects.filter(id=doc.id).exists()


@pytest.mark.django_db
def test_archived_documents_do_not_appear_in_search():
    old = timezone.now() - dt.timedelta(days=800)
    SearchDocument.objects.create(source="ibay", source_key="1",
                                  doc_type="shopping", url="https://x",
                                  title_en="ancient washing machine",
                                  published_at=old, expires_at=old,
                                  is_active=False)
    call_command("reindex_vectors")
    call_command("archive_documents", "--days", "365")
    # They were already excluded by is_active; archiving must not change that,
    # and must not resurrect them either.
    assert search_page("washing machine").results == []


@pytest.mark.django_db
def test_dry_run_reports_and_moves_nothing(capsys):
    old = timezone.now() - dt.timedelta(days=800)
    for i in range(3):
        SearchDocument.objects.create(source="ibay", source_key=str(i),
                                      doc_type="shopping", url="https://x",
                                      published_at=old, expires_at=old,
                                      is_active=False)
    call_command("archive_documents", "--days", "365", "--dry-run")
    assert "3" in capsys.readouterr().out
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM search_searchdocument_archive")
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/search/test_archive.py -v`
Expected: FAIL — the partition does not exist.

- [ ] **Step 3: Add the partition**

`SearchDocument` is LIST-partitioned by `source`, so the archive is a partition value, not a range. A migration:

```python
migrations.RunSQL(
    sql="""
    CREATE TABLE IF NOT EXISTS search_searchdocument_archive
        PARTITION OF search_searchdocument FOR VALUES IN ('archive');
    """,
    reverse_sql="DROP TABLE IF EXISTS search_searchdocument_archive;",
)
```

Archiving therefore rewrites `source` to `'archive'`, which moves the row between partitions. That has one consequence worth stating plainly: `(source, source_key)` is the identity key, so an archived row's identity changes and a re-scrape of the same listing would create a new row rather than updating the old one. That is acceptable and in fact desirable — a listing that comes back after two years dormant is a new listing — but the original key must be preserved so nothing is lost. Store it in `attrs['archived_from_source']` before the move.

- [ ] **Step 4: Write the command**

`search/archive.py`:

```python
"""Cold storage. Spec 12.6, 12.7.

The working set is what has to fit in shared_buffers on a 4 GB box. An expired
2019 listing competing for that memory with today's results is the specific
problem this solves.

Never deletes. An archived listing moves partition and keeps its row, its
attrs and its original source key.

Gazette is excluded on purpose: a published government notice does not expire,
and the gazette partition being write-once with no row churn is a structural
benefit (spec 5.7) that moving rows out of it would spend for nothing.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from search.models import SearchDocument

ARCHIVE_SOURCE = "archive"
NEVER_ARCHIVE_SOURCES = {"gazette", ARCHIVE_SOURCE}


def archive_candidates(days: int = 365):
    cutoff = timezone.now() - dt.timedelta(days=days)
    return (
        SearchDocument.objects.using("direct")
        .exclude(source__in=NEVER_ARCHIVE_SOURCES)
        .filter(is_active=False, published_at__lt=cutoff)
    )


def archive(days: int = 365, batch_size: int = 500) -> int:
    moved = 0
    while True:
        batch = list(
            archive_candidates(days).only("id", "source", "source_key", "attrs")
            [:batch_size]
        )
        if not batch:
            break
        with transaction.atomic(using="direct"):
            for doc in batch:
                # A row moving partition changes its identity key, so keep the
                # original where nothing can lose it.
                doc.attrs = {**doc.attrs,
                             "archived_from_source": doc.source,
                             "archived_at": timezone.now().isoformat()}
                doc.source_key = f"{doc.source}:{doc.source_key}"
                doc.source = ARCHIVE_SOURCE
                doc.save(update_fields=["source", "source_key", "attrs"])
        moved += len(batch)
    return moved
```

Moving a row between partitions is an `UPDATE` on the partition key, which Postgres implements as a delete plus insert. Django's `save(update_fields=...)` issues exactly that `UPDATE`, so it works — but it also means the row's `ctid` changes and any concurrent query holding a reference sees the old version. Run this off-peak.

`search/management/commands/archive_documents.py`: `--days` (default 365), `--dry-run` reporting the count, `--batch-size`, printing the moved total.

- [ ] **Step 5: Run the tests and measure**

```bash
pytest tests/search/test_archive.py -v
python manage.py archive_documents --days 365 --dry-run
```

Record table sizes before and after:

```sql
SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid))
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE relname LIKE 'search_searchdocument%' ORDER BY 1;
```

- [ ] **Step 6: Commit**

```bash
jj commit -m "P8 task 6: archive partitioning"
```

---

### Task 7: Final measurement and the operational runbook

**Files:**
- Create: `docs/superpowers/measurements/2026-08-p8-hardening.md`, `docs/RUNBOOK.md`

- [ ] **Step 1: Write the measurements file**

`docs/superpowers/measurements/2026-08-p8-hardening.md`:

```markdown
# P8 hardening, measured

Date: <fill>
Traffic window used: <fill> days, <fill> logged queries, <fill> logged clicks

## Evaluation set

| | Before P8 | After P8 |
|---|---|---|
| Cases | | |
| of which from real logs | 0 | |
| recall@5 | | |
| MRR | | |
| nDCG@10 | | |

Gate: recall@5 >= 0.80. If it moved down, the change is wrong, not the gate.

## Zero-result rate

| | Before | After aliases | After relaxation |
|---|---|---|---|
| Queries returning nothing | | | |
| % of all queries | | | |

## Aliases mined

| Source | Proposed | Applied | Improved recall? |
|---|---|---|---|
| translit | | | |
| keymap | | | |
| trigram | | | |

## Ranking weights

| Weight | Before | After |
|---|---|---|
| w_en | | |
| w_dv | | |
| w_latin | | |
| w_trigram | | |
| w_same_lang | | |
| w_freshness | | |
| w_quality | | |

Evaluations run: <fill>. Objective before / after: <fill>.

## Translation coverage

| Field | Filled before | Filled after |
|---|---|---|
| title_dv | | |
| title_en | | |
| summary_dv | | |
| summary_en | | |

TranslationCache hit rate during the pass: <fill>.

## Storage

| Table / partition | Rows | Size before | Size after archiving |
|---|---|---|---|
| search_searchdocument_ibay | | | |
| search_searchdocument_gazette | | | |
| search_searchdocument_archive | | | |
| search_documentspec | | | |
| search_querylog (all partitions) | | | |
| enrich_enrichedrecord | | | |
| **total** | | | |

Spec 12.6 projected ~670 MB at 51,000 iulaan and ~3.2 GB at the full archive.

## v2 re-entry conditions (spec 16)

| Condition | Threshold | Now | Triggered? |
|---|---|---|---|
| pgvector (16.1) | embedding beats lexical on cross-language recall@5 | | |
| Learning to rank (16.2) | ~10,000 logged clicks | | |
| Meilisearch (16.4) | p95 search latency missing target | | |
```

- [ ] **Step 2: Write the runbook**

`docs/RUNBOOK.md` — the operational half that has been accumulating across six phases and lives nowhere:

```markdown
# Runbook

## Scheduled

| Command | Cadence | Consequence of missing it |
|---|---|---|
| `create_log_partitions --months 3` | monthly | rows land in DEFAULT; retention cannot drop them cheaply |
| `prune_logs --days 90` | monthly | query text retained past the window (spec 16.3) |
| `rebuild_suggest_terms` | after each full reindex | autocomplete goes stale |
| `sync_specs --source ibay --prune` | after each shopping reindex | facets reflect old attributes |
| `translate_fields` | weekly | new documents show only one language |
| `archive_documents --days 365` | monthly, off-peak | working set grows into shared_buffers |
| `mine_aliases --days 30` | monthly, review before `--apply` | zero-result queries stay zero-result |
| `eval_search` | before and after any ranking change | a regression ships unnoticed |

## The reprocess chain

`stale_marked_at` is the single trigger and only the last stage clears it, so
the order is fixed:

    UPDATE search_searchdocument SET stale_marked_at = now() WHERE <slice>;
    python manage.py extract_attachments --stale     # P3, costs money
    python manage.py enrich_documents --stale        # P4, costs money
    python manage.py reindex --stale                 # clears the flag

Running `reindex --stale` first clears the flag and the paid stages find
nothing. Every one of these commands reports its count before spending.

## Cost controls

| Operation | Unit cost | Guard |
|---|---|---|
| Claude transcription | ~$0.006 / page batched | `status='ok'` is never re-sent |
| DeepSeek enrichment | ~$0.00025 / document | content_hash + prompt_version gates |
| Gazette prompt bump | would re-bill 51,000 docs | disabled for source='gazette' |
| Public report endpoint | free | inert data; only an admin action re-queues |

Off-peak DeepSeek is half price: avoid 01:00-04:00 and 06:00-10:00 UTC.

## When search gets slow

1. `EXPLAIN (ANALYZE, BUFFERS)` the page query. The candidate CTE is capped at
   500 rows; if it is scanning more, the GIN indexes are not being used.
2. Check facet cost separately (P5 measurements). N statements over one CTE.
3. Check discovery cost separately (P7 measurements).
4. Only then consider spec 16.4's Meilisearch re-entry.

## When results get worse

1. `eval_search` first. If it dropped, the last ranking or language change did it.
2. If eval is fine but users complain, the eval set is missing their case:
   `eval_search --propose` and read the zero-result list.
3. Never tune weights against a single complaint.
```

- [ ] **Step 3: Commit**

```bash
jj commit -m "P8 task 7: final measurements and runbook"
```

---

## Self-Review

**Spec coverage.** 14's evaluation set including minimal pairs → task 1 grows what P2 built; the minimal pairs themselves are already in `queries.yaml` from P2. 7's ranking tuning → task 4. 7's progressive relaxation ("drop the rarest term, then lower the trigram threshold, then suggest alternatives") → task 3, one rung per bullet. 5.5's background title translation → task 5. 12.6/12.7's archive partitioning → task 6. 6.5's `QueryAlias` population, which the spec left undefined → task 2. 10's language toggle, deferred from P6 → task 5 step 4. 16.1/16.2/16.4's re-entry conditions → task 7's final table, which is the point at which they are decided with evidence rather than as a preference.

**Known gaps, deliberate.** No learned ranking model: spec 16.2 sets the bar at ~10,000 logged clicks and task 7's table is where that is checked, not assumed. No pgvector: spec 16.1's re-entry needs a measured cross-language recall comparison, which is a project of its own and additive when it comes. No new sources: spec 16.5.

**Type consistency checked.** `zero_result_queries` and `clicked_pairs` both return `list[dict]` and `propose_cases` consumes both. `coordinate_descent(initial, grid, *, objective, rounds)` is called with that exact signature in `tune_ranking`. `RelaxationStep.as_dict()` produces the shape `SearchPage.relaxations` carries and `RelaxationOut` declares. `archive_candidates(days)` returns a queryset that `archive` slices.

**The two things to watch.** First, task 3's `set_limit` is session-scoped and the connection is pooled — a relaxed query that does not reset the threshold leaks a loose trigram floor into every later request on that connection, which degrades relevance invisibly. The reset belongs in a `finally`, and the test for it is named in step 4. Second, task 5's vector rebuild must call P2's `_rebuild_vectors` rather than a plain `SearchVector`, or the documents this command touches get a `vector_dv` built with the wrong analysis and Dhivehi search silently degrades for exactly the rows the command was supposed to improve.
