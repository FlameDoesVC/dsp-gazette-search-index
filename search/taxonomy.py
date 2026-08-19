"""Canonical taxonomy helpers. Spec section 5.

Nothing here reads a source path except through SourceCategoryMap. That is the
whole point of the module: one place decides what a source's category means.
"""

from __future__ import annotations

import hashlib

from search.models import Category, SourceCategoryMap

TIERS = ("family", "primary", "accessory", "part", "service")


def path_key(source: str, path: list[str]) -> str:
    """Stable, order-sensitive, source-scoped key for one category path."""
    joined = "\x1f".join([source, *[str(p).strip() for p in path]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def map_path(source: str, path: list[str]) -> Category | None:
    """The canonical node for a source path, or None.

    None covers three different situations on purpose -- no row, a reviewed row
    with no category, and an inactive category -- because every one of them
    means the same thing downstream: this document has no canonical category.
    """
    if not path:
        return None
    row = (SourceCategoryMap.objects
           .filter(source=source, path_key=path_key(source, path))
           .select_related("category")
           .first())
    if row is None or row.category is None or not row.category.is_active:
        return None
    return row.category


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
