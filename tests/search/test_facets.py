import pytest

from search.facets import FACETS, facet_def


def test_every_doc_type_has_a_facet_set():
    assert set(FACETS) == {"job", "property", "shopping", "news", "all"}


@pytest.mark.parametrize(
    "doc_type,expected",
    [
        # Spec 8.1
        ("job", {"job_category", "position_type", "salary_state", "employer",
                 "grade", "location", "net_estimate"}),
        # Spec 8.2
        ("property", {"listing_kind", "price", "unit_kind", "is_shared",
                      "bedrooms", "bathrooms", "furnishing", "neighborhood",
                      "island", "atoll", "has_lift", "square_feet",
                      "tenant_preference"}),
        # Spec 8.3 universal half; the dynamic half is P7
        ("shopping", {"price", "condition", "brand", "location", "seller_type",
                      "has_images"}),
        # Spec 8.4
        ("news", {"office", "announcement_type", "has_attachments",
                  "is_tender"}),
    ],
)
def test_the_spec_facet_sets_are_all_present(doc_type, expected):
    keys = {f.key for f in FACETS[doc_type]}
    assert expected <= keys, f"missing from {doc_type}: {expected - keys}"


def test_every_facet_declares_a_widget_and_a_bilingual_label():
    for doc_type, defs in FACETS.items():
        for f in defs:
            assert f.widget in {"checkbox", "range", "toggle"}, (doc_type, f.key)
            assert f.label_en and f.label_dv, (doc_type, f.key)


def test_every_facet_declares_where_its_value_lives():
    for defs in FACETS.values():
        for f in defs:
            assert f.storage in {"column", "attrs", "attrs_array"}, f.key
            assert f.path, f.key


def test_rent_ranges_are_declared_per_period_and_currency():
    """Spec 8.2: a 300-per-day guest house room and a 7,000-per-month
    apartment on one slider is meaningless."""
    price = facet_def("property", "price")
    assert price.split_by == ["currency", "price_period"]


def test_no_facet_is_time_dependent():
    """Spec 8. `deadline` appears as a computed response field, never as a
    stored facet value."""
    for defs in FACETS.values():
        for f in defs:
            assert f.key not in {"deadline_state", "days_left", "is_open"}
