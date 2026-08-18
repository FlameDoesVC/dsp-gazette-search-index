"""Filter parsing and SQL generation. Spec 9.

Wire format is `key:value` for enums and `key:min..max` for ranges, repeated
for multi-select. The frontend does not need to know which facets exist for
which query, which is the whole point of the facet registry.

Security: every key is looked up in the registry and every value is bound as a
parameter. No user string is ever concatenated into SQL. A key that is not in
the registry is a 400, not a query fragment -- the registry is a whitelist and
that is its second job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from search.facets import FacetDef, facet_def

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


class FilterError(ValueError):
    """A filter the API must reject with 400."""


@dataclass
class Filter:
    key: str
    op: str                       # eq | range | bool
    values: list = field(default_factory=list)
    lo: float | None = None
    hi: float | None = None
    definition: FacetDef | None = None

    def __eq__(self, other):      # definition is incidental to identity
        return (self.key, self.op, self.values, self.lo, self.hi) == (
            other.key, other.op, other.values, other.lo, other.hi)


def parse_filters(raw: list[str] | None, doc_type: str | None) -> list[Filter]:
    if not raw:
        return []

    by_key: dict[str, Filter] = {}
    for item in raw:
        if ":" not in item:
            raise FilterError(f"malformed filter {item!r}: expected key:value")
        key, _, value = item.partition(":")
        key = key.strip()

        if not _IDENT.match(key):
            raise FilterError(f"unknown filter {key!r}")
        d = facet_def(doc_type, key)
        if d is None:
            raise FilterError(f"unknown filter {key!r} for type {doc_type!r}")

        if d.widget == "range":
            lo, hi = _parse_range(value, key)
            by_key[key] = Filter(key=key, op="range", lo=lo, hi=hi, definition=d)
        elif d.widget == "toggle":
            v = value.strip().lower()
            if v in _TRUE:
                b = True
            elif v in _FALSE:
                b = False
            else:
                raise FilterError(f"filter {key!r} expects a boolean, got {value!r}")
            by_key[key] = Filter(key=key, op="bool", values=[b], definition=d)
        else:
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = Filter(key=key, op="eq", values=[value],
                                     definition=d)
            else:
                existing.values.append(value)

    return list(by_key.values())


def _parse_range(value: str, key: str) -> tuple[float | None, float | None]:
    if ".." not in value:
        raise FilterError(f"filter {key!r} expects min..max, got {value!r}")
    lo_s, _, hi_s = value.partition("..")

    def num(s):
        s = s.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            raise FilterError(f"filter {key!r} expects numbers, got {s!r}") from None

    lo, hi = num(lo_s), num(hi_s)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _expr(d: FacetDef) -> str:
    """The SQL expression that yields this facet's value for one row.

    The path is registry-controlled, never user input, so building the JSONB
    accessor by string is safe here and only here.
    """
    if d.storage == "column":
        return f"d.{d.path}"
    parts = d.path.split(".")
    if len(parts) == 1:
        return f"d.attrs ->> '{parts[0]}'"
    inner = " -> ".join(f"'{p}'" for p in parts[:-1])
    return f"d.attrs -> {inner} ->> '{parts[-1]}'"


def _array_expr(d: FacetDef) -> str:
    parts = d.path.split(".")
    if len(parts) == 1:
        return f"d.attrs -> '{parts[0]}'"
    return "d.attrs -> " + " -> ".join(f"'{p}'" for p in parts)


def filter_sql(filters: list[Filter]) -> tuple[str, dict]:
    """Returns an AND-joined clause fragment and its bound parameters."""
    if not filters:
        return "", {}

    clauses: list[str] = []
    params: dict = {}

    for i, f in enumerate(filters):
        d = f.definition or facet_def(None, f.key)
        p = f"flt{i}"

        if d.key == "deadline":
            # Values are open | closing_soon | closed, derived at query time.
            # Never stored: a gazette row is written once and would otherwise
            # advertise a closed vacancy as open forever (spec 8).
            wanted = set(f.values)
            parts = []
            if "open" in wanted:
                parts.append("(d.expires_at IS NULL OR d.expires_at >= now())")
            if "closing_soon" in wanted:
                parts.append(
                    "(d.expires_at >= now() AND "
                    "d.expires_at < now() + interval '7 days')"
                )
            if "closed" in wanted:
                parts.append("(d.expires_at IS NOT NULL AND d.expires_at < now())")
            clauses.append("(" + " OR ".join(parts) + ")" if parts else "TRUE")

        elif d.storage == "spec":
            # A promoted SpecKey lives in the DocumentSpec side table. The
            # key is registry-controlled and every value is bound, so this is
            # still a whitelisted filter.
            sub = ("EXISTS (SELECT 1 FROM search_documentspec sp "
                   "JOIN search_speckey sk ON sk.id = sp.key_id "
                   "WHERE sp.document_id = d.id AND sk.key = %({p}_key)s AND {cond})")
            params[f"{p}_key"] = d.key
            if f.op == "range":
                conds = []
                if f.lo is not None:
                    conds.append(f"sp.value_num >= %({p}_lo)s")
                    params[f"{p}_lo"] = f.lo
                if f.hi is not None:
                    conds.append(f"sp.value_num <= %({p}_hi)s")
                    params[f"{p}_hi"] = f.hi
                cond = " AND ".join(conds) or "TRUE"
            elif f.op == "bool":
                cond = ("lower(sp.value_text) IN ('true','yes','1')"
                        if f.values[0] else
                        "lower(sp.value_text) NOT IN ('true','yes','1')")
            else:
                cond = f"sp.value_text = ANY(%({p})s)"
                params[p] = list(f.values)
            clauses.append(sub.format(p=p, cond=cond))

        elif f.op == "range":
            expr = _expr(d)
            cast = expr if d.storage == "column" else f"({expr})::numeric"
            if f.lo is not None:
                clauses.append(f"{cast} >= %({p}_lo)s")
                params[f"{p}_lo"] = f.lo
            if f.hi is not None:
                clauses.append(f"{cast} <= %({p}_hi)s")
                params[f"{p}_hi"] = f.hi

        elif f.op == "bool":
            if d.key == "has_images":
                clauses.append(
                    "jsonb_array_length(d.thumbnails) > 0"
                    if f.values[0] else "jsonb_array_length(d.thumbnails) = 0"
                )
            elif d.key == "has_attachments":
                clauses.append(
                    f"jsonb_array_length(COALESCE({_array_expr(d)}, '[]'::jsonb)) > 0"
                    if f.values[0] else
                    f"jsonb_array_length(COALESCE({_array_expr(d)}, '[]'::jsonb)) = 0"
                )
            else:
                clauses.append(f"{_expr(d)} = %({p})s")
                params[p] = "true" if f.values[0] else "false"

        elif d.storage == "attrs_array":
            clauses.append(
                f"EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                f"COALESCE({_array_expr(d)}, '[]'::jsonb)) v "
                f"WHERE v = ANY(%({p})s))"
            )
            params[p] = list(f.values)

        else:
            clauses.append(f"{_expr(d)} = ANY(%({p})s)")
            params[p] = list(f.values)

    return " AND " + " AND ".join(f"({c})" for c in clauses), params
