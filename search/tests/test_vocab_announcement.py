import pytest

from search.vocab import (
    ANNOUNCEMENT_TYPE_CANONICAL, LISTING_KIND, canonical,
    canonical_announcement_type, label,
)


def test_announcement_type_english_variants_collapse_to_one_bucket():
    """'Job Opportunity' and 'ވަޒީފާގެ ފުރުޞަތު' are one concept split into
    two IulaanType rows; the facet must show one bucket, not two."""
    assert canonical_announcement_type("Job Opportunity") == "ވަޒީފާގެ ފުރުޞަތު"
    assert canonical_announcement_type("Need to Rent") == "ކުއްޔަށް ހިފުން"
    assert canonical_announcement_type("ބީލަން") == "ބީލަން"


def test_listing_kind_includes_wanted():
    """PropertyAttrs.listing_kind permits 'wanted'; it must not fall through
    to raw English."""
    assert "wanted" in LISTING_KIND
    assert label("listing_kind", "Wanted") == "Wanted"


def test_canonical_keeps_thaana_keys():
    assert canonical("ބީލަން") == "ބީލަން"


def test_the_announcement_type_label_resolves_to_the_correct_english():
    """'Bill' was the machine translation of ބީލަން; the catalog says Tender."""
    from django.utils import translation
    with translation.override("en"):
        assert label("announcement_type", "ބީލަން") == "Tender"


@pytest.mark.django_db
def test_the_prior_and_adapter_tables_agree_on_every_iulaan_type():
    """Two tables classify gazette IulaanType rows; a row classified 'property'
    by one and 'news' by the other is a silent doc_type regression."""
    from enrich.prior import IULAAN_TYPE_MAP
    from gazette.models import IulaanType
    from search.adapters.gazette import IULAAN_TYPE_DOC_TYPE

    names = [
        "ވަޒީފާގެ ފުރުޞަތު", "Job Opportunity", "ކުއްޔަށް ދިނުން",
        "ކުއްޔަށް ހިފުން", "ޢާންމު މަޢުލޫމާތު", "ބީލަން",
        "ނީލަން", "މަސައްކަތް", "ތަމްރީނު", "ގަންނަން ބޭނުންވާ ތަކެތި",
        "Need to Rent", "Public Information", "Tender", "Auction", "Training",
    ]
    for name in names:
        IulaanType.objects.create(name=name)

    for row in IulaanType.objects.all():
        prior = IULAAN_TYPE_MAP.get(row.name, "news")
        adapter = IULAAN_TYPE_DOC_TYPE.get(row.name, "news")
        assert prior == adapter, (
            f"tables disagree on {row.name!r}: prior={prior} adapter={adapter}"
        )
