

def test_an_overlong_unit_is_dropped_rather_than_crashing_the_pass(db):
    """A unit is short by nature: GB, mm, W, MVR, mAh. What overflowed
    DocumentSpec.unit was not units at all -- 31 of 494 distinct values read
    'Action and Adventure', 'H.265+/H.265/H.264+/H.264', 'Capture & reduction of
    growth'. Those are VALUES the model filed in the wrong slot.

    Widening the column would let a sentence become a facet value. The opposite
    call from Iulaan.translated_title, which WAS widened because its content was
    legitimate -- same symptom, and the data decides which.

    Before this the DataError aborted the whole pass: 20,494 enriched records
    projected 192 rows.
    """
    from search.models import DocumentSpec, SearchDocument
    from search.specs.project import sync_document_specs

    doc = SearchDocument.objects.create(
        source="ibay", source_key="u1", doc_type="shopping", url="https://x/u1",
        title_en="Sharp air purifier",
        attrs={"specs": [
            # The real shape of the three that crashed: a NUMBER with a
            # sentence in the unit slot. Verbatim from source_key 6521025.
            {"key_raw": "dimension", "value_num": 70,
             "unit": "height under sofa"},
            {"key_raw": "Storage", "value_num": 256, "unit": "GB"},
        ]})
    sync_document_specs(doc)

    rows = {r.key_raw: r for r in DocumentSpec.objects.filter(document_id=doc.id)}
    # The good spec is untouched.
    assert rows["storage"].unit == "GB"
    assert rows["storage"].value_num == 256
    # The number survives; only the bogus unit is dropped, and the text it held
    # is kept as a value rather than thrown away.
    bad = rows["dimension"]
    assert bad.value_num == 70
    assert bad.unit == ""
    assert bad.value_text == "height under sofa"


def test_widths_are_read_from_the_model():
    """So a migration cannot leave the truncations silently wrong."""
    from search.models import DocumentSpec
    import search.specs.project as project

    assert project._UNIT_MAX == DocumentSpec._meta.get_field("unit").max_length
    assert project._VALUE_TEXT_MAX == \
        DocumentSpec._meta.get_field("value_text").max_length
