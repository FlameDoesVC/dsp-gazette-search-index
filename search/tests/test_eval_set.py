"""Relevance regression. Spec 14.

The fixtures are synthetic but the *queries* are real input shapes: Thaana,
keyboard space, phonetic Latin, English, and mixed. A weight change that
lowers recall@5 here is rejected regardless of how it looks by eye.
"""

import pytest
import yaml
from pathlib import Path

from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search import query

FIXTURE = Path(__file__).parent.parent / "eval" / "queries.yaml"
MIN_RECALL_AT_5 = 0.80


@pytest.fixture
def corpus(db):
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    upsert_drafts([DocumentDraft(**doc) for doc in data["documents"]])
    return data


def _recall_at_5(cases) -> tuple[float, list[str]]:
    hits, misses = 0, []
    for case in cases:
        found = [r.source_key for r in query.search(case["q"], limit=5)]
        if case["expect"] in found:
            hits += 1
        else:
            misses.append(f"{case['q']!r} -> {found} (wanted {case['expect']})")
    return hits / len(cases), misses


@pytest.mark.django_db
def test_recall_at_5_meets_the_bar(corpus):
    recall, misses = _recall_at_5(corpus["cases"])
    assert recall >= MIN_RECALL_AT_5, (
        f"recall@5={recall:.2f} below {MIN_RECALL_AT_5}\n" + "\n".join(misses)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["dv-Thaa", "dv-Keys", "dv-Latn", "en"])
def test_every_input_mode_is_represented_and_works(corpus, lang):
    cases = [c for c in corpus["cases"] if c["lang"] == lang]
    assert cases, f"no evaluation cases for {lang}"
    recall, misses = _recall_at_5(cases)
    assert recall >= MIN_RECALL_AT_5, f"{lang}: {recall:.2f}\n" + "\n".join(misses)


@pytest.mark.django_db
def test_minimal_pairs_rank_correctly(corpus):
    """The fili precision guard, promoted into the eval set so a weight change
    cannot quietly regress it."""
    for pair in corpus["minimal_pairs"]:
        top = query.search(pair["q"], limit=5)
        assert top, f"no results for {pair['q']!r}"
        assert top[0].source_key == pair["expect_first"], (
            f"{pair['q']!r} ranked {top[0].source_key} first, "
            f"wanted {pair['expect_first']}"
        )
