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


@pytest.mark.django_db
def test_estimated_net_min_is_written_for_the_salary_facet():
    EnrichedRecord.objects.create(
        source="gazette", source_key="IUL-1", content_hash="h" * 64,
        doc_type="job", status="ok",
        attrs={"compensation": {"basic_salary": 10750, "salary_state": "listed",
                                "pension_applies": True, "completeness": "basic_only"}},
    )
    out = apply_enrichment(_draft())
    assert out.attrs["estimated_net_min"] == pytest.approx(9997.50)


@pytest.mark.django_db
def test_an_unset_schema_default_does_not_blank_adapter_data():
    """model_dump() returns every field in the schema, including ones the model
    never touched. Merging it wholesale let `category_path: []` overwrite iBay's
    own breadcrumb on 7,553 documents -- which took in_scope(), _mapped_key()
    and _is_service() out together and halved entity resolution, 22,869 links
    down to 11,098.

    This is the prompt's rule 3 enforced on our side instead of trusted to the
    model: scraped fields win, the model may fill a blank and never overwrite
    one."""
    EnrichedRecord.objects.create(
        source="ibay", source_key="cp1", doc_type="shopping", status="ok",
        content_hash="h", canonical_title_en="Fridge repair",
        attrs={"brand": "Samsung"},          # category_path never mentioned
    )
    draft = DocumentDraft(
        source="ibay", source_key="cp1", doc_type="shopping",
        url="https://x/cp1", title_en="Fridge repair", content_hash="h",
        attrs={"category_path": ["Services", "Repairs"], "specs_raw": {"a": "b"}},
    )

    out = apply_enrichment(draft)

    assert out.attrs["category_path"] == ["Services", "Repairs"]
    assert out.attrs["specs_raw"] == {"a": "b"}
    assert out.attrs["brand"] == "Samsung"


@pytest.mark.django_db
def test_false_and_zero_are_answers_not_absences():
    """`negotiable: False` is the whole point of a boolean facet. Dropping
    falsy values instead of empty ones would lose it."""
    EnrichedRecord.objects.create(
        source="ibay", source_key="fz1", doc_type="shopping", status="ok",
        content_hash="h", attrs={"negotiable": False, "quantity": 0},
    )
    draft = DocumentDraft(source="ibay", source_key="fz1", doc_type="shopping",
                          url="https://x/fz1", content_hash="h", attrs={})

    out = apply_enrichment(draft)

    assert out.attrs["negotiable"] is False
    assert out.attrs["quantity"] == 0


@pytest.mark.django_db
def test_the_model_can_never_write_the_source_taxonomy():
    """Of 250 records where the model filled category_path, 250 differed from the
    adapter and none matched -- it invents its own scheme ('Electronics/Audio
    Equipment', 'Education/Online Learning', 'Websites').

    That is fatal rather than untidy: in_scope() requires path[0] to be 'For
    Sale' or 'Services', so an invented root drops the document out of entity
    resolution, and _mapped_key() and _is_service() read the same field.
    Filtering empty answers is not enough when a wrong answer is worse than a
    blank one and no right answer is possible."""
    EnrichedRecord.objects.create(
        source="ibay", source_key="tx1", doc_type="shopping", status="ok",
        content_hash="h",
        attrs={"category_path": ["Electronics", "Audio Equipment"],
               "specs_raw": {"invented": "yes"},
               "brand": "Sony"},
    )
    draft = DocumentDraft(
        source="ibay", source_key="tx1", doc_type="shopping",
        url="https://x/tx1", content_hash="h",
        attrs={"category_path": ["For Sale", "Electronics", "Speaker Systems"],
               "specs_raw": {"Brand": "Sony"}},
    )

    out = apply_enrichment(draft)

    assert out.attrs["category_path"] == ["For Sale", "Electronics", "Speaker Systems"]
    assert out.attrs["specs_raw"] == {"Brand": "Sony"}
    # What the model is actually good for still lands.
    assert out.attrs["brand"] == "Sony"
