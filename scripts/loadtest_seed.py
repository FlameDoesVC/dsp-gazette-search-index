"""Seed synthetic documents to size the index. Spec 12.7.

Run:  ./venv/bin/python scripts/loadtest_seed.py 100000
The vocabulary is deliberately Zipf-ish rather than uniform, because GIN index
size depends heavily on vocabulary shape and uniform random words would give a
falsely reassuring answer.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "beynunehcheh.settings")
django.setup()

from search.adapters.base import DocumentDraft  # noqa: E402
from search.indexing import upsert_drafts  # noqa: E402

WORDS = (
    "ministry notice tender vacancy apartment iphone samsung rent island "
    "council announcement auction salary allowance office delivery brand new "
    "used furniture laptop camera boat engine service repair machine phone"
).split()
TYPES = ["shopping", "job", "news", "property"]


def phrase(rng: random.Random, n: int) -> str:
    # Zipf-weighted so common words dominate, as in real corpora.
    return " ".join(
        WORDS[min(int(rng.paretovariate(1.2)) - 1, len(WORDS) - 1)]
        for _ in range(n)
    )


def main(total: int, batch_size: int = 1000) -> None:
    rng = random.Random(20260817)
    written = 0
    while written < total:
        n = min(batch_size, total - written)
        upsert_drafts([
            DocumentDraft(
                source="ibay",
                source_key=f"synthetic-{written + i}",
                doc_type=rng.choice(TYPES),
                url=f"https://example.mv/{written + i}",
                title_en=phrase(rng, 8),
                summary_en=phrase(rng, 30),
                text_en=phrase(rng, 60),
                quality=rng.random(),
            )
            for i in range(n)
        ])
        written += n
        print(f"{written}/{total}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
