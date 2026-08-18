"""The registry endpoint. Spec 4.3.3, 8.5, 9.

Nothing about tabs, labels or icons is hardcoded in the frontend. `card`
payloads carry a source key and the browser resolves it here, once per
session, which is why a card never issues its own request for an icon.
"""

from ninja import Router

from api.schemas import MetaOut
from search.models import Source

router = Router()

# The six tabs from spec 8. 'all' interleaves types; 'images' runs the same
# query and flattens thumbnails. Neither maps to a single doc_type.
TABS = [
    {"key": "all", "label_en": "All", "label_dv": "ހުރިހާ", "doc_type": None},
    {"key": "shopping", "label_en": "Shopping", "label_dv": "ވިޔަފާރި",
     "doc_type": "shopping"},
    {"key": "job", "label_en": "Jobs", "label_dv": "ވަޒީފާ", "doc_type": "job"},
    {"key": "property", "label_en": "Property", "label_dv": "ބިންވެރި",
     "doc_type": "property"},
    {"key": "news", "label_en": "News", "label_dv": "ޚަބަރު", "doc_type": "news"},
    {"key": "images", "label_en": "Images", "label_dv": "ފޮޓޯ", "doc_type": None},
]

SORTS = ["relevance", "newest", "price_asc", "price_desc", "salary_desc"]


@router.get("/meta", response=MetaOut)
def meta(request):
    sources = [
        {
            "key": s.key,
            "label_en": s.label_en,
            "label_dv": s.label_dv or s.label_en,
            "icon": s.icon,
            "icon_fallback_text": s.icon_fallback_text,
            "accent": s.accent,
            "site_url": s.site_url,
        }
        for s in Source.objects.filter(is_active=True).order_by("key")
    ]
    return {
        "tabs": TABS,
        "sources": sources,
        "doc_types": ["shopping", "job", "property", "news"],
        "sorts": SORTS,
    }
