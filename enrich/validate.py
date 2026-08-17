"""Layer 3: the grounding validator. Spec 5.2.

Every extracted string must be traceable to the source text: an exact
substring after normalization, or at least 0.85 token overlap. Every number
must appear as digits in the candidate set. Every date must parse and land in
a sane range. A field that fails is dropped and the reason is recorded.
Nothing is repaired by guessing.

Three rules here are not generic validation and exist for named failures:

- `negotiable` is demoted to `unlisted` unless the source contains a
  negotiability marker, because those are different claims on the card.
- `completeness` is recomputed from what actually survived, not taken from the
  model, so a dropped allowance turns a point estimate into a floor.
- A scraped field is never overwritten; a conflict keeps the scraped value and
  flags the record `needs_review`.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from pydantic import ValidationError

from enrich.preextract import Candidates
from enrich.schemas import ATTRS_FOR_TYPE

STRING_OVERLAP_FLOOR = 0.85
MIN_YEAR = 2000
MAX_YEARS_AHEAD = 10
# Below this length a substring match is meaningless -- 'GS3' is fine, but a
# two-character string matches almost anything.
MIN_GROUNDED_LEN = 3

_PUNCT = re.compile(r"[^\w\sހ-޿]", re.UNICODE)
_WS = re.compile(r"\s+")

_NEGOTIABLE_MARKERS = (
    "negotiable", "negotiation", "negotiate", "to be discussed",
    "depending on experience", "as per experience", "doe",
    "އެއްބަސްވެވޭ", "މަޝްވަރާ",
)

# Fields whose values are enums, free labels or lists of short tokens that the
# model is allowed to normalize rather than copy. Grounding a normalized
# category against the raw text would drop almost all of them.
_UNGROUNDED_STRING_FIELDS = {
    "position_type", "job_category", "listing_kind", "unit_kind",
    "furnishing", "condition", "seller_type", "delivery", "announcement_type",
    "period", "basis", "kind", "price_period", "datatype", "widget",
    "tenant_preference", "shared_facilities", "category_path", "keywords",
    "salary_state", "completeness",
}


def normalize_for_match(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def token_overlap(a: str, b: str) -> float:
    """Overlap between `a`'s and `b`'s tokens, normalized by the smaller set.

    The question grounding asks is whether the extracted value is supported by
    the source, so a value that is a subset of the source scores 1.0 -- but the
    measure stays symmetric enough to be useful on its own.
    """
    ta = set(normalize_for_match(a).split())
    tb = set(normalize_for_match(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _digit_forms(value) -> set[str]:
    """Every way `value` might be written in the source."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return set()
    out = set()
    if f.is_integer():
        i = int(f)
        out.add(str(i))
        out.add(f"{i:,}")
    else:
        out.add(str(f))
        out.add(f"{f:,}")
    return out


class _Report:
    def __init__(self):
        self.dropped: list[dict] = []
        self.needs_review = False

    def drop(self, field: str, value, reason: str):
        self.dropped.append({"field": field, "value": _jsonable(value),
                             "reason": reason})

    def as_dict(self) -> dict:
        return {"dropped": self.dropped, "needs_review": self.needs_review}


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _string_is_grounded(value: str, source_norm: str) -> bool:
    if len(value.strip()) < MIN_GROUNDED_LEN:
        return True
    v = normalize_for_match(value)
    if not v:
        return True
    if v in source_norm:
        return True
    return token_overlap(value, source_norm) >= STRING_OVERLAP_FLOOR


def _date_is_sane(value: str) -> str | None:
    """Returns a reason string when the date is bad, None when it is fine."""
    try:
        d = dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return "bad_date"
    today = dt.date.today()
    if d.year < MIN_YEAR or d.year > today.year + MAX_YEARS_AHEAD:
        return "date_out_of_range"
    return None


def _walk(node, path, *, source_norm, numeric_ok, report):
    """Recursively prune ungrounded leaves out of the raw attrs dict."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            kept = _walk(v, f"{path}.{k}" if path else k,
                         source_norm=source_norm, numeric_ok=numeric_ok,
                         report=report)
            if kept is not None:
                out[k] = kept
        return out

    if isinstance(node, list):
        out_list = []
        for i, v in enumerate(node):
            kept = _walk(v, f"{path}[{i}]", source_norm=source_norm,
                         numeric_ok=numeric_ok, report=report)
            if kept is None:
                continue
            # An object whose identifying value was dropped is not worth
            # keeping: an allowance with no amount, a contact with no number.
            if isinstance(kept, dict) and _is_empty_item(v, kept):
                report.drop(path, v, "not_grounded")
                continue
            out_list.append(kept)
        return out_list

    leaf = path.rsplit(".", 1)[-1].split("[")[0]

    if isinstance(node, bool) or node is None:
        return node

    if isinstance(node, (int, float)):
        if leaf in {"doc_type_confidence", "pension_rate", "priority"}:
            return node
        if not (_digit_forms(node) & numeric_ok):
            report.drop(path, node, "not_grounded")
            return None
        return node

    if isinstance(node, str):
        if leaf in {"deadline", "apply_before", "published"} and node:
            reason = _date_is_sane(node)
            if reason:
                report.drop(path, node, reason)
                return None
            return node
        if leaf in _UNGROUNDED_STRING_FIELDS:
            return node
        if leaf == "value":
            # contact / apply-method values: phone numbers, emails, URLs. Those
            # are all in the candidate set verbatim.
            if node and normalize_for_match(node) not in source_norm:
                report.drop(path, node, "not_grounded")
                return None
            return node
        if node and not _string_is_grounded(node, source_norm):
            report.drop(path, node, "not_grounded")
            return None
        return node

    report.drop(path, node, "unexpected_type")
    return None


def _is_empty_item(original: dict, kept: dict) -> bool:
    """True when the identifying field of a list item did not survive."""
    for identifying in ("amount", "value", "value_num", "value_text"):
        if identifying in original and identifying not in kept:
            return True
    return False


def _apply_scraped(attrs: dict, scraped: dict, report: _Report) -> dict:
    """Layer 4. Scraped fields win; a conflict flags the record."""
    for key, truth in scraped.items():
        if truth in (None, "", [], {}):
            continue
        claimed = attrs.get(key)
        if claimed in (None, "", [], {}):
            attrs[key] = truth
            continue
        if normalize_for_match(str(claimed)) != normalize_for_match(str(truth)):
            report.drop(key, claimed, "scraped_conflict")
            report.needs_review = True
        attrs[key] = truth
    return attrs


def _fix_compensation(attrs: dict, source_text: str, claimed_allowances: int):
    """The negotiable rule and the completeness recomputation."""
    comp = attrs.get("compensation")
    if not isinstance(comp, dict):
        return None

    reason = None
    if comp.get("salary_state") == "negotiable":
        low = source_text.lower()
        if not any(m in low for m in _NEGOTIABLE_MARKERS):
            comp["salary_state"] = "unlisted"
            reason = "negotiable_unsupported"

    has_basic = bool(comp.get("basic_salary"))
    kept_allowances = len(comp.get("allowances") or [])
    if not has_basic:
        comp["completeness"] = "none"
    elif claimed_allowances == 0:
        comp["completeness"] = "basic_only"
    elif kept_allowances == claimed_allowances:
        comp["completeness"] = "full"
    else:
        comp["completeness"] = "partial"
    return reason


def _drop_schema_leaves(pruned: dict, exc: ValidationError, report: _Report) -> None:
    """Pop exactly the offending leaf, not the containing subtree, so one bad
    enum in `compensation.period` costs `period`, never the whole block."""
    for err in exc.errors():
        loc = err.get("loc") or ()
        if not loc:
            continue
        target = pruned
        for part in loc[:-1]:
            if isinstance(part, int):
                if isinstance(target, list) and part < len(target):
                    target = target[part]
                else:
                    target = None
                    break
            elif isinstance(target, dict):
                target = target.get(part)
            else:
                target = None
                break
            if target is None:
                break
        if target is None:
            continue
        leaf = loc[-1]
        if isinstance(target, dict) and leaf in target:
            report.drop(f"{loc[0]}.{leaf}", target.get(leaf), "schema")
            del target[leaf]
        elif isinstance(target, list) and isinstance(leaf, int) and leaf < len(target):
            report.drop(str(loc[0]), target[leaf], "schema")
            del target[leaf]


def ground(
    raw_attrs: dict,
    *,
    doc_type: str,
    source_text: str,
    candidates: Candidates,
    scraped: dict | None = None,
):
    """Prune, then parse. Returns (validated_model, report_dict)."""
    report = _Report()
    model_cls = ATTRS_FOR_TYPE.get(doc_type, ATTRS_FOR_TYPE["news"])

    if not isinstance(raw_attrs, dict):
        report.drop("attrs", raw_attrs, "unexpected_type")
        return model_cls(), report.as_dict()

    claimed_allowances = len(
        ((raw_attrs.get("compensation") or {}).get("allowances") or [])
        if isinstance(raw_attrs.get("compensation"), dict) else []
    )

    source_norm = normalize_for_match(source_text)
    numeric_ok = candidates.all_numeric_strings()

    pruned = _walk(raw_attrs, "", source_norm=source_norm,
                   numeric_ok=numeric_ok, report=report)
    pruned = _apply_scraped(pruned, scraped or {}, report)

    reason = _fix_compensation(pruned, source_text, claimed_allowances)
    if reason:
        report.drop("compensation.salary_state", "negotiable", reason)

    try:
        model = model_cls(**pruned)
    except ValidationError as exc:
        # Drop only the offending keys and re-parse, so one bad enum does not
        # cost the whole record.
        _drop_schema_leaves(pruned, exc, report)
        try:
            model = model_cls(**pruned)
        except ValidationError:
            report.drop("attrs", None, "schema")
            model = model_cls()

    return model, report.as_dict()
