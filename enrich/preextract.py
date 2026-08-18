"""Layer 0: deterministic pre-extraction. Spec 5.2.

Phone numbers, emails, URLs, money amounts, <number><unit> pairs and dates are
pulled out with regex before the model is called and passed in as a candidate
list. The model selects and labels from these candidates; it never transcribes
them.

The consequence is structural, not statistical: task 7 drops any number in the
model's output that is not in this candidate set, so a wrong phone number, an
invented voltage or a fabricated salary cannot reach a card. It also cuts
output tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from search.extract.dates import parse_dv_month

# Maldivian numbers are seven digits: mobile starts 7 or 9, landline 3 or 6.
# The +960 prefix is optional and the number is frequently embedded in a title
# with no separator, hence the explicit boundary guards rather than \b (which
# would happily match the '445' tail of a longer run of digits).
_PHONE = re.compile(r"(?<![\d])(?:\+?960[\s\-]?)?([79]\d{6}|[36]\d{6})(?![\d])")

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")

# Money is written at least four ways in this corpus and all of them appear:
#   10,750 ރުފިޔާ | -/32,632 | 7000/- | MVR 5,000 | USD 450 | $450
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MONEY_PATTERNS = [
    (re.compile(rf"(?:USD|\$)\s*({_NUM})", re.I), "USD"),
    (re.compile(rf"({_NUM})\s*(?:USD|dollars?)", re.I), "USD"),
    (re.compile(rf"(?:MVR|Rf\.?|ރ\.?|ރުފިޔާ)\s*({_NUM})", re.I), "MVR"),
    (re.compile(rf"({_NUM})\s*(?:MVR|rufiyaa|ރުފިޔާ)", re.I), "MVR"),
    (re.compile(rf"-/\s*({_NUM})"), "MVR"),
    (re.compile(rf"({_NUM})\s*/-"), "MVR"),
]
# Anything else that looks like an amount. Kept separate because it is the
# weakest signal and the model is told so. A trailing period that ends a
# sentence is allowed -- '4,400.' is an amount, '4,400.50' is a decimal.
_BARE_AMOUNT = re.compile(rf"(?<![\d,.])({_NUM})(?![,\d]|\.\d)")

# P7 moved the unit vocabulary into the SpecKey registry (spec 4.4) so adding
# a unit is an admin row. This fixed list is the fallback when the registry is
# empty, which it is in a fresh database and during P4's own tests.
_FALLBACK_UNIT_VOCAB = [
    "kWh", "mAh", "GHz", "MHz", "sqft", "inch", "kW", "GB", "TB", "MB",
    "kg", "ml", "cm", "mm", "V", "A", "W", "L", '"',
]


def unit_vocab() -> list[str]:
    """P7 moved this into the SpecKey registry so adding a unit is an admin
    row (spec 4.4). Falls back to the fixed list when the registry is empty."""
    try:
        from search.specs.extract import unit_vocabulary
        vocab = unit_vocabulary()
    except Exception:
        vocab = []
    return vocab or _FALLBACK_UNIT_VOCAB


_UNIT_CACHE: tuple[int, re.Pattern] | None = None


def _unit_pattern() -> re.Pattern:
    global _UNIT_CACHE
    vocab = unit_vocab()
    fingerprint = hash(tuple(vocab))
    if _UNIT_CACHE is None or _UNIT_CACHE[0] != fingerprint:
        alt = "|".join(re.escape(u) for u in vocab)
        _UNIT_CACHE = (fingerprint, re.compile(
            rf"(?<![A-Za-z\d])({_NUM})\s*({alt})(?![A-Za-z])"))
    return _UNIT_CACHE[1]

_ISO_DATE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
_DMY_DATE = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2})")
_EN_TEXT_DATE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})")
# day-first, which is what Maldivian sources actually write
_DV_TEXT_DATE = re.compile(r"(\d{1,2})\s+([ހ-޿]+)\s+(20\d{2})")
# year-month-day also occurs in the wild; keep both orders working
_DV_YMD_TEXT_DATE = re.compile(r"(20\d{2})\s+([ހ-޿]+)\s+(\d{1,2})")

_COUNT = re.compile(r"^\s*(\d+)\s*(?:rooms?|bedrooms?|baths?|bathrooms?)?"
                    r"\s*(and\s+more)?\s*$", re.I)
_MULTIVALUE_SPLIT = re.compile(r"\s*(?:,|/|\||\bor\b|\band\b)\s*", re.I)


@dataclass(slots=True)
class Candidates:
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    money: list[dict] = field(default_factory=list)     # {amount, currency, raw}
    units: list[dict] = field(default_factory=list)     # {value, unit, raw}
    dates: list[str] = field(default_factory=list)      # ISO
    numbers: list[float] = field(default_factory=list)  # every bare number seen

    def all_numeric_strings(self) -> set[str]:
        """Every digit run the validator will accept in model output."""
        out: set[str] = set()
        for p in self.phones:
            out.add(p)
        for m in self.money:
            out.add(_fmt(m["amount"]))
        for u in self.units:
            out.add(_fmt(u["value"]))
        for n in self.numbers:
            out.add(_fmt(n))
        return out


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_money(s: str) -> tuple[float, str] | None:
    """First money amount in `s`, with its currency.

    Currency is set from an explicit marker only. A bare number defaults to
    MVR and the caller records `currency_inferred` (spec 4.3.1) -- 1,019
    products in this corpus mention USD, so assuming is not safe.
    """
    for pattern, currency in _MONEY_PATTERNS:
        m = pattern.search(s)
        if m:
            return float(m.group(1).replace(",", "")), currency
    m = _BARE_AMOUNT.search(s)
    if m:
        return float(m.group(1).replace(",", "")), "MVR"
    return None


def parse_count(raw: str) -> tuple[int, bool] | None:
    """'3 Rooms' -> (3, False); '4 Rooms and More' -> (4, True). Spec 4.3.1."""
    if not raw:
        return None
    m = _COUNT.match(raw)
    if not m:
        return None
    return int(m.group(1)), bool(m.group(2))


def split_multivalue(raw: str) -> list[str]:
    """'Air Conditioning, Fans, Towels' -> three values. Spec 4.4."""
    if not raw:
        return []
    return [p.strip() for p in _MULTIVALUE_SPLIT.split(raw) if p.strip()]


def _extract_dates(text: str) -> list[str]:
    out: list[str] = []

    def push(y, mo, d):
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            out.append(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")

    for y, mo, d in _ISO_DATE.findall(text):
        push(y, mo, d)
    for d, mo, y in _DMY_DATE.findall(text):
        push(y, mo, d)
    for d, name, y in _EN_TEXT_DATE.findall(text):
        mo = parse_dv_month(name)
        if mo:
            push(y, mo, d)
    for d, name, y in _DV_TEXT_DATE.findall(text):
        mo = parse_dv_month(name)
        if mo:
            push(y, mo, d)
    for y, name, d in _DV_YMD_TEXT_DATE.findall(text):
        mo = parse_dv_month(name)
        if mo:
            push(y, mo, d)
    return _dedup(out)


def extract_candidates(text: str) -> Candidates:
    if not text:
        return Candidates()

    phones = _dedup(_PHONE.findall(text))
    phone_set = set(phones)

    money: list[dict] = []
    consumed: set[str] = set()
    for pattern, currency in _MONEY_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(1)
            amount = float(raw.replace(",", ""))
            money.append({"amount": amount, "currency": currency, "raw": m.group(0)})
            consumed.add(raw)

    units: list[dict] = []
    unit_spans: list[tuple[int, int]] = []
    for m in _unit_pattern().finditer(text):
        raw = m.group(1)
        # A four-digit run that reads as a year is a model year, not a unit
        # value -- 'Model year 2019 A/C unit' must not yield a 2019 A-unit.
        looks_like_year = raw.isdigit() and len(raw) == 4 and raw.startswith("20")
        if looks_like_year:
            continue
        units.append({
            "value": float(raw.replace(",", "")),
            "unit": m.group(2),
            "raw": m.group(0),
        })
        unit_spans.append(m.span())

    dates = _extract_dates(text)
    date_digits = {p for d in dates for p in d.split("-")}

    numbers: list[float] = []
    for m in _BARE_AMOUNT.finditer(text):
        raw = m.group(1)
        if raw in consumed or raw in phone_set:
            continue
        if any(s <= m.start() < e for s, e in unit_spans):
            continue
        # A four-digit run that reads as a year is not a price. Everything else
        # bare is offered as a weak money candidate.
        looks_like_year = raw.isdigit() and len(raw) == 4 and raw.startswith("20")
        if not looks_like_year and raw not in date_digits:
            money.append({"amount": float(raw.replace(",", "")),
                          "currency": "MVR", "raw": raw})

    # dedupe money on (amount, currency), first appearance wins
    seen, dedup_money = set(), []
    for m in money:
        k = (m["amount"], m["currency"])
        if k not in seen:
            seen.add(k)
            dedup_money.append(m)

    return Candidates(
        phones=phones,
        emails=_dedup(_EMAIL.findall(text)),
        urls=_dedup(_URL.findall(text)),
        money=dedup_money,
        units=units,
        dates=dates,
        numbers=_dedup(numbers),
    )


def candidates_block(c: Candidates) -> str:
    """The block appended to the user prompt. Sorted, so identical input
    produces an identical prompt and the provider cache keeps hitting."""
    return json.dumps(
        {
            "phones": c.phones,
            "emails": c.emails,
            "urls": c.urls,
            "money": [{"amount": m["amount"], "currency": m["currency"]}
                      for m in c.money],
            "units": [{"value": u["value"], "unit": u["unit"]} for u in c.units],
            "dates": c.dates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
