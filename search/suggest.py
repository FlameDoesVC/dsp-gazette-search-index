"""Autocomplete. Spec 9.

Prefix matches first (that is what a user typing expects), then trigram
neighbours (that is what rescues a typo), both ranked by corpus frequency.
"""

from __future__ import annotations

import re
from collections import Counter

from django.conf import settings
from django.db import connection, transaction

from search.models import SearchDocument, SuggestTerm

MIN_QUERY_LEN = 2
MIN_TERM_LEN = 3
MAX_TERMS = 20_000
_TOKEN = re.compile(r"[\wހ-޿]+", re.UNICODE)


def _script(term: str) -> str:
    return "thaana" if any("ހ" <= c <= "޿" for c in term) else "latin"


def rebuild_terms() -> int:
    """Rebuild the whole table from current titles.

    Streams with .iterator() -- this reads every row in the corpus and must not
    materialize it (spec 12.4).
    """
    freq: Counter[str] = Counter()
    types: dict[str, Counter[str]] = {}

    qs = (SearchDocument.objects.using(settings.STREAM_DB_ALIAS)
          .filter(is_active=True)
          .only("title_en", "title_dv", "title_latin", "doc_type"))
    for doc in qs.iterator(chunk_size=500):
        seen = set()
        for title in (doc.title_en, doc.title_dv, doc.title_latin):
            for tok in _TOKEN.findall((title or "").lower()):
                if len(tok) < MIN_TERM_LEN or tok.isdigit():
                    continue
                seen.add(tok)
        for tok in seen:
            freq[tok] += 1
            types.setdefault(tok, Counter())[doc.doc_type] += 1

    rows = [
        SuggestTerm(term=t, frequency=n, script=_script(t),
                    doc_type=types[t].most_common(1)[0][0])
        for t, n in freq.most_common(MAX_TERMS)
    ]
    with transaction.atomic(using=settings.STREAM_DB_ALIAS):
        SuggestTerm.objects.using(settings.STREAM_DB_ALIAS).all().delete()
        SuggestTerm.objects.using(settings.STREAM_DB_ALIAS).bulk_create(
            rows, batch_size=1000)
    return len(rows)


_SQL = """
SELECT term, frequency, script, doc_type,
       CASE WHEN term LIKE %(prefix)s THEN 1 ELSE 0 END AS is_prefix,
       similarity(term, %(q)s) AS sim
FROM search_suggestterm
WHERE term LIKE %(prefix)s OR similarity(term, %(q)s) > %(min_sim)s
ORDER BY is_prefix DESC, sim DESC, frequency DESC, term
LIMIT %(limit)s
"""

# The pg_trgm `%` operator's default threshold (0.3) does not rescue a single
# transposition typo like 'ihpone' -> 'iphone' (similarity 0.27), which is the
# whole point of the trigram branch. An explicit cutoff makes the behavior
# deterministic instead of depending on a session threshold.
MIN_SIMILARITY = 0.2


def suggest(q: str, limit: int = 8) -> list[dict]:
    q = (q or "").strip().lower()
    if len(q) < MIN_QUERY_LEN:
        return []
    with connection.cursor() as cur:
        cur.execute(_SQL, {"q": q, "prefix": f"{q}%", "limit": limit,
                           "min_sim": MIN_SIMILARITY})
        return [
            {"term": t, "frequency": f, "script": s, "doc_type": d}
            for t, f, s, d, _p, _sim in cur.fetchall()
        ]
