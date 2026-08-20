"""Typed attribute schemas. Spec 4.3, 4.3.1, 4.3.2.

One Pydantic model per doc_type is the single source of truth for five
consumers: the JSON schema sent to the provider, database validation, the
facet registry, the API response type, and the generated TypeScript types.

Everything is optional. Spec 5.2 layer 5: the prompt instructs omission over
guessing, so a null field is correct behavior and a plausible invention is a
bug. There is no field here whose absence should fail a parse.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Base(BaseModel):
    # Extra keys are dropped rather than raising: a provider that invents a
    # field should lose the field, not the whole record.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_absent(cls, value, info):
        """A null where a plain string is expected means the model had nothing
        to say, and that is the same claim as an empty string.

        Same reasoning as extra="ignore" above, in the other direction. The
        prompt tells the model to prefer null over a guess, so rejecting
        `"summary_dv": null` on an English-only listing punishes it for
        following instructions. gemmatranslate:12b writes null for both Dhivehi
        fields on every English listing, which cost the whole record: 8 of 8
        documents failed validation with usable extractions inside them.

        Restricted to fields annotated exactly `str`. A nullable number means
        something different -- `basic_salary: null` is "not stated", and "" is
        not a salary -- so those keep their None and fail loudly if they are
        ever given a string.
        """
        if value is not None:
            return value
        field = cls.model_fields.get(info.field_name)
        return "" if field is not None and field.annotation is str else value


# --------------------------------------------------------------------------
# shared parts
# --------------------------------------------------------------------------

class Contact(_Base):
    kind: Literal["phone", "mobile", "landline", "viber", "whatsapp",
                  "email", "url"] = "phone"
    value: str = ""
    label_raw: str = ""


class ApplyMethod(_Base):
    kind: Literal["form", "email", "phone", "viber", "whatsapp",
                  "portal", "walk_in", "post"] = "email"
    value: str = ""
    label_en: str = ""
    label_dv: str = ""


class Spec(_Base):
    """One extracted attribute. Mirrors DocumentSpec (spec 4.4) but lives in
    the model output; P7 is what turns these rows into facets."""

    key_raw: str = ""
    value_num: float | None = None
    value_text: str = ""
    unit: str = ""


# --------------------------------------------------------------------------
# job
# --------------------------------------------------------------------------

class Allowance(_Base):
    kind: Literal["service", "living", "attendance", "ration", "phone",
                  "risk", "transport", "overtime", "other"] = "other"
    label_raw: str = ""
    amount: float | None = None
    basis: Literal["fixed_monthly", "per_day", "per_hour",
                   "percent_of_basic"] = "fixed_monthly"


class Compensation(_Base):
    basic_salary: float | None = None
    basic_salary_max: float | None = None      # grade bands quote a range
    currency: str = "MVR"
    period: Literal["month", "day", "hour", "year"] = "month"
    allowances: list[Allowance] = Field(default_factory=list)
    # Only when the ad says so. A silent ad is not evidence of a pension.
    pension_applies: bool = False
    pension_rate: float = 0.07
    # Three-way, not a nullable number: the card must distinguish "Negotiable"
    # from "Unlisted" and those are different claims. Spec 4.3.
    salary_state: Literal["listed", "negotiable", "unlisted"] = "unlisted"
    completeness: Literal["full", "partial", "basic_only", "none"] = "none"


class JobAttrs(_Base):
    role: str = ""                 # the job title alone, no employer, no boilerplate
    employer: str = ""
    position_type: str = ""        # Permanent | Contract | Temporary | Part-time
    job_category: str = ""
    grade: str = ""                # civil service rank: GS3, MS1
    compensation: Compensation = Field(default_factory=Compensation)
    qualifications: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    deadline: str = ""             # ISO date; validated in validate.py
    apply_methods: list[ApplyMethod] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    vacancies: int | None = None


# --------------------------------------------------------------------------
# property
# --------------------------------------------------------------------------

class Occupancy(_Base):
    """Occupancy is not a bedroom count. Spec 4.3.1.

    A listing offering one room of three must never render as a three-bedroom
    unit, which is why rooms_offered and rooms_total are separate fields.
    """

    unit_kind: Literal["whole_unit", "room", "bed_space",
                       "guest_house", "land", "commercial"] = "whole_unit"
    rooms_offered: int | None = None
    rooms_total: int | None = None
    beds_offered: int | None = None
    max_occupants: int | None = None
    is_shared: bool = False
    shared_facilities: list[str] = Field(default_factory=list)
    tenant_preference: list[str] = Field(default_factory=list)


class PropertyAttrs(_Base):
    listing_kind: Literal["rent", "sale", "wanted"] = "rent"
    unit_kind: str = ""
    occupancy: Occupancy = Field(default_factory=Occupancy)
    bedrooms: int | None = None
    bedrooms_or_more: bool = False        # '4 Rooms and More'
    bathrooms: int | None = None
    square_feet: float | None = None
    floor: str = ""
    furnishing: str = ""
    neighborhood: str = ""
    has_lift: bool | None = None
    room_facilities: list[str] = Field(default_factory=list)
    tenant_preference: list[str] = Field(default_factory=list)
    price_period: Literal["month", "day", "year"] = "month"
    currency_inferred: bool = False
    contacts: list[Contact] = Field(default_factory=list)


# --------------------------------------------------------------------------
# shopping
# --------------------------------------------------------------------------

class ShoppingAttrs(_Base):
    condition: str = ""
    brand: str = ""
    model: str = ""
    category_path: list[str] = Field(default_factory=list)
    quantity: int | None = None
    delivery: str = ""
    seller_type: str = ""
    negotiable: bool | None = None
    contacts: list[Contact] = Field(default_factory=list)
    specs: list[Spec] = Field(default_factory=list)


# --------------------------------------------------------------------------
# news -- the default sink (spec 5.3)
# --------------------------------------------------------------------------

class NewsAttrs(_Base):
    office: str = ""
    announcement_type: str = ""
    reference_no: str = ""
    deadline: str = ""
    tender_fee: float | None = None
    documents: list[str] = Field(default_factory=list)
    is_tender: bool = False


ATTRS_FOR_TYPE: dict[str, type[_Base]] = {
    "job": JobAttrs,
    "property": PropertyAttrs,
    "shopping": ShoppingAttrs,
    "news": NewsAttrs,
}


class EnrichmentOutput(_Base):
    """The whole model response. `attrs` stays a raw dict here and is parsed
    into the per-type model afterwards, so a bad `attrs` does not cost us the
    title and summary the news card depends on."""

    doc_type: Literal["job", "property", "shopping", "news"] = "news"
    doc_type_confidence: float = 0.0
    canonical_title_en: str = ""
    canonical_title_dv: str = ""
    summary_en: str = ""
    summary_dv: str = ""
    keywords: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)


_SCHEMA_CACHE: dict[str, str] = {}


def schema_text(doc_type: str) -> str:
    """The JSON schema, pasted into the prompt verbatim.

    Cached and sorted: the prefix must be byte-identical on every call or
    DeepSeek's context cache misses and the input cost triples (spec 5.1).
    """
    if doc_type not in _SCHEMA_CACHE:
        model = ATTRS_FOR_TYPE[doc_type]
        _SCHEMA_CACHE[doc_type] = json.dumps(
            model.model_json_schema(), sort_keys=True, ensure_ascii=False
        )
    return _SCHEMA_CACHE[doc_type]
