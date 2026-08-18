"""Type and category interleaving. Spec 8.

`All` interleaves doc_types with a cap of three consecutive results from one
type, so 16,000 shopping listings cannot bury 306 iulaan. The same shape caps
consecutive results sharing one leaf category on the shopping tab, so six
chargers cannot occupy the top six and `Mobile Phones` surfaces on page one
whatever the query (P9 task 4). Relative order within a group is preserved --
this reorders across groups only, it never re-ranks.
"""

from __future__ import annotations

from collections import defaultdict, deque


def interleave(results: list, cap: int = 3) -> list:
    """Interleave by doc_type -- the All-tab cap."""
    return interleave_by(results, key="doc_type", cap=cap)


def interleave_by(results: list, key: str, cap: int = 3) -> list:
    """Interleave by the named result attribute, capping consecutive runs."""
    if not results:
        return []

    queues: dict[str, deque] = defaultdict(deque)
    order: list[str] = []
    for r in results:
        group = getattr(r, key, "")
        if group not in queues:
            order.append(group)
        queues[group].append(r)

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
