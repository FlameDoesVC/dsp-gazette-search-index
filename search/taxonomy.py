"""Canonical taxonomy helpers. Spec section 5.

Nothing here reads a source path except through SourceCategoryMap. That is the
whole point of the module: one place decides what a source's category means.
"""

from __future__ import annotations

import hashlib

from django.db.models.signals import post_delete, post_save

from search.models import Category, SourceCategoryMap

TIERS = ("family", "primary", "accessory", "part", "service")

# (source, path_key) -> Category | None.
#
# `map_path` is called once per document from search/indexing.py::_row, and the
# question it asks has only ~306 distinct answers, so the uncached version issues
# one query per row: 20,445 on today's corpus and 5M at the size spec 12 projects.
# Same lifetime reasoning as _OVERLAY_CACHE in indexing.py, except this one is
# invalidated by signals, because a taxonomy edit in the admin must be visible to
# the web process without a restart and the tests build their taxonomy row by row.
_CACHE: dict[tuple[str, str], Category | None] = {}


def clear_cache(*args, **kwargs) -> None:
    """Drop the resolution cache. Connected to Category and SourceCategoryMap
    saves and deletes below; also safe to call directly."""
    _CACHE.clear()


post_save.connect(clear_cache, sender=Category,
                  dispatch_uid="taxonomy_cache_category_save")
post_delete.connect(clear_cache, sender=Category,
                    dispatch_uid="taxonomy_cache_category_delete")
post_save.connect(clear_cache, sender=SourceCategoryMap,
                  dispatch_uid="taxonomy_cache_map_save")
post_delete.connect(clear_cache, sender=SourceCategoryMap,
                    dispatch_uid="taxonomy_cache_map_delete")


def path_key(source: str, path: list[str]) -> str:
    """Stable, order-sensitive, source-scoped key for one category path."""
    joined = "\x1f".join([source, *[str(p).strip() for p in path]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def map_path(source: str, path: list[str]) -> Category | None:
    """The canonical node for a source path, or None.

    None covers three different situations on purpose -- no row, a reviewed row
    with no category, and an inactive category -- because every one of them
    means the same thing downstream: this document has no canonical category.

    A miss is cached as None too. An unmapped path is asked about once per
    document just like a mapped one, and there are only so many of them.
    """
    if not path:
        return None

    cache_key = (source, path_key(source, path))
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    row = (SourceCategoryMap.objects
           .filter(source=source, path_key=cache_key[1])
           .select_related("category")
           .first())
    if row is None or row.category is None or not row.category.is_active:
        _CACHE[cache_key] = None
    else:
        _CACHE[cache_key] = row.category
    return _CACHE[cache_key]


def family_of(category: Category) -> Category:
    node = category
    while node.parent_id is not None and node.tier != "family":
        node = node.parent
    return node


def primary_sibling_of(category: Category) -> Category | None:
    """'iphone' with no modifier wants this; 'iphone charger' wants the
    accessory. One relationship serves both (P10 task 3)."""
    family = family_of(category)
    return family.children.filter(tier="primary", is_active=True).first()
