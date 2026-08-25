"""Translations have to survive a reindex.

They did not. `reindex` recomputes every field in indexing._UPDATE_FIELDS from
the adapter plus the overlays, so a value written straight into SearchDocument
lasted until the next pass and then vanished with no error -- the adapter simply
rebuilt the field as empty. Gazette lost 149 English titles that way. iBay,
whose adapter supplies no Dhivehi side at all, would have lost ~20,000 titles
and every translated summary.
"""

import pytest

from search.adapters.base import DocumentDraft
from search.models import FieldTranslation
from search.translations import apply_translations, remember, source_hash


def draft(**kw):
    base = dict(source="ibay", source_key="1", doc_type="shopping",
                url="https://x/1")
    base.update(kw)
    return DocumentDraft(**base)


@pytest.mark.django_db
def test_a_stored_translation_is_put_back_on_the_draft():
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair for sale.", value="ގޮނޑިއެއް ވިއްކަނީ")

    out = apply_translations(draft(summary_en="A chair for sale."))

    assert out.summary_dv == "ގޮނޑިއެއް ވިއްކަނީ"


@pytest.mark.django_db
def test_real_source_content_is_never_overwritten():
    """If the adapter supplied Dhivehi from the source, that is ground truth and
    a translation of the English has no business replacing it."""
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair for sale.", value="machine translation")

    out = apply_translations(
        draft(summary_en="A chair for sale.", summary_dv="the real Dhivehi"))

    assert out.summary_dv == "the real Dhivehi"


@pytest.mark.django_db
def test_a_translation_of_text_that_has_changed_is_ignored():
    """A seller edits the listing, so the English is new and the stored Dhivehi
    describes something the document no longer says. Showing it would be worse
    than showing nothing."""
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair for sale.", value="ގޮނޑިއެއް ވިއްކަނީ")

    out = apply_translations(draft(summary_en="A desk for sale, reduced."))

    assert out.summary_dv == ""


@pytest.mark.django_db
def test_reflowed_whitespace_is_not_a_change():
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair for sale.", value="ގޮނޑިއެއް")

    out = apply_translations(draft(summary_en="A  chair\nfor   sale."))

    assert out.summary_dv == "ގޮނޑިއެއް"


@pytest.mark.django_db
def test_a_missing_source_field_is_ignored_rather_than_trusted():
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair for sale.", value="ގޮނޑިއެއް")

    out = apply_translations(draft(summary_en=""))

    assert out.summary_dv == ""


@pytest.mark.django_db
def test_remembering_twice_updates_rather_than_duplicating():
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair.", value="first")
    remember("ibay", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair.", value="second")

    rows = FieldTranslation.objects.filter(source="ibay", source_key="1")
    assert rows.count() == 1
    assert rows.first().value == "second"


@pytest.mark.django_db
def test_the_overlay_runs_before_enrichment():
    """Order is the whole safety argument: a machine translation is the weakest
    claim in the stack and has to lose to a model extraction from the document
    itself."""
    from django.conf import settings

    overlays = settings.SEARCH_DRAFT_OVERLAYS
    assert overlays[0] == "search.translations.apply_translations"
    assert "enrich.overlay.apply_enrichment" in overlays[1:]


@pytest.mark.django_db
def test_a_translation_survives_a_reindex(monkeypatch):
    """The regression this whole table exists for. Before it, fill_bilingual's
    output lived only in SearchDocument and the next reindex rebuilt the field
    from an adapter that had nothing to put there."""
    from search.adapters import base
    from search.indexing import reindex_source
    from search.models import SearchDocument

    class _Adapter:
        key = "toy"

        def iter_source_keys(self, **filters):
            yield "1"

        def fetch_raw(self, source_key):
            return base.RawDocument(source=self.key, source_key=source_key,
                                    payload={})

        def to_document(self, raw):
            # Like iBay: an English side and nothing at all in Dhivehi.
            return DocumentDraft(source="toy", source_key="1",
                                 doc_type="shopping", url="https://x/1",
                                 title_en="Office chair",
                                 summary_en="A chair for sale.")

    monkeypatch.setitem(base._REGISTRY, "toy", _Adapter())
    remember("toy", "1", target_field="summary_dv", source_field="summary_en",
             origin_text="A chair for sale.", value="ގޮނޑިއެއް ވިއްކަނީ")

    reindex_source("toy")
    doc = SearchDocument.objects.get(source="toy", source_key="1")
    assert doc.summary_dv == "ގޮނޑިއެއް ވިއްކަނީ"

    # And again, because surviving once is not surviving.
    reindex_source("toy")
    doc.refresh_from_db()
    assert doc.summary_dv == "ގޮނޑިއެއް ވިއްކަނީ"
