"""Seed Brand from the brand values already in DocumentSpec.

P7's scraped-ProductInfo projection already holds 35 distinct brands on 2,313
listings. Paying a model to re-derive a vocabulary the source gave us would be
the mistake spec 4.4 was written to avoid.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import Brand
from search.models import DocumentSpec


# (canonical name, aliases). Recovered-listing counts from the 2026-08-19 miss
# analysis are in the comment beside each.
CURATED: list[tuple[str, tuple[str, ...]]] = [
    ("JBL", ()),                       # 131
    ("DJI", ()),                       # 45
    ("Marshall", ()),                  # 38
    ("Nintendo", ("switch",)),         # 35
    ("Sharp", ()),                     # 34
    ("Philips", ()),                   # 29
    ("Midea", ()),                     # 21
    ("Geepas", ()),                    # 18
    ("Boss", ()),                      # 17
    ("Amazfit", ("amazefit",)),        # 17
    ("Anker", ()),                     # 16
]


def _split_alias(raw: str) -> tuple[str, list[str]]:
    """'Apple (iPhone)' -> ('Apple', ['iPhone', 'Apple (iPhone)']).

    Both forms are kept: the contents are what a title actually says, and the
    raw string is what the scraped field says, so matching either is correct.
    """
    if "(" not in raw:
        return raw, []
    base = raw.split("(")[0].strip() or raw
    inner = raw[raw.index("(") + 1:].split(")")[0].strip()
    aliases = [a for a in (inner, raw) if a and a.lower() != base.lower()]
    return base, aliases


class Command(BaseCommand):
    help = "Seed the Brand vocabulary from DocumentSpec brand values."

    def handle(self, *args, **opts):
        rows = (DocumentSpec.objects.filter(key_raw="brand")
                .exclude(value_text="")
                .values("value_text")
                .annotate(n=Count("id")).order_by("-n"))
        created = 0
        for row in rows:
            name = row["value_text"].strip()
            if not name or len(name) > 64:
                continue
            # 'Apple (iPhone)' and 'Apple' are the same brand, so the
            # parenthetical becomes an alias rather than a second brand -- and
            # the alias is its CONTENTS, 'iPhone', not the whole raw string.
            # Storing 'Apple (iPhone)' verbatim matches nothing: measured, 60
            # For Sale titles begin with the word iPhone.
            base, aliases = _split_alias(name)
            brand, was_created = Brand.objects.get_or_create(name=base)
            created += int(was_created)
            new = [a for a in aliases if a and a not in (brand.aliases or [])]
            if new:
                brand.aliases = [*(brand.aliases or []), *new]
                brand.save(update_fields=["aliases"])
            self.stdout.write(f"{row['n']:5d}  {base}")

        # Brands the corpus uses that DocumentSpec cannot supply, because the
        # scraped `Brand` field is only populated on 2,313 of 7,105 For Sale
        # listings. Hand-verified from the most frequent leading word among the
        # listings that resolved to no identity at all; the counts are how many
        # such listings each one recovers. Frequency alone is not the test --
        # 'SMART', 'USB', 'HOTEL' and 'UNIVERSAL' rank just as high and are not
        # brands.
        for name, aliases in CURATED:
            brand, was_created = Brand.objects.get_or_create(
                name=name, defaults={"aliases": list(aliases)})
            created += int(was_created)
            if not was_created:
                new = [a for a in aliases if a not in (brand.aliases or [])]
                if new:
                    brand.aliases = [*(brand.aliases or []), *new]
                    brand.save(update_fields=["aliases"])

        self.stdout.write(self.style.SUCCESS(
            f"{created} brands created, {Brand.objects.count()} total"))
