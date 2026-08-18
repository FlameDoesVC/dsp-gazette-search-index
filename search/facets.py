"""The static facet registry. Spec 8.1, 8.2, 8.3, 8.4.

Every facet declares three things: the widget the frontend renders, where the
value lives (a SearchDocument column, an attrs JSONB scalar, or an attrs JSONB
array), and its bilingual label. That is enough for both filtering (filters.py)
and counting (the aggregation SQL below).

The dynamic shopping facets are P7. They produce entries of exactly this shape
at request time and are appended to the static list, which is why the API
returns an ordered list rather than a map.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FacetDef:
    key: str
    label_en: str
    label_dv: str
    widget: str                      # checkbox | range | toggle
    storage: str                     # column | attrs | attrs_array
    path: str                        # column name, or dotted attrs path
    unit: str = ""
    top_n: int = 12
    buckets: int = 10
    # Ranges that are only comparable within a group. A rent slider that mixes
    # currencies or periods is meaningless (spec 8.2).
    split_by: list[str] = field(default_factory=list)
    always_available: bool = True


def _f(**kw) -> FacetDef:
    return FacetDef(**kw)


_SOURCE = _f(key="source", label_en="Source", label_dv="މަޞްދަރު",
             widget="checkbox", storage="column", path="source")
_LOCATION = _f(key="location", label_en="Location", label_dv="ތަން",
               widget="checkbox", storage="column", path="location")

JOB_FACETS = [
    _f(key="job_category", label_en="Category", label_dv="ބާވަތް",
       widget="checkbox", storage="attrs", path="job_category"),
    _f(key="position_type", label_en="Position type", label_dv="ވަޒީފާގެ ބާވަތް",
       widget="checkbox", storage="attrs", path="position_type"),
    _f(key="net_estimate", label_en="Take-home", label_dv="ލިބޭ މުސާރަ",
       widget="range", storage="attrs", path="estimated_net_min", unit="MVR"),
    _f(key="salary_state", label_en="Salary", label_dv="މުސާރަ",
       widget="checkbox", storage="attrs", path="compensation.salary_state"),
    _f(key="employer", label_en="Employer", label_dv="ވަޒީފާދޭ ފަރާތް",
       widget="checkbox", storage="attrs", path="employer", top_n=20),
    _f(key="grade", label_en="Grade", label_dv="ގްރޭޑް",
       widget="checkbox", storage="attrs", path="grade"),
    _LOCATION,
    _SOURCE,
]

PROPERTY_FACETS = [
    _f(key="listing_kind", label_en="Listing", label_dv="ބާވަތް",
       widget="checkbox", storage="attrs", path="listing_kind"),
    _f(key="price", label_en="Rent", label_dv="ކުލި", widget="range",
       storage="column", path="price",
       split_by=["currency", "price_period"]),
    _f(key="unit_kind", label_en="Unit", label_dv="ޔުނިޓް",
       widget="checkbox", storage="attrs", path="occupancy.unit_kind"),
    _f(key="is_shared", label_en="Shared", label_dv="ޙިއްޞާކުރެވޭ",
       widget="toggle", storage="attrs", path="occupancy.is_shared"),
    _f(key="bedrooms", label_en="Bedrooms", label_dv="ކޮޓަރި",
       widget="checkbox", storage="attrs", path="bedrooms"),
    _f(key="bathrooms", label_en="Bathrooms", label_dv="ފާޚާނާ",
       widget="checkbox", storage="attrs", path="bathrooms"),
    _f(key="furnishing", label_en="Furnishing", label_dv="ފަރުނީޗަރު",
       widget="checkbox", storage="attrs", path="furnishing"),
    _f(key="neighborhood", label_en="Neighbourhood", label_dv="އަވަށް",
       widget="checkbox", storage="attrs", path="neighborhood", top_n=20),
    _f(key="island", label_en="Island", label_dv="ރަށް",
       widget="checkbox", storage="column", path="island"),
    _f(key="atoll", label_en="Atoll", label_dv="އަތޮޅު",
       widget="checkbox", storage="column", path="atoll"),
    _f(key="has_lift", label_en="Lift", label_dv="ލިފްޓް",
       widget="toggle", storage="attrs", path="has_lift"),
    _f(key="square_feet", label_en="Square feet", label_dv="އަކަފޫޓު",
       widget="range", storage="attrs", path="square_feet", unit="sqft"),
    _f(key="tenant_preference", label_en="Tenants", label_dv="ކުއްޔަށްހިފާ ފަރާތް",
       widget="checkbox", storage="attrs_array", path="tenant_preference"),
    _SOURCE,
]

SHOPPING_FACETS = [
    _f(key="price", label_en="Price", label_dv="އަގު", widget="range",
       storage="column", path="price", split_by=["currency"]),
    _f(key="condition", label_en="Condition", label_dv="ޙާލަތު",
       widget="checkbox", storage="attrs", path="condition"),
    _f(key="brand", label_en="Brand", label_dv="ބްރޭންޑް",
       widget="checkbox", storage="attrs", path="brand", top_n=20),
    _f(key="seller_type", label_en="Seller", label_dv="ވިއްކާ ފަރާތް",
       widget="checkbox", storage="attrs", path="seller_type"),
    _f(key="has_images", label_en="Has photos", label_dv="ފޮޓޯ ހުރި",
       widget="toggle", storage="column", path="thumbnails"),
    _LOCATION,
    _SOURCE,
]

NEWS_FACETS = [
    _SOURCE,
    _f(key="office", label_en="Office", label_dv="އޮފީސް",
       widget="checkbox", storage="attrs", path="office", top_n=20),
    _f(key="announcement_type", label_en="Type", label_dv="ބާވަތް",
       widget="checkbox", storage="attrs", path="announcement_type"),
    _f(key="has_attachments", label_en="Has documents", label_dv="ލިޔުން ހުރި",
       widget="toggle", storage="attrs", path="documents"),
    _f(key="is_tender", label_en="Tender or auction", label_dv="ބީލަން",
       widget="toggle", storage="attrs", path="is_tender"),
]

# The 'all' tab offers only what is meaningful across every type.
ALL_FACETS = [_SOURCE, _LOCATION,
              _f(key="doc_type", label_en="Type", label_dv="ބާވަތް",
                 widget="checkbox", storage="column", path="doc_type")]

FACETS: dict[str, list[FacetDef]] = {
    "job": JOB_FACETS,
    "property": PROPERTY_FACETS,
    "shopping": SHOPPING_FACETS,
    "news": NEWS_FACETS,
    "all": ALL_FACETS,
}


def facet_def(doc_type: str | None, key: str) -> FacetDef | None:
    for f in FACETS.get(doc_type or "all", ALL_FACETS):
        if f.key == key:
            return f
    return None
