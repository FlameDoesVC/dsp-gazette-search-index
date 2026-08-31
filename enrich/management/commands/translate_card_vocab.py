"""Build the Dhivehi side of the recurring per-document strings that a `job`
card's free-text fields carry: allowance names, required documents, apply
labels, qualifications, role, employer.

None of these are a closed vocabulary (search/vocab.py) -- an office can
phrase "Copies of educational certificates and transcripts" a dozen ways --
but in practice they recur heavily across postings, because offices reuse
their own boilerplate. So this collects the corpus's *current* unique set,
translates whichever of them are not already in TranslationCache, and stops
-- the cache absorbs the rest, and running this again after new documents
land only pays for what is actually new. That "run it again for the amends"
step is meant to be routine, not a one-off backfill, so it is cheap by
construction rather than by discipline.

Always the general ladder (core.translate's ollama -> openrouter -> gemini),
never a dedicated small model. A dedicated translation model was tried first
here and measured to produce fluent-*looking* but simply wrong Dhivehi on a
large fraction of both short labels ("Special Duty Allowance" ->
non-existent Dhivehi words) and long compound sentences -- valid script,
invented meaning, which `_is_clean_translation`'s script check cannot catch
because nothing about the output looks malformed. That failure mode showed up
repeatedly across both categories, not just the long ones, so there is no
safe subset left to hand it: the general ladder's first rung (Ollama with the
normal chat model) is still local and free, it is just measurably more
reliable than the dedicated model was on this content.

Card display resolves the Dhivehi side at request time from the cache
(search/vocab.py::annotate_free_text) -- nothing here writes to `card` or
`EnrichedRecord.attrs`, so there is no reindex dependency.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from enrich.models import EnrichedRecord

_USABLE = ("ok", "needs_review")
MIN_CHARS = 3


def _harvest(attrs: dict) -> set[str]:
    found: set[str] = set()

    def add(value):
        if value and isinstance(value, str) and len(value.strip()) >= MIN_CHARS:
            found.add(value.strip())

    add(attrs.get("role"))
    add(attrs.get("employer"))
    for q in attrs.get("qualifications") or []:
        add(q)
    for d in attrs.get("required_documents") or []:
        add(d)
    comp = attrs.get("compensation") or {}
    for allowance in comp.get("allowances") or []:
        add(allowance.get("label_raw"))
    for method in attrs.get("apply_methods") or []:
        add(method.get("label_en"))

    return found


class Command(BaseCommand):
    help = ("Translate recurring per-document job-card strings (allowances, "
            "required documents, apply labels, qualifications, role) into a "
            "reusable Dhivehi language pack.")

    def add_arguments(self, parser):
        parser.add_argument("--source", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from core.translate import cached_translations, translate_batch_sync

        qs = EnrichedRecord.objects.filter(doc_type="job", status__in=_USABLE)
        if opts["source"]:
            qs = qs.filter(source=opts["source"])

        strings: set[str] = set()
        for record in qs.iterator(chunk_size=200):
            strings |= _harvest(record.attrs or {})

        strings = sorted(strings)
        already = cached_translations(strings)
        missing = [s for s in strings if s not in already]

        self.stdout.write(
            f"{len(strings)} unique strings, {len(missing)} not yet translated")
        if opts["dry_run"] or not missing:
            return

        translated = translate_batch_sync(missing, target="dv")
        n = sum(1 for orig, t in zip(missing, translated) if t and t != orig)
        self.stdout.write(self.style.SUCCESS(
            f"{n} of {len(missing)} translated and cached"))
