import pytest
from search.adapters import base


class _Dummy:
    key = "dummy"

    def iter_source_keys(self, **filters):
        yield "1"

    def fetch_raw(self, source_key):
        if source_key != "1":
            return None
        return base.RawDocument(source="dummy", source_key="1", payload={"t": "hi"})

    def to_document(self, raw):
        return base.DocumentDraft(
            source="dummy",
            source_key=raw.source_key,
            doc_type="news",
            url="https://example.mv/1",
            title_en=raw.payload["t"],
            text_en=raw.payload["t"],
        )


def test_register_and_retrieve(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register(_Dummy())
    assert base.get_adapter("dummy").key == "dummy"
    assert [a.key for a in base.all_adapters()] == ["dummy"]


def test_unknown_adapter_raises(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})
    with pytest.raises(KeyError):
        base.get_adapter("nope")


def test_duplicate_registration_raises(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register(_Dummy())
    with pytest.raises(ValueError):
        base.register(_Dummy())


def test_fetch_raw_round_trips_every_listed_key(monkeypatch):
    """Spec 3.1: a source that cannot be read back cannot be reprocessed."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    base.register(_Dummy())
    adapter = base.get_adapter("dummy")
    for key in adapter.iter_source_keys():
        assert adapter.fetch_raw(key) is not None


def test_draft_defaults_are_safe():
    d = base.DocumentDraft(
        source="s", source_key="k", doc_type="news", url="https://x.mv"
    )
    assert d.attrs == {}
    assert d.thumbnails == []
    assert d.is_active is True
    assert d.currency == "MVR"
