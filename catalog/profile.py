"""Stage 2: one model call per entity. Spec section 8.

Per entity, not per document: the collapse ratios measured in the spec (6.13:1
for services, 1.87:1 for products) turn 16,608 document calls into about 5,300
entity calls, and an entity is also the only scope at which a spec sheet is
meaningful.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from asgiref.sync import sync_to_async
from django.conf import settings

from catalog.models import Entity, EntityField, EntityLink
from catalog.prompts import PROFILE_PROMPT_VERSION, build_profile_messages
from catalog.schemas import EntityProfileOutput
from catalog.tiers import classify_origin
from enrich.client import EnrichClient, ProviderError
from enrich.preextract import extract_candidates
from search.models import Category, SearchDocument, SpecKey
from search.specs.project import slugify_key

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProfileInput:
    entity_id: int
    kind: str
    identity: dict
    categories: list[str]
    listings: list[str] = field(default_factory=list)
    union_text: str = ""


def build_profile_input(entity: Entity) -> ProfileInput | None:
    keys = list(EntityLink.objects.filter(entity=entity)
                .values_list("source", "source_key"))
    if not keys:
        return None

    listings: list[str] = []
    for source, source_key in keys[: settings.CATALOG_PROFILE_MAX_LISTINGS]:
        doc = (SearchDocument.objects
               .filter(source=source, source_key=source_key)
               .only("title_en", "summary_en", "attrs").first())
        if doc is None:
            continue
        scraped = " ".join(f"{k}: {v}" for k, v in
                           (doc.attrs.get("specs_raw") or {}).items())
        listings.append(" ".join(p for p in (doc.title_en, doc.summary_en,
                                             scraped) if p)[:600])

    if not listings:
        return None

    union = "\n".join(listings)[: settings.ENRICH_MAX_INPUT_CHARS]
    identity = {"brand": entity.brand, "model_name": entity.model_name,
                "service_type": entity.service_type,
                "provider_listings": len(keys)}
    # The closed registry the model must choose from (spec section 5).
    categories = list(Category.objects.filter(is_active=True)
                      .values_list("key", flat=True))
    return ProfileInput(entity_id=entity.id, kind=entity.kind,
                        identity=identity, categories=categories,
                        listings=listings, union_text=union)


def _store_fields(entity: Entity, out: EntityProfileOutput,
                  inp: ProfileInput) -> int:
    """Write EntityField rows for this pass, replacing only the tiers stage 2
    owns. `scraped` and `correction` rows are never touched here."""
    candidates = extract_candidates(inp.union_text)
    registry = {k.key: k for k in SpecKey.objects.all()}

    EntityField.objects.filter(
        entity=entity, provenance__in=("grounded", "inferred")).delete()

    rows: list[EntityField] = []
    seen: set[tuple] = set()

    def push(key_raw, *, claimed, value_num=None, value_text="", unit=""):
        key_raw = slugify_key(key_raw)
        if not key_raw:
            return
        ident = (key_raw, value_num, value_text)
        if ident in seen:
            return
        seen.add(ident)
        provenance = classify_origin(claimed=claimed, value_num=value_num,
                                     value_text=value_text,
                                     union_text=inp.union_text,
                                     candidates=candidates, key_raw=key_raw)
        rows.append(EntityField(
            entity=entity, key_raw=key_raw,
            key=registry.get(key_raw), value_num=value_num,
            value_text=value_text[:128], unit=unit[:16],
            provenance=provenance,
            confidence=entity.identity_confidence))

    if out.product is not None:
        if out.product.brand:
            push("brand", claimed="from_listings", value_text=out.product.brand)
        for spec in out.product.specs:
            push(spec.key_raw, claimed=spec.origin, value_num=spec.value_num,
                 value_text=spec.value_text, unit=spec.unit)

    if out.service is not None:
        s = out.service
        for value in s.services_offered:
            push("service_offered", claimed="from_listings", value_text=value)
        for value in s.coverage:
            push("coverage", claimed="from_listings", value_text=value)
        if s.call_out is not None:
            push("call_out", claimed="from_listings",
                 value_text="yes" if s.call_out else "no")
        if s.shop_visit is not None:
            push("shop_visit", claimed="from_listings",
                 value_text="yes" if s.shop_visit else "no")
        if s.rate_basis:
            push("rate_basis", claimed="from_listings", value_text=s.rate_basis)

    EntityField.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)
    return len(rows)


async def profile_one(inp: ProfileInput, client) -> Entity:
    """One entity, one profile. Never raises: a failure is a stored status."""

    def _messages(repair_error=None):
        return build_profile_messages(
            kind=inp.kind, identity=inp.identity, categories=inp.categories,
            listings=inp.listings, repair_error=repair_error)

    entity = await sync_to_async(Entity.objects.get)(id=inp.entity_id)
    entity.profile_prompt_version = PROFILE_PROMPT_VERSION

    try:
        payload, model_name = await client.run_chain(
            _messages(), rebuild=lambda err: _messages(repair_error=err))
    except ProviderError as exc:
        entity.profile_status = "failed"
        entity.profile_error = str(exc)[:2000]
        await sync_to_async(entity.save)()
        return entity

    out = (EntityProfileOutput(**payload) if isinstance(payload, dict)
           else EntityProfileOutput())

    entity.title_en = out.title_en[:256]
    entity.title_dv = out.title_dv[:256]
    entity.summary_en = out.summary_en[:240]
    entity.summary_dv = out.summary_dv[:240]

    # The model picks from the registry or gets ignored. It never creates one.
    if out.category_key:
        category = await sync_to_async(
            lambda: Category.objects.filter(key=out.category_key,
                                            is_active=True).first())()
        if category is not None:
            entity.category = category

    if out.product is not None:
        entity.brand = out.product.brand[:64] or entity.brand
        entity.model_name = out.product.model_name[:128] or entity.model_name
        entity.variant = out.product.variant[:64]
    if out.service is not None and out.service.service_type:
        entity.service_type = out.service.service_type[:64] or entity.service_type

    entity.profile_status = "ok"
    entity.profile_error = ""
    await sync_to_async(entity.save)()
    await sync_to_async(_store_fields)(entity, out, inp)
    return entity


def select_entity_ids(*, kind=None, force=False, limit=None) -> list[int]:
    qs = Entity.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    if not force:
        qs = qs.exclude(profile_status="ok",
                        profile_prompt_version__gte=PROFILE_PROMPT_VERSION)
    qs = qs.order_by("-listing_count")
    ids = list(qs.values_list("id", flat=True))
    return ids[:limit] if limit else ids


async def run_profile_pass(entity_ids: list[int], *, concurrency=None) -> dict:
    sem = asyncio.Semaphore(concurrency or settings.ENRICH_CONCURRENCY)
    client = EnrichClient()
    counts = {"ok": 0, "failed": 0, "skipped": 0}

    async def _one(entity_id: int):
        async with sem:
            entity = await sync_to_async(Entity.objects.get)(id=entity_id)
            inp = await sync_to_async(build_profile_input)(entity)
            if inp is None:
                counts["skipped"] += 1
                return
            result = await profile_one(inp, client)
            counts[result.profile_status] = counts.get(
                result.profile_status, 0) + 1

    try:
        await asyncio.gather(*(_one(i) for i in entity_ids))
    finally:
        await client.aclose()
    return counts
