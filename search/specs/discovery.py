"""Dynamic facet discovery. Spec 8.3.

Runs over the candidate set from section 7, after retrieval and before
pagination. Six steps:

  1. aggregate DocumentSpec joined to the candidate CTE, is_facetable only
  2. discard keys present in fewer than 8 results or under 5% of them
  3. discard keys whose values are effectively constant
  4. score the survivors by coverage x distinctiveness (normalized entropy)
  5. when >=70% of candidates share one leaf category, the curated priority for
     that category overrides the scoring order
  6. emit at most 8, shaped by widget

So "power supply" surfaces voltage, amperage and wattage because DocumentSpec
holds 24V, 5A and 120W parsed out of titles, while "iphone" surfaces brand,
storage and condition. Same code path, different data.
"""

from __future__ import annotations

import math
from collections import Counter

from search.models import SpecKey

MIN_DOCUMENTS = 8
MIN_COVERAGE = 0.05
MAX_FACETS = 8
CATEGORY_DOMINANCE = 0.70
ENUM_TOP_N = 12
HISTOGRAM_BUCKETS = 10
# Below this, a key's values are effectively constant and the filter cannot
# partition anything.
MIN_ENTROPY = 0.05


def normalized_entropy(counts: list[int]) -> float:
    """Shannon entropy over the value distribution, scaled to [0, 1].

    Zero means one value dominates completely -- a dead filter. One means the
    values are evenly spread, which partitions the results best.
    """
    total = sum(counts)
    if total <= 0:
        return 0.0
    present = [c for c in counts if c > 0]
    if len(present) <= 1:
        return 0.0
    h = -sum((c / total) * math.log2(c / total) for c in present)
    return h / math.log2(len(present))


def score(coverage: float, entropy: float) -> float:
    return coverage * entropy


def dominant_category(rows: list[dict]) -> str | None:
    """The leaf category shared by at least 70% of the candidate set."""
    cats = [r.get("category") for r in rows if r.get("category")]
    if not cats:
        return None
    top, n = Counter(cats).most_common(1)[0]
    return top if n / len(rows) >= CATEGORY_DOMINANCE else None


_CANDIDATE_CATEGORY_SQL = """
SELECT COALESCE(d.attrs -> 'category_path' ->> -1, '') AS category, count(*)
FROM candidates d GROUP BY 1
"""

_TOTAL_SQL = "SELECT count(*) FROM candidates"

_KEY_STATS_SQL = """
SELECT s.key_id,
       count(DISTINCT s.document_id) AS documents,
       count(DISTINCT COALESCE(NULLIF(s.value_text, ''), s.value_num::text))
           AS distinct_values
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = ANY(%(facetable_ids)s)
GROUP BY 1
"""

_ENUM_SQL = """
SELECT s.value_text, count(DISTINCT s.document_id) AS n
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_text <> ''
GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %(top_n)s
"""

_ENUM_DISTRIBUTION_SQL = """
SELECT count(DISTINCT s.document_id) AS n
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_text <> ''
GROUP BY s.value_text
"""

_NUMERIC_SQL = """
SELECT min(s.value_num), max(s.value_num), count(DISTINCT s.document_id)
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_num IS NOT NULL
"""

_HISTOGRAM_SQL = """
SELECT width_bucket(s.value_num, %(lo)s, %(hi)s, %(buckets)s) AS b,
       count(DISTINCT s.document_id)
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND s.value_num IS NOT NULL
GROUP BY 1 ORDER BY 1
"""

_BOOL_SQL = """
SELECT count(DISTINCT s.document_id)
FROM candidates d
JOIN search_documentspec s ON s.document_id = d.id
WHERE s.key_id = %(key_id)s AND lower(s.value_text) IN ('true','yes','1')
"""


def discover_facets(cte: str, params: dict, cur, *,
                    max_facets: int = MAX_FACETS) -> list[dict]:
    """Returns dynamic facet entries in emit order, each shaped like FacetOut
    plus `dynamic: True` so P6 can tell them apart if it ever needs to."""
    facetable = list(SpecKey.objects.filter(is_facetable=True))
    if not facetable:
        return []
    by_id = {k.id: k for k in facetable}

    cur.execute(cte + _TOTAL_SQL, params)
    (total,) = cur.fetchone()
    if not total:
        return []

    cur.execute(cte + _CANDIDATE_CATEGORY_SQL, params)
    category_rows = [{"category": c, "n": n} for c, n in cur.fetchall()]
    expanded = [{"category": r["category"]} for r in category_rows
                for _ in range(r["n"])]
    dominant = dominant_category(expanded)

    # Step 1 and 2: aggregate, then apply the sparsity floors.
    cur.execute(cte + _KEY_STATS_SQL,
                {**params, "facetable_ids": list(by_id)})
    stats = []
    for key_id, documents, distinct_values in cur.fetchall():
        if documents < MIN_DOCUMENTS:
            continue
        coverage = documents / total
        if coverage < MIN_COVERAGE:
            continue
        if distinct_values <= 1:
            # Step 3, cheap form: literally one value. The entropy check below
            # catches the subtler 'one value in 98% of rows' case.
            continue
        stats.append({"key": by_id[key_id], "documents": documents,
                      "coverage": coverage, "distinct": distinct_values})

    # Steps 3 and 4: build each candidate and score it.
    scored = []
    for st in stats:
        key = st["key"]
        entry = _build(cur, cte, params, key, st, total)
        if entry is None:
            continue
        if entry["_entropy"] < MIN_ENTROPY:
            continue
        scored.append(entry)

    # Step 5: a category supermajority replaces the ordering with that
    # category's curated priority.
    if dominant:
        scoped = [e for e in scored
                  if not e["_key"].categories or dominant in e["_key"].categories]
        rest = [e for e in scored if e not in scoped]
        scoped.sort(key=lambda e: (e["_key"].priority, -e["_score"]))
        rest.sort(key=lambda e: -e["_score"])
        ordered = scoped + rest
    else:
        ordered = sorted(scored, key=lambda e: -e["_score"])

    # Step 6.
    out = []
    for entry in ordered[:max_facets]:
        entry.pop("_entropy", None)
        entry.pop("_score", None)
        entry.pop("_key", None)
        out.append(entry)
    return out


def _shell(key: SpecKey) -> dict:
    return {"key": key.key, "label": key.label_en, "label_dv": key.label_dv,
            "widget": key.widget, "unit": key.unit, "values": [],
            "min": None, "max": None, "histogram": [], "count_true": None,
            "dynamic": True}


def _build(cur, cte, params, key: SpecKey, st: dict, total: int) -> dict | None:
    entry = _shell(key)
    p = {**params, "key_id": key.id}

    if key.datatype == "numeric":
        cur.execute(cte + _NUMERIC_SQL, p)
        lo, hi, n = cur.fetchone()
        if lo is None or not n or float(lo) == float(hi):
            return None      # a single value is not a range
        entry["min"], entry["max"] = float(lo), float(hi)
        cur.execute(cte + _HISTOGRAM_SQL,
                    {**p, "lo": float(lo), "hi": float(hi),
                     "buckets": HISTOGRAM_BUCKETS})
        counts = {int(b): int(c) for b, c in cur.fetchall() if b is not None}
        counts[HISTOGRAM_BUCKETS] = (counts.get(HISTOGRAM_BUCKETS, 0)
                                     + counts.pop(HISTOGRAM_BUCKETS + 1, 0))
        width = (float(hi) - float(lo)) / HISTOGRAM_BUCKETS
        entry["histogram"] = [
            {"from": float(lo) + width * (i - 1),
             "to": float(lo) + width * i,
             "count": counts.get(i, 0)}
            for i in range(1, HISTOGRAM_BUCKETS + 1)
        ]
        entry["_entropy"] = normalized_entropy(
            [b["count"] for b in entry["histogram"]]
        )

    elif key.datatype == "bool":
        cur.execute(cte + _BOOL_SQL, p)
        (true_n,) = cur.fetchone()
        if not true_n or true_n == st["documents"]:
            return None      # all-true is as dead a filter as all-false
        entry["count_true"] = int(true_n)
        entry["_entropy"] = normalized_entropy(
            [true_n, st["documents"] - true_n]
        )

    else:
        cur.execute(cte + _ENUM_SQL, {**p, "top_n": ENUM_TOP_N})
        rows = cur.fetchall()
        if not rows:
            return None
        entry["values"] = [
            {"value": v, "label": v, "count": int(n)} for v, n in rows
        ]
        # Entropy over the FULL distribution, not the top 12: a key with one
        # dominant value and a long tail would look diverse from the top slice
        # alone and would be a bad filter anyway.
        cur.execute(cte + _ENUM_DISTRIBUTION_SQL, p)
        entry["_entropy"] = normalized_entropy([int(n) for (n,) in cur.fetchall()])

    entry["_key"] = key
    entry["_score"] = score(st["coverage"], entry["_entropy"])
    return entry
