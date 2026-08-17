import pytest

from enrich.prior import apply_confidence_gate, prior_for


@pytest.mark.parametrize(
    "iulaan_type,expected",
    [
        ("ވަޒީފާގެ ފުރުޞަތު", "job"),
        ("Job Opportunity", "job"),
        ("ކުއްޔަށް ދިނުން", "property"),
        ("ކުއްޔަށް ހިފުން", "property"),
        ("ޢާންމު މަޢުލޫމާތު", "news"),
        ("Public Information", "news"),
        ("ދެންނެވުން", "news"),
        ("ބީލަން", "news"),
        ("ނީލަން", "news"),
        ("މުބާރާތް", "news"),
        ("", "news"),
        ("something nobody has seen before", "news"),
    ],
)
def test_gazette_prior(iulaan_type, expected):
    assert prior_for("gazette", iulaan_type=iulaan_type) == expected


@pytest.mark.parametrize(
    "categories,expected",
    [
        (["Jobs"], "job"),
        (["Housing & Real Estate"], "property"),
        (["Announcements & Events"], "news"),
        (["For Sale"], "shopping"),
        (["Services"], "shopping"),
        (["Wanted"], "shopping"),
        (["Free Stuff"], "shopping"),
        (["Business Opportunities"], "shopping"),
        ([], "news"),
        (["Electronics", "Jobs"], "job"),      # any matching level wins
    ],
)
def test_ibay_prior(categories, expected):
    assert prior_for("ibay", categories=categories) == expected


def test_unknown_source_falls_back_to_news():
    """news is the default sink -- there is no 'unknown' type. Spec 5.3."""
    assert prior_for("newspaper-mv") == "news"


def test_model_may_override_only_at_high_confidence():
    assert apply_confidence_gate("shopping", "job", 0.91) == ("job", True)
    assert apply_confidence_gate("shopping", "job", 0.80) == ("job", True)
    assert apply_confidence_gate("shopping", "job", 0.79) == ("shopping", False)
    assert apply_confidence_gate("shopping", "job", 0.0) == ("shopping", False)


def test_agreement_is_never_an_override():
    assert apply_confidence_gate("job", "job", 0.1) == ("job", False)


def test_an_unknown_model_type_never_wins():
    assert apply_confidence_gate("shopping", "tender", 0.99) == ("shopping", False)
