"""Take-home estimation. Spec 4.3.2.

Every derived figure in the system comes from here. The language model
extracts line items and nothing else; arithmetic in a prompt is unreliable at
temperature 0 and a wrong take-home figure is precisely the misleading failure
this design forbids.

The estimate is always labelled as an estimate. The card leads with the number
the employer actually stated and shows this as clearly secondary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from enrich.schemas import Compensation

HOURS_PER_DAY = 8


@dataclass(slots=True)
class NetEstimate:
    value: float
    is_floor: bool
    working_days: int
    completeness: str
    breakdown: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "value": round(self.value, 2),
            "is_floor": self.is_floor,
            "working_days": self.working_days,
            "completeness": self.completeness,
            "breakdown": [
                {"label": b["label"], "amount": round(b["amount"], 2)}
                for b in self.breakdown
            ],
        }


def estimate_net(comp: Compensation, working_days: int | None = None) -> NetEstimate | None:
    """Estimated monthly take-home, or None when no honest figure exists.

    None is returned in four cases, all deliberate:
      - no basic salary (unlisted or negotiable): nothing to compute from
      - a non-monthly period: turning a daily wage into a monthly figure
        requires inventing a schedule
      - basic_only with no pension: the result would just restate the number
        already displayed above it (spec 8.1)
      - completeness 'none'
    """
    if working_days is None:
        working_days = settings.DEFAULT_WORKING_DAYS

    if comp.basic_salary is None:
        return None
    if comp.period != "month":
        return None

    basic = float(comp.basic_salary)
    breakdown = [{"label": "basic", "amount": basic}]

    pension = 0.0
    if comp.pension_applies:
        rate = comp.pension_rate or settings.PENSION_RATE
        base = basic if settings.PENSION_BASE == "basic" else basic
        pension = base * rate
        breakdown.append({"label": "pension", "amount": -pension})

    added = 0.0
    for a in comp.allowances:
        if a.amount is None:
            continue
        if a.basis == "fixed_monthly":
            amount = float(a.amount)
        elif a.basis == "per_day":
            amount = float(a.amount) * working_days
        elif a.basis == "per_hour":
            amount = float(a.amount) * HOURS_PER_DAY * working_days
        elif a.basis == "percent_of_basic":
            amount = basic * float(a.amount) / 100.0
        else:                                    # unreachable: Literal-typed
            continue
        added += amount
        breakdown.append({"label": a.kind, "amount": amount})

    if comp.completeness == "basic_only" and pension == 0.0 and added == 0.0:
        # Nothing to say that the stated salary does not already say.
        return None

    return NetEstimate(
        value=basic - pension + added,
        is_floor=comp.completeness == "partial",
        working_days=working_days,
        completeness=comp.completeness,
        breakdown=breakdown,
    )


def salary_display(comp: Compensation) -> str:
    """One of three strings, never a null the frontend has to interpret.

    'Negotiable' appears only when the source said so; absence is 'Unlisted'.
    Spec 8.1.
    """
    if comp.salary_state == "negotiable":
        return "Negotiable"
    if comp.salary_state != "listed" or not comp.basic_salary:
        return "Unlisted"

    cur = comp.currency or "MVR"
    lo = f"{comp.basic_salary:,.0f}"
    if comp.basic_salary_max and comp.basic_salary_max > comp.basic_salary:
        hi = f"{comp.basic_salary_max:,.0f}"
        return f"{cur} {lo} - {hi} / {comp.period}"
    return f"{cur} {lo} / {comp.period}"
