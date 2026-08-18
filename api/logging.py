"""Fire-and-forget query and click logging. Spec 16.3.

Logging must never sit on the hot path: it adds no latency to a search and a
failure here must not fail the response. Task 6 replaces the stub below with
the thread-pool writer and the partitioned tables; the router already calls
the same signature.
"""

from __future__ import annotations


def log_query(request, **kwargs) -> int | None:
    """Task 4 placeholder. Task 6 persists the query and returns its id."""
    return None
