"""The All-tab type cap. Spec 8.

`All` interleaves types with a cap of three consecutive results from one type,
so 16,000 shopping listings cannot bury 306 iulaan. Relative order within a
type is preserved -- this reorders across types only, it never re-ranks.
"""

from __future__ import annotations

from collections import defaultdict, deque


def interleave(results: list, cap: int = 3) -> list:
    if not results:
        return []

    queues: dict[str, deque] = defaultdict(deque)
    order: list[str] = []
    for r in results:
        if r.doc_type not in queues:
            order.append(r.doc_type)
        queues[r.doc_type].append(r)

    if len(order) == 1:
        return list(results)

    out: list = []
    run_type, run_len = None, 0

    while any(queues[t] for t in order):
        # Prefer the highest-scoring available head that would not break the
        # cap; fall back to any head when only the capped type remains.
        best = None
        for t in order:
            if not queues[t]:
                continue
            if t == run_type and run_len >= cap:
                continue
            head = queues[t][0]
            if best is None or head.score > queues[best][0].score:
                best = t
        if best is None:
            best = next(t for t in order if queues[t])

        pick = queues[best].popleft()
        run_len = run_len + 1 if best == run_type else 1
        run_type = best
        out.append(pick)

    return out
