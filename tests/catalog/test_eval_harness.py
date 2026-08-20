"""The gate has to be scoreable more than once.

Two flaws made it a one-shot measurement. `--sample` overwrote the file, so
every identity change cost a fresh 50 rows of hand labelling and in practice the
gate was scored once and left to go stale. And the fixed seed did not reproduce
the draw: `random.sample` over an unsorted id list is only stable while the
population is, and the corpus grows every sync.
"""

import yaml
import pytest
from django.core.management import call_command

from catalog.models import Entity, EntityLink
from search.models import SearchDocument


@pytest.fixture
def golden(tmp_path, monkeypatch):
    from catalog.management.commands import eval_entities
    path = tmp_path / "golden.yaml"
    monkeypatch.setattr(eval_entities, "GOLDEN", path)
    return path


@pytest.fixture
def corpus(db):
    docs = []
    for i in range(6):
        docs.append(SearchDocument.objects.create(
            source="ibay", source_key=f"k{i}", doc_type="shopping",
            title_en=f"Widget {i} model X{i}", url=f"https://x/{i}"))
    entity = Entity.objects.create(kind="product", key="e" * 16,
                                   model_name="X0", listing_count=2)
    for doc in docs[:2]:
        EntityLink.objects.create(entity=entity, source="ibay",
                                  source_key=doc.source_key)
    return docs, entity


def test_a_refresh_keeps_a_label_whose_grouping_did_not_change(golden, corpus):
    call_command("eval_entities", sample=6)
    rows = yaml.safe_load(golden.read_text())
    for row in rows:
        row["correct"] = True
        row["note"] = "reviewed"
    golden.write_text(yaml.safe_dump(rows, sort_keys=False))

    call_command("eval_entities", refresh=True)
    rows = yaml.safe_load(golden.read_text())
    assert all(r["correct"] is True for r in rows)
    assert all(r["note"] == "reviewed" for r in rows)


def test_a_changed_grouping_invalidates_its_label(golden, corpus):
    docs, entity = corpus
    call_command("eval_entities", sample=6)
    rows = yaml.safe_load(golden.read_text())
    for row in rows:
        row["correct"] = True
    golden.write_text(yaml.safe_dump(rows, sort_keys=False))

    # A third listing joins the entity: the question the label answered was
    # "does this belong with those", and "those" is now a different set.
    EntityLink.objects.create(entity=entity, source="ibay", source_key="k2")
    entity.listing_count = 3
    entity.save(update_fields=["listing_count"])

    call_command("eval_entities", refresh=True)
    rows = {r["source_key"]: r for r in yaml.safe_load(golden.read_text())}
    assert rows["k0"]["correct"] is None
    assert rows["k0"]["was"]["correct"] is True
    # A row the change did not touch keeps its verdict.
    assert rows["k5"]["correct"] is True


def test_a_draw_is_reproducible_from_scratch(golden, corpus):
    call_command("eval_entities", sample=3)
    first = {r["source_key"] for r in yaml.safe_load(golden.read_text())}
    golden.unlink()
    call_command("eval_entities", sample=3)
    assert {r["source_key"] for r in yaml.safe_load(golden.read_text())} == first


def test_a_growing_corpus_does_not_disturb_the_rows_already_drawn(golden, corpus):
    """The property that actually matters. A redraw on a bigger corpus is
    allowed to reach different listings; what must not happen is losing the
    labelled ones, because that is what made the gate a one-shot measurement."""
    call_command("eval_entities", sample=3)
    rows = yaml.safe_load(golden.read_text())
    for row in rows:
        row["correct"] = True
    golden.write_text(yaml.safe_dump(rows, sort_keys=False))
    drawn = {r["source_key"] for r in rows}

    for i in range(20, 40):
        SearchDocument.objects.create(
            source="ibay", source_key=f"k{i}", doc_type="shopping",
            title_en=f"Widget {i}", url=f"https://x/{i}")

    call_command("eval_entities", sample=5)
    rows = yaml.safe_load(golden.read_text())
    assert drawn <= {r["source_key"] for r in rows}
    assert len(rows) == 5
    kept = [r for r in rows if r["source_key"] in drawn]
    assert all(r["correct"] is True for r in kept)


def test_topping_up_adds_rows_without_relabelling_the_old_ones(golden, corpus):
    call_command("eval_entities", sample=2)
    rows = yaml.safe_load(golden.read_text())
    for row in rows:
        row["correct"] = False
    golden.write_text(yaml.safe_dump(rows, sort_keys=False))

    call_command("eval_entities", sample=4)
    rows = yaml.safe_load(golden.read_text())
    assert len(rows) == 4
    assert sum(1 for r in rows if r["correct"] is False) == 2
    assert sum(1 for r in rows if r["correct"] is None) == 2


def test_unlabelled_rows_are_reported_rather_than_counted_as_passes(
        golden, corpus, capsys):
    call_command("eval_entities", sample=6)
    rows = yaml.safe_load(golden.read_text())
    rows[0]["correct"] = True
    golden.write_text(yaml.safe_dump(rows, sort_keys=False))

    call_command("eval_entities")
    out = capsys.readouterr().out
    assert "1 labelled" in out
    assert "5 of 6 rows are unlabelled" in out
