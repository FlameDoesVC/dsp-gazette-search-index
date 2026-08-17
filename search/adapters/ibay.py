"""iBay adapter. P1 maps scraped fields only -- no language model is called
here. doc_type is assigned by the deterministic category prior from spec 5.3;
the LLM classifier arrives in P4."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterator

from ibay.models import Product
from search.adapters.base import DocumentDraft, RawDocument

# Spec 5.3 category priors. Property and job promotion happen here because the
# iBay category tree already carries the signal.
_CATEGORY_DOC_TYPE = {
    "Jobs": "job",
    "Housing & Real Estate": "property",
    "Announcements & Events": "news",
}
_DEFAULT_DOC_TYPE = "shopping"

_BOILERPLATE = re.compile(r"^\s*Description\s*", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _summarize(text: str, limit: int = 240) -> str:
    text = _WS.sub(" ", _BOILERPLATE.sub("", text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


class IbayAdapter:
    key = "ibay"

    def iter_source_keys(self, **filters: Any) -> Iterator[str]:
        qs = Product.objects.all()
        if since := filters.get("since"):
            qs = qs.filter(updated_at__gte=since)
        for listing_id in qs.values_list("listing_id", flat=True).iterator(
            chunk_size=500
        ):
            yield str(listing_id)

    def fetch_raw(self, source_key: str) -> RawDocument | None:
        try:
            product = (
                Product.objects.select_related("seller")
                .prefetch_related("images", "info", "categories")
                .get(listing_id=int(source_key))
            )
        except (Product.DoesNotExist, ValueError):
            return None
        return RawDocument(
            source=self.key,
            source_key=source_key,
            payload={
                "product": product,
                "categories": [c.name for c in product.categories.all()],
                "images": [i.image_url for i in product.images.all()],
                "info": {i.info_key: i.info_value for i in product.info.all()},
            },
        )

    def to_document(self, raw: RawDocument) -> DocumentDraft | None:
        p: Product = raw.payload["product"]
        categories: list[str] = raw.payload["categories"]
        images: list[str] = raw.payload["images"]
        info: dict[str, str] = raw.payload["info"]

        doc_type = _DEFAULT_DOC_TYPE
        for name in categories:
            if name in _CATEGORY_DOC_TYPE:
                doc_type = _CATEGORY_DOC_TYPE[name]
                break

        body = p.description or ""
        text_en = f"{p.name}\n{body}\n" + "\n".join(
            f"{k} {v}" for k, v in info.items()
        )

        return DocumentDraft(
            source=self.key,
            source_key=str(p.listing_id),
            doc_type=doc_type,
            url=p.url,
            title_en=p.name,
            summary_en=_summarize(body),
            text_en=text_en,
            price=p.price,
            currency="MVR",
            location=p.product_location or "",
            is_active=p.status != "ERROR",
            attrs={"category_path": categories, "specs_raw": info},
            card={
                "source": self.key,
                "title": p.name,
                "price_display": f"MVR {p.price:,.0f}" if p.price else None,
                "location": p.product_location or "",
                "hero_image": images[0] if images else None,
                "image_count": len(images),
                "seller_name": p.seller.name if p.seller else "",
                "seller_is_premium": bool(p.seller and p.seller.is_premium),
                "condition": info.get("Item Condition", ""),
                "brand": info.get("Brand", ""),
            },
            thumbnails=images[:5],
            quality=_quality(p, images, info),
            content_hash=hashlib.sha256(text_en.encode()).hexdigest(),
        )


def _quality(product: Product, images: list[str], info: dict[str, str]) -> float:
    """Completeness score in [0, 1]. Feeds ranking (spec 7)."""
    score = 0.0
    score += 0.3 if product.description else 0.0
    score += 0.3 if images else 0.0
    score += 0.2 if product.price is not None else 0.0
    score += 0.2 if info else 0.0
    return round(score, 3)
