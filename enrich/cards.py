"""Card payload builders. Spec 8.1 through 8.5.

The `card` JSONB carries exactly what its card component renders, already
resolved, so the frontend does no formatting decisions and no joins.

Two constraints hold across all four builders:

- Nothing time-dependent. `card` stores raw dates; deadline_state, freshness
  and relative-time labels are computed per request. A gazette document is
  written once and never reprocessed, so a frozen "3 days left" is wrong the
  next morning and a frozen "open" is wrong forever.
- The source key, never an icon path. The frontend resolves it against /meta.
"""

from __future__ import annotations

from enrich.compensation import estimate_net, salary_display
from enrich.schemas import Occupancy, Spec
from search.vocab import label

# Bump when a card's field set changes. Triggers a reindex rather than a
# runtime lookup. Spec 8.
CARD_VERSION = 1


def _money(value, currency="MVR") -> str | None:
    if value is None:
        return None
    return f"{currency} {float(value):,.0f}"


def capacity_display(occ: Occupancy) -> str:
    """The field most likely to mislead if done carelessly, so it states the
    shape explicitly rather than reducing everything to a room count."""
    kind = occ.unit_kind
    shared = ", shared" if occ.is_shared else ""

    if kind == "room":
        if occ.rooms_offered and occ.rooms_total:
            return f"{occ.rooms_offered} room of {occ.rooms_total}{shared}"
        if occ.rooms_offered:
            return f"{occ.rooms_offered} room{shared}"
        return f"Room{shared}"

    if kind == "bed_space":
        if occ.beds_offered:
            return f"Bed space, {occ.beds_offered} available{shared}"
        return f"Bed space{shared}"

    if kind == "guest_house":
        if occ.max_occupants:
            return f"Guest house room, up to {occ.max_occupants}"
        return "Guest house room"

    if kind == "whole_unit":
        if occ.rooms_total:
            return f"Whole unit, {occ.rooms_total} rooms"
        return "Whole unit"

    if kind == "land":
        return "Land"
    if kind == "commercial":
        return "Commercial space"
    return "Whole unit"


def rent_display(price, currency: str, period: str) -> str:
    if price is None:
        return "Price on request"
    return f"{currency or 'MVR'} {float(price):,.0f} / {period or 'month'}"


def spec_chips(specs: list[Spec], limit: int = 3) -> list[str]:
    """Up to three compact chips: '24V', '120W', '128GB'."""
    out: list[str] = []
    for s in specs:
        if s.value_num is None or not s.unit:
            continue
        n = int(s.value_num) if float(s.value_num).is_integer() else s.value_num
        out.append(f"{n}{s.unit}")
        if len(out) >= limit:
            break
    return out


def _job_card(a, base: dict) -> dict:
    est = estimate_net(a.compensation)
    return {
        "source": base.get("source", ""),
        "role": a.role or base.get("title", ""),
        "employer": a.employer or base.get("employer", ""),
        "employer_logo": base.get("employer_logo"),
        "salary_display": salary_display(a.compensation),
        "salary_state": a.compensation.salary_state,
        "net_estimate": est.as_dict() if est else None,
        "compensation": a.compensation.model_dump(),
        "grade": a.grade,
        "location": base.get("location", ""),
        "position_type": a.position_type,
        "position_type_label": label("position_type", a.position_type),
        "job_category_label": label("job_category", a.job_category),
        "required_documents": a.required_documents,
        # raw date only; state is computed at query time
        "deadline": a.deadline,
        "apply_kinds": [m.kind for m in a.apply_methods],
        "detail_source": base.get("detail_source", "listing"),
        "source_label": base.get("source_label", ""),
    }


def _property_card(a, base: dict) -> dict:
    return {
        "source": base.get("source", ""),
        "hero_image": base.get("hero_image"),
        "image_count": base.get("image_count", 0),
        "location_display": base.get("location", "") or a.neighborhood,
        "rent_display": rent_display(base.get("price"), base.get("currency", "MVR"),
                                     a.price_period),
        "currency": base.get("currency", "MVR"),
        "currency_inferred": a.currency_inferred,
        "capacity_display": capacity_display(a.occupancy),
        "unit_kind": a.occupancy.unit_kind,
        "is_shared": a.occupancy.is_shared,
        "bedrooms": a.bedrooms,
        "bathrooms": a.bathrooms,
        "furnishing": a.furnishing,
        "tenant_preference": a.tenant_preference or a.occupancy.tenant_preference,
        "listing_kind": a.listing_kind,
        "listing_kind_label": label("listing_kind", a.listing_kind),
    }


def _shopping_card(a, base: dict) -> dict:
    return {
        "source": base.get("source", ""),
        "hero_image": base.get("hero_image"),
        "image_count": base.get("image_count", 0),
        "title": base.get("title", ""),
        "price_display": _money(base.get("price"), base.get("currency", "MVR")),
        "currency": base.get("currency", "MVR"),
        "negotiable": a.negotiable,
        "condition": a.condition,
        "condition_label": label("condition", a.condition),
        "brand": a.brand,
        "location": base.get("location", ""),
        "seller_name": base.get("seller_name", ""),
        "seller_is_premium": base.get("seller_is_premium", False),
        "spec_chips": spec_chips(a.specs),
    }


def _news_card(a, base: dict) -> dict:
    """Four things and nothing else: icon, title, excerpt, link out. The rest
    is context that costs nothing to carry."""
    return {
        "source": base.get("source", ""),
        "title": base.get("title", ""),
        "summary": base.get("summary", ""),
        "office": a.office,
        "announcement_type": a.announcement_type,
        "published_at": base.get("published_at"),
        "external_url": base.get("external_url", ""),
        "attachment_count": base.get("attachment_count", 0),
        "is_tender": a.is_tender,
    }


_BUILDERS = {
    "job": _job_card,
    "property": _property_card,
    "shopping": _shopping_card,
    "news": _news_card,
}


def build_card(doc_type: str, attrs_model, *, base: dict) -> dict:
    return _BUILDERS.get(doc_type, _news_card)(attrs_model, base)
