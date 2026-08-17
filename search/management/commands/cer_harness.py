"""Measure transcription accuracy against text-layer PDFs. Spec 5.6.1.

Run before committing to a transcription model, and again whenever the model
changes. This decides the model empirically rather than by reputation.
"""

import random

from django.core.management.base import BaseCommand

from gazette.models import Attachment
from search.extract import local, transcribe
from search.extract.cer import char_error_rate
from search.extract.fetch import fetch_bytes


class Command(BaseCommand):
    help = "Measure CER of the transcription path against text-layer PDFs."

    def add_arguments(self, parser):
        parser.add_argument("--sample", type=int, default=20)
        parser.add_argument("--seed", type=int, default=20260818)

    def handle(self, *args, **options):
        candidates = list(
            Attachment.objects.filter(
                status="ok", method="pdftotext", transcribed=False
            ).exclude(text="")[:500]
        )
        dense = [a for a in candidates if (a.chars_per_page or 0) >= 500]
        if not dense:
            self.stdout.write(
                self.style.ERROR(
                    "no text-layer attachments available; run "
                    "extract_attachments first"
                )
            )
            return

        rng = random.Random(options["seed"])
        rng.shuffle(dense)
        sample = dense[: options["sample"]]
        self.stdout.write(f"sampling {len(sample)} text-layer PDFs")

        items, references = [], {}
        for attachment in sample:
            fetched = fetch_bytes(attachment.url)
            if not fetched:
                continue
            content, _sha = fetched
            items.append(
                transcribe.TranscriptionItem(
                    custom_id=str(attachment.id), content=content
                )
            )
            references[str(attachment.id)] = attachment.text

        results = transcribe.transcribe_batch(items)

        rates = []
        for custom_id, result in results.items():
            if result.status != "ok":
                self.stdout.write(f"  {custom_id}: FAILED {result.error}")
                continue
            rate = char_error_rate(references[custom_id], result.text)
            rates.append(rate)
            self.stdout.write(f"  {custom_id}: CER {rate:.3f}")

        if not rates:
            self.stdout.write(self.style.ERROR("no successful transcriptions"))
            return

        rates.sort()
        mean = sum(rates) / len(rates)
        self.stdout.write(
            self.style.SUCCESS(
                f"n={len(rates)} mean={mean:.3f} "
                f"median={rates[len(rates) // 2]:.3f} "
                f"p90={rates[int(len(rates) * 0.9)]:.3f} max={rates[-1]:.3f}"
            )
        )
        self.stdout.write(
            "Set TRANSCRIBE_MAX_CER above the median and below the tail, and "
            "record these numbers in docs/superpowers/measurements/."
        )
