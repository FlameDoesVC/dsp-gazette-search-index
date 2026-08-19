"""Stage-2 output schemas. Spec sections 8.1 and 8.2.

Products get a spec sheet; services get a shape of their own, because a spec
sheet is the wrong model for 'this person will come and fix your aircon'.
Everything is optional: an omitted field is correct behaviour, exactly as in
enrich/schemas.py.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ORIGINS = ("from_listings", "from_knowledge")


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ProfileSpec(_Base):
    key_raw: str = ""
    value_num: float | None = None
    value_text: str = ""
    unit: str = ""
    # Checked by catalog/tiers.py, never trusted.
    origin: Literal["from_listings", "from_knowledge"] = "from_knowledge"


class ProductProfile(_Base):
    brand: str = ""
    model_name: str = ""
    variant: str = ""
    specs: list[ProfileSpec] = Field(default_factory=list)


class ServiceProfile(_Base):
    service_type: str = ""
    services_offered: list[str] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    call_out: bool | None = None
    shop_visit: bool | None = None
    rate_basis: Literal["per_job", "per_hour", "per_visit",
                        "quote_only"] = "quote_only"
    availability: str = ""


class EntityProfileOutput(_Base):
    title_en: str = ""
    title_dv: str = ""
    summary_en: str = ""
    summary_dv: str = ""
    # A key from the registry, or empty. The model never invents one.
    category_key: str = ""
    product: ProductProfile | None = None
    service: ServiceProfile | None = None


_SCHEMA_CACHE: dict[str, str] = {}


def schema_text(kind: str) -> str:
    """Byte-identical per kind, so the provider's context cache keeps hitting
    (spec 5.1)."""
    if kind not in _SCHEMA_CACHE:
        model = ProductProfile if kind == "product" else ServiceProfile
        _SCHEMA_CACHE[kind] = json.dumps(model.model_json_schema(),
                                         sort_keys=True, ensure_ascii=False)
    return _SCHEMA_CACHE[kind]
