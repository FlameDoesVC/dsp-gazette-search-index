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


def test_six_texts_take_one_call(db, provider):
    rec = provider("1. one\n2. two\n3. three\n4. four\n5. five\n6. six")
    out = translate_batch_sync(["a", "b", "c", "d", "e", "f"], target="en")
    assert out == ["one", "two", "three", "four", "five", "six"]
    assert len(rec.prompts) == 1


def test_numbering_is_stripped_from_each_result(db, provider):
    provider("1. Preschool Teacher Assistant\n2. Staff needed")
    assert translate_batch_sync(["a", "b"], target="en") == [
        "Preschool Teacher Assistant", "Staff needed",
    ]


def test_a_short_reply_falls_back_to_one_call_per_item(db, provider):
    """The batch failure mode: the model returns fewer lines than it was given,
    so results would silently shift onto the wrong documents. Misalignment must
    never be papered over -- fall back and pay for accuracy."""
    rec = provider("1. only one line")
    out = translate_batch_sync(["a", "b", "c"], target="en")
    assert len(out) == 3
    assert len(rec.prompts) > 1        # fell back to individual calls


def test_a_long_reply_also_falls_back(db, provider):
    rec = provider("1. a\n2. b\n3. c\n4. spurious extra line")
    out = translate_batch_sync(["a", "b", "c"], target="en")
    assert len(out) == 3
    assert len(rec.prompts) > 1


def test_out_of_order_numbering_is_reordered_not_trusted_positionally(db, provider):
    provider("2. second\n1. first")
    assert translate_batch_sync(["a", "b"], target="en") == ["first", "second"]


def test_batch_size_is_respected(db, provider):
    rec = provider("\n".join(f"{i}. x" for i in range(1, 5)))
    # distinct inputs so the second chunk is a miss, not a cache hit
    translate_batch_sync([f"t{i}" for i in range(8)], target="en", batch_size=4)
    assert len(rec.prompts) == 2


def test_an_empty_input_makes_no_call(db, provider):
    rec = provider("")
    assert translate_batch_sync([], target="en") == []
    assert rec.prompts == []


def test_the_cache_is_consulted_per_item_not_per_batch(provider, db):
    """A batch of six where five are cached must send one item, not six.
    Keying the cache on the batch would make it useless -- batches never
    repeat, individual titles repeat constantly (40% of iBay titles are
    duplicates)."""
    from core.models import TranslationCache
    from core.translate import _hash
    for i, t in enumerate(["a", "b", "c", "d", "e"]):
        TranslationCache.objects.create(source_hash=_hash(t),
                                        translated_text=f"cached-{i}")
    rec = provider("1. fresh")
    out = translate_batch_sync(["a", "b", "c", "d", "e", "f"], target="en")
    assert out[:5] == [f"cached-{i}" for i in range(5)]
    assert out[5] == "fresh"
    assert len(rec.prompts) == 1
