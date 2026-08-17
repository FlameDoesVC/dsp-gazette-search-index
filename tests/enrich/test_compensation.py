import pytest

from enrich.compensation import estimate_net, salary_display
from enrich.schemas import Allowance, Compensation


def _fixed(kind, amount):
    return Allowance(kind=kind, label_raw=kind, amount=amount, basis="fixed_monthly")


def test_the_worked_example_from_the_spec():
    """basic 10,750, attendance 4,400 fixed, 7% pension on basic alone.
    10750 - 752.50 + 4400 = 14,397.50"""
    comp = Compensation(
        basic_salary=10750,
        allowances=[_fixed("attendance", 4400)],
        pension_applies=True,
        salary_state="listed",
        completeness="full",
    )
    est = estimate_net(comp)
    assert est.value == pytest.approx(14397.50)
    assert est.is_floor is False
    assert est.working_days == 20


def test_pension_is_deducted_from_basic_not_from_gross():
    """Allowances are added AFTER the deduction. Pensionable wage is basic
    salary alone. Getting this backwards overstates take-home by 7% of every
    allowance, which is exactly the misleading number this system forbids."""
    comp = Compensation(basic_salary=10000, allowances=[_fixed("living", 5000)],
                        pension_applies=True)
    est = estimate_net(comp)
    assert est.value == pytest.approx(14300.0)     # not 13950.0


def test_no_pension_when_the_ad_does_not_say_so():
    comp = Compensation(basic_salary=10000, pension_applies=False)
    assert estimate_net(comp).value == pytest.approx(10000.0)


def test_per_day_allowance_multiplies_by_working_days():
    comp = Compensation(
        basic_salary=8000,
        allowances=[Allowance(kind="attendance", label_raw="daily", amount=100,
                              basis="per_day")],
        pension_applies=True,
    )
    assert estimate_net(comp).value == pytest.approx(8000 - 560 + 2000)
    assert estimate_net(comp, working_days=26).value == pytest.approx(8000 - 560 + 2600)


def test_per_hour_allowance_uses_eight_hour_days():
    comp = Compensation(
        basic_salary=0,
        allowances=[Allowance(kind="overtime", label_raw="hourly", amount=50,
                              basis="per_hour")],
    )
    assert estimate_net(comp, working_days=20).value == pytest.approx(50 * 8 * 20)


def test_percent_of_basic_allowance():
    comp = Compensation(
        basic_salary=10000,
        allowances=[Allowance(kind="service", label_raw="35%", amount=35,
                              basis="percent_of_basic")],
    )
    assert estimate_net(comp).value == pytest.approx(13500.0)


def test_partial_completeness_renders_as_a_floor():
    comp = Compensation(basic_salary=10000, allowances=[_fixed("living", 1000)],
                        completeness="partial")
    est = estimate_net(comp)
    assert est.is_floor is True


def test_basic_only_returns_none_rather_than_restating_basic():
    """Spec 8.1: when the estimate would just restate basic salary it is
    omitted entirely rather than padding the card with a fake calculation."""
    comp = Compensation(basic_salary=10000, pension_applies=False,
                        completeness="basic_only")
    assert estimate_net(comp) is None


def test_basic_only_with_pension_is_still_worth_showing():
    comp = Compensation(basic_salary=10000, pension_applies=True,
                        completeness="basic_only")
    assert estimate_net(comp).value == pytest.approx(9300.0)


def test_no_basic_salary_returns_none():
    assert estimate_net(Compensation(salary_state="unlisted")) is None
    assert estimate_net(Compensation(salary_state="negotiable")) is None


def test_daily_and_hourly_period_are_not_monthly_estimates():
    """A wage quoted per day cannot be turned into a monthly take-home
    without inventing a schedule. Return None rather than guess."""
    assert estimate_net(Compensation(basic_salary=500, period="day")) is None
    assert estimate_net(Compensation(basic_salary=60, period="hour")) is None


def test_breakdown_shows_the_arithmetic():
    comp = Compensation(basic_salary=10750, allowances=[_fixed("attendance", 4400)],
                        pension_applies=True, completeness="full")
    est = estimate_net(comp)
    assert est.breakdown == [
        {"label": "basic", "amount": pytest.approx(10750.0)},
        {"label": "pension", "amount": pytest.approx(-752.50)},
        {"label": "attendance", "amount": pytest.approx(4400.0)},
    ]


def test_a_banded_posting_with_duplicate_allowances_refuses_to_estimate():
    """Real record gazette:407587 -- one iulaan, two ranks. Summing both
    ranks' allowances onto the lower basic produced MVR 51,787 against a real
    figure near 34,000. No estimate beats a fabricated one."""
    comp = Compensation(
        basic_salary=18129, basic_salary_max=20004, salary_state="listed",
        completeness="full",
        allowances=[
            Allowance(kind="other", label_raw="Position Allowance",
                      amount=10382, basis="fixed_monthly"),
            Allowance(kind="other", label_raw="Position Allowance",
                      amount=11456, basis="fixed_monthly"),
            Allowance(kind="attendance", label_raw="Attendance (per day)",
                      amount=281, basis="per_day"),
            Allowance(kind="attendance", label_raw="Attendance (per day)",
                      amount=310, basis="per_day"),
        ],
    )
    assert estimate_net(comp) is None
    assert salary_display(comp) == "MVR 18,129 - 20,004 / month"


def test_a_band_with_distinct_allowance_kinds_still_estimates():
    """A genuine grade band with one allowance each is not two ranks."""
    comp = Compensation(
        basic_salary=8000, basic_salary_max=9000, salary_state="listed",
        completeness="full", pension_applies=True,
        allowances=[Allowance(kind="living", label_raw="Living", amount=1000,
                              basis="fixed_monthly")],
    )
    assert estimate_net(comp) is not None


@pytest.mark.parametrize(
    "comp,expected",
    [
        (Compensation(basic_salary=10750, salary_state="listed"), "MVR 10,750 / month"),
        (Compensation(basic_salary=450, currency="USD", salary_state="listed"),
         "USD 450 / month"),
        (Compensation(basic_salary=500, period="day", salary_state="listed"),
         "MVR 500 / day"),
        (Compensation(basic_salary=8000, basic_salary_max=12000, salary_state="listed"),
         "MVR 8,000 - 12,000 / month"),
        (Compensation(salary_state="negotiable"), "Negotiable"),
        (Compensation(salary_state="unlisted"), "Unlisted"),
        # listed but no number: fall back to Unlisted, never to an empty string
        (Compensation(salary_state="listed"), "Unlisted"),
    ],
)
def test_salary_display_is_always_one_of_three_shapes(comp, expected):
    assert salary_display(comp) == expected
