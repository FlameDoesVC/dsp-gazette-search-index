import pytest

from enrich.preextract import extract_candidates
from enrich.validate import ground, normalize_for_match, token_overlap
from tests.enrich.fixtures.corpus_samples import GAZETTE_JOB_BODY


def _ground(attrs, text, doc_type="job", scraped=None):
    return ground(
        attrs,
        doc_type=doc_type,
        source_text=text,
        candidates=extract_candidates(text),
        scraped=scraped or {},
    )


# --- strings ------------------------------------------------------------

def test_a_string_present_verbatim_survives():
    model, report = _ground({"role": "Administrative Officer"},
                            "Vacancy: Administrative Officer at the Ministry")
    assert model.role == "Administrative Officer"
    assert report["dropped"] == []


def test_a_string_that_is_not_in_the_source_is_dropped():
    model, report = _ground({"employer": "Bank of Maldives"},
                            "Vacancy: Administrative Officer")
    assert model.employer == ""
    assert any(d["field"] == "employer" for d in report["dropped"])
    assert report["dropped"][0]["reason"] == "not_grounded"


def test_a_lightly_reworded_string_survives_on_token_overlap():
    model, _ = _ground({"role": "Senior Administrative Officer"},
                       "Post: Senior  Administrative   Officer (contract)")
    assert model.role == "Senior Administrative Officer"


def test_normalization_ignores_case_punctuation_and_spacing():
    assert normalize_for_match("Senior  Officer, (GS3)") == "senior officer gs3"


def test_token_overlap_is_symmetric_enough_to_be_useful():
    assert token_overlap("administrative officer", "administrative officer") == 1.0
    assert token_overlap("administrative officer", "officer") == 1.0
    assert token_overlap("bank of maldives", "administrative officer") == 0.0


# --- numbers ------------------------------------------------------------

def test_a_salary_present_in_the_source_survives():
    model, _ = _ground(
        {"compensation": {"basic_salary": 10750, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.basic_salary == 10750


def test_a_salary_the_model_invented_is_dropped():
    """The number 99,999 appears nowhere in the body. Spec 5.2 layer 3."""
    model, report = _ground(
        {"compensation": {"basic_salary": 99999, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.basic_salary is None
    assert any("basic_salary" in d["field"] for d in report["dropped"])


def test_a_totalled_salary_is_dropped_because_the_total_is_not_in_the_source():
    """10,750 + 4,400 + 2,000 = 17,150. The model was told not to add. If it
    adds anyway, the sum is not a digit run in the source and dies here.
    This is the test that makes 'no arithmetic' enforceable rather than
    aspirational."""
    model, _ = _ground(
        {"compensation": {"basic_salary": 17150, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.basic_salary is None


def test_a_phone_not_in_the_candidate_set_is_dropped():
    model, report = _ground(
        {"contacts": [{"kind": "phone", "value": "7771234"}]},
        "Call 7994400 for details",
    )
    assert model.contacts == []
    assert any(d["reason"] == "not_grounded" for d in report["dropped"])


def test_a_phone_in_the_candidate_set_survives():
    model, _ = _ground(
        {"contacts": [{"kind": "phone", "value": "7994400"}]},
        "Call 7994400 for details",
    )
    assert model.contacts[0].value == "7994400"


def test_thousands_separators_do_not_break_number_matching():
    model, _ = _ground(
        {"compensation": {"basic_salary": 32632, "salary_state": "listed"}},
        "Basic salary -/32,632 per month",
    )
    assert model.compensation.basic_salary == 32632


# --- dates --------------------------------------------------------------

def test_a_parseable_in_range_date_survives():
    model, _ = _ground({"deadline": "2026-08-31"}, GAZETTE_JOB_BODY)
    assert model.deadline == "2026-08-31"


def test_an_unparseable_date_is_dropped():
    model, report = _ground({"deadline": "next Thursday"}, GAZETTE_JOB_BODY)
    assert model.deadline == ""
    assert any(d["reason"] == "bad_date" for d in report["dropped"])


def test_a_date_outside_the_sane_range_is_dropped():
    model, report = _ground({"deadline": "1953-01-01"}, "deadline 1953-01-01")
    assert model.deadline == ""
    assert any(d["reason"] == "date_out_of_range" for d in report["dropped"])


# --- the negotiable rule ------------------------------------------------

def test_negotiable_survives_when_the_source_says_so():
    model, _ = _ground(
        {"compensation": {"salary_state": "negotiable"}},
        "Salary negotiable depending on experience",
    )
    assert model.compensation.salary_state == "negotiable"


def test_negotiable_is_demoted_to_unlisted_when_the_source_is_silent():
    """Spec 4.3: a missing salary is `unlisted`, never `negotiable`. Those are
    different claims and the card renders them differently."""
    model, report = _ground(
        {"compensation": {"salary_state": "negotiable"}},
        "Looking for a cashier. Call 9483252.",
    )
    assert model.compensation.salary_state == "unlisted"
    assert any(d["reason"] == "negotiable_unsupported" for d in report["dropped"])


@pytest.mark.parametrize(
    "text",
    ["salary negotiable", "Salary is Negotiable", "pay to be discussed",
     "މުސާރަ: އެއްބަސްވެވޭ ގޮތެއްގެ މަތިން"],
)
def test_negotiable_markers(text):
    model, _ = _ground({"compensation": {"salary_state": "negotiable"}}, text)
    assert model.compensation.salary_state == "negotiable"


# --- scraped fields win -------------------------------------------------

def test_the_model_may_fill_a_null_scraped_field():
    model, report = _ground(
        {"employer": "Ministry of Example"},
        "Ministry of Example is hiring",
        scraped={"employer": ""},
    )
    assert model.employer == "Ministry of Example"
    assert report["needs_review"] is False


def test_the_model_may_not_overwrite_a_scraped_field():
    """Spec 5.2 layer 4. The scraped value stays and the record is flagged."""
    model, report = _ground(
        {"employer": "Ministry of Example"},
        "Ministry of Example is hiring",
        scraped={"employer": "Ministry of Health"},
    )
    assert model.employer == "Ministry of Health"
    assert report["needs_review"] is True
    assert any(d["reason"] == "scraped_conflict" for d in report["dropped"])


def test_an_identical_value_is_not_a_conflict():
    model, report = _ground(
        {"employer": "Ministry of Health"},
        "Ministry of Health is hiring",
        scraped={"employer": "Ministry of Health"},
    )
    assert report["needs_review"] is False


# --- completeness is derived, not trusted -------------------------------

def test_completeness_is_recomputed_from_what_survived():
    """The model claimed `full`, but the allowance was dropped as ungrounded,
    so the estimate is now partial and the card must say 'at least'."""
    model, _ = _ground(
        {"compensation": {
            "basic_salary": 10750, "salary_state": "listed", "completeness": "full",
            "allowances": [{"kind": "living", "label_raw": "living", "amount": 9999,
                            "basis": "fixed_monthly"}],
        }},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.allowances == []
    assert model.compensation.completeness == "partial"


def test_basic_only_when_no_allowances_were_claimed():
    model, _ = _ground(
        {"compensation": {"basic_salary": 10750, "salary_state": "listed"}},
        GAZETTE_JOB_BODY,
    )
    assert model.compensation.completeness == "basic_only"


# --- schema violations --------------------------------------------------

def test_an_unknown_enum_value_does_not_lose_the_whole_record():
    model, report = _ground(
        {"role": "Administrative Officer", "position_type": "Permanent",
         "compensation": {"period": "fortnight"}},
        "Administrative Officer, Permanent",
    )
    assert model.role == "Administrative Officer"
    assert model.compensation.period == "month"        # default restored
    assert any(d["reason"] == "schema" for d in report["dropped"])


def test_garbage_attrs_yields_an_empty_model_not_an_exception():
    model, report = _ground({"role": {"nested": "object"}}, "text")
    assert model.role == ""
    assert report["dropped"]
