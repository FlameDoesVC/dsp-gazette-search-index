# Search Engine P2 Dhivehi Pipeline - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make search work in Dhivehi — Thaana script, Latin-script phonetic Dhivehi, and Thaana keyboard-layout input — with ranking that knows which language matched.

**Architecture:** A `search/lang/` package of pure functions handles script detection, normalization, keyboard decoding and phonetic transliteration. The indexer uses them to populate `vector_dv` (dual-weighted: fili-preserved at A, consonant skeleton at C) and `vector_latin`. The query layer expands one query into three term sets and blends per-language ranks into a single score.

**Tech Stack:** Python 3.12, PostgreSQL `tsvector`/`pg_trgm`, Django 6.0.5, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-search-engine-design.md` (sections 6, 7, 14)

**Plan index:** `docs/superpowers/plans/README.md` — read the cross-plan contract before starting.

## Global Constraints

- Everything in `search/lang/` is a **pure function**: no database access, no I/O, no Django imports except in the transliteration golden-file test. This is what makes the whole package table-testable.
- Fili are exactly the 11 codepoints `U+07A6`-`U+07B0`. Measured on the corpus: 49 distinct Thaana codepoints total, 38 consonants and 11 fili.
- `vector_dv` is dual-weighted — fili-preserved at weight `A`, consonant skeleton at weight `C`. Skeleton-only indexing is rejected: `ހަކަތަ` and `ހިކަތި` collide. Source: spec 6.2.
- `SEARCH_DV_INDEX_MODE` takes `dual` (default), `skeleton` or `fili`. Changing it is a settings change plus a reindex, never a migration. Source: spec 6.2.
- Keyboard-space is a **query input mode only**, never a storage format. It must never be written to `text_latin`/`vector_latin`, which hold *phonetic* Latin Dhivehi. The two encodings disagree on nearly every letter. Source: spec 6.4.
- Body text is still never stored on `SearchDocument`. Vectors are built from text passed at index time and discarded. Source: spec 12.1.
- Candidate generation stays capped at `LIMIT 500`. Source: spec 12.3.
- Ranking weights live in `settings.SEARCH_RANKING`, tunable without a migration. Source: spec 7.
- Version control is **jj**, not git. Commit with `jj commit -m "..."`.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `search/lang/__init__.py` | Re-exports the public surface |
| `search/lang/normalize.py` | NFC, HTML stripping, whitespace, digits, fili handling |
| `search/lang/keymap.py` | Thaana keyboard-layout bijection and decisive detection |
| `search/lang/translit.py` | Phonetic Thaana/Latin transliteration with variant sets |
| `search/lang/script.py` | Per-token script detection |
| `search/lang/expand.py` | `QueryPlan` construction |
| `search/lang/data/keymap.tsv` | The 49-entry keyboard table, one mapping per line |
| `search/lang/data/translit.tsv` | Phonetic mapping table |
| `search/tests/test_lang_*.py` | One test module per language module |
| `search/tests/test_eval_set.py` | Relevance regression harness |
| `search/eval/queries.yaml` | ~40 hand-written (query, expected) pairs |

**Modified:**

| Path | Change |
|---|---|
| `beynunehcheh/settings.py` | `SEARCH_RANKING`, `SEARCH_DV_INDEX_MODE` |
| `search/models.py` | Add `QueryAlias` |
| `search/adapters/gazette.py` | Populate `text_latin` and `title_latin` |
| `search/indexing.py` | Build `vector_dv` and `vector_latin` |
| `search/query.py` | Multi-vector blended ranking |

---

### Task 1: Normalization

**Files:**
- Create: `search/lang/__init__.py`, `search/lang/normalize.py`, `search/tests/test_lang_normalize.py`
- Test: `search/tests/test_lang_normalize.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize_text(s) -> str`, `strip_html(s) -> str`, `strip_fili(s) -> str`, `normalize_dv(s) -> str`, `FILI` (frozenset of 11 chars), `THAANA_RANGE`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_lang_normalize.py`:

```python
from search.lang import normalize as n


def test_fili_set_is_the_eleven_measured_codepoints():
    assert len(n.FILI) == 11
    assert "ަ" in n.FILI   # abafili
    assert "ް" in n.FILI   # sukun
    assert "ޥ" not in n.FILI   # a consonant, not a fili


def test_strip_fili_produces_the_consonant_skeleton():
    assert n.strip_fili("ހަކަތަ") == "ހކތ"
    assert n.strip_fili("ހިކަތި") == "ހކތ"


def test_strip_fili_leaves_latin_alone():
    assert n.strip_fili("iPhone 13") == "iPhone 13"


def test_strip_html_removes_markup_but_keeps_text():
    html = '<td><p dir="RTL"><strong>އަސާސީ މުސާރަ:</strong></p></td>'
    out = n.strip_html(html)
    for token in ("<td>", "dir=", "strong", "RTL"):
        assert token not in out
    assert "އަސާސީ" in out


def test_strip_html_passes_plain_text_through():
    assert n.strip_html("just text") == "just text"


def test_normalize_collapses_whitespace_and_casefolds_latin():
    assert n.normalize_text("  Hello   WORLD \n") == "hello world"


def test_normalize_maps_arabic_indic_digits_to_ascii():
    assert n.normalize_text("١٢٣") == "123"


def test_normalize_strips_zero_width_characters():
    assert n.normalize_text("a​b") == "ab"


def test_normalize_dv_keeps_fili_by_default():
    assert n.normalize_dv("ހަކަތަ") == "ހަކަތަ"


def test_normalize_is_idempotent():
    once = n.normalize_text("  Hello   WORLD ١٢٣ ")
    assert n.normalize_text(once) == once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_lang_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.lang'`.

- [ ] **Step 3: Write the module**

```bash
mkdir -p search/lang/data && touch search/lang/__init__.py
```

Create `search/lang/normalize.py`:

```python
"""Text normalization. Pure functions, no I/O. Spec 6.2."""

from __future__ import annotations

import re
import unicodedata

# Thaana block. Consonants occupy U+0780-U+07A5; fili (vowel marks and sukun)
# occupy U+07A6-U+07B0. The corpus contains exactly 49 distinct codepoints:
# 38 consonants and all 11 fili.
THAANA_RANGE = (0x0780, 0x07BF)
FILI = frozenset(chr(c) for c in range(0x07A6, 0x07B1))

_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None
)
_ARABIC_INDIC = {chr(0x0660 + i): str(i) for i in range(10)}
_EXT_ARABIC_INDIC = {chr(0x06F0 + i): str(i) for i in range(10)}
_DIGITS = str.maketrans({**_ARABIC_INDIC, **_EXT_ARABIC_INDIC})

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def is_thaana_char(ch: str) -> bool:
    return THAANA_RANGE[0] <= ord(ch) <= THAANA_RANGE[1]


def contains_thaana(s: str) -> bool:
    return any(is_thaana_char(c) for c in s or "")


def strip_html(s: str) -> str:
    """Return visible text. Gazette bodies are Word-exported HTML tables and
    indexing `td`/`valign`/`strong` would poison the vocabulary (spec 6.2)."""
    if not s or "<" not in s:
        return (s or "").strip()
    try:
        from lxml import html as lxml_html

        text = lxml_html.fromstring(s).text_content()
    except Exception:
        text = _TAG.sub(" ", s)
    return _WS.sub(" ", text).strip()


def strip_fili(s: str) -> str:
    """Remove vowel marks and sukun, leaving the consonant skeleton.

    Highest-impact recall trick for Thaana, because users type fili
    inconsistently. Indexed at weight C, never alone -- see spec 6.2.
    """
    return "".join(c for c in (s or "") if c not in FILI)


def normalize_text(s: str) -> str:
    """Script-agnostic normalization: NFC, drop zero-width, ASCII digits,
    casefold Latin, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_ZERO_WIDTH).translate(_DIGITS)
    s = s.casefold()
    return _WS.sub(" ", s).strip()


def normalize_dv(s: str, *, drop_fili: bool = False) -> str:
    """Normalize Thaana text. `drop_fili` selects the skeleton form."""
    out = normalize_text(strip_html(s))
    return strip_fili(out) if drop_fili else out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_lang_normalize.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
jj commit -m "feat(lang): thaana-aware text normalization and fili stripping"
```

---

### Task 2: Keyboard-layout decoding

**Files:**
- Create: `search/lang/keymap.py`, `search/lang/data/keymap.tsv`, `search/tests/test_lang_keymap.py`
- Test: `search/tests/test_lang_keymap.py`

**Interfaces:**
- Consumes: `search.lang.normalize`.
- Produces: `decode_keys(s) -> str | None`, `encode_keys(s) -> str`, `looks_like_keys(s) -> bool`, `KEY_TO_THAANA`, `THAANA_TO_KEY`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_lang_keymap.py`:

```python
import pytest
from search.lang import keymap as k


@pytest.mark.parametrize("keys,thaana", [
    ("migotawq", "މިގޮތައް"),
    ("liyegenq", "ލިޔެގެން"),
    ("wewqcewq", "އެއްޗެއް"),
    ("walawikumq", "އަލައިކުމް"),
    ("wawqsalAmq", "އައްސަލާމް"),
])
def test_decodes_known_pairs(keys, thaana):
    assert k.decode_keys(keys) == thaana


def test_the_mapping_is_a_bijection():
    assert len(k.KEY_TO_THAANA) == len(k.THAANA_TO_KEY)
    for key, th in k.KEY_TO_THAANA.items():
        assert k.THAANA_TO_KEY[th] == key


def test_round_trips_every_mapped_codepoint():
    for th in k.THAANA_TO_KEY:
        assert k.decode_keys(k.encode_keys(th)) == th


def test_detects_keyboard_space():
    assert k.looks_like_keys("migotawq") is True


@pytest.mark.parametrize("phrase", [
    "kuyyah", "dhinun", "firihen", "bahattaden", "vikkanee",
    "kuyyah dhinun", "firihen kudhin bahattaden",
    "Halaalukuvefa hunna", "vazeefaa ah edhey form",
])
def test_does_not_misread_phonetic_latin_dhivehi(phrase):
    """The decisive test. `text_latin` holds phonetic Latin Dhivehi like these
    real corpus titles; misreading them as keyboard space is silent corruption
    (spec 6.4). Note `kuyyah` decodes to ކުޔޔަހ under a naive check -- only the
    every-consonant-carries-a-fili rule rejects it."""
    assert k.looks_like_keys(phrase) is False


@pytest.mark.parametrize("phrase", [
    "washing", "machine", "delivery", "apartment",
    "washing machine", "iphone 13 pro", "apartment for rent",
])
def test_does_not_misread_plain_english(phrase):
    assert k.looks_like_keys(phrase) is False


def test_decode_returns_none_for_undecodable_input():
    assert k.decode_keys("iphone 13") is None


def test_decode_preserves_spaces_and_digits():
    assert k.decode_keys("migotawq 13") == "މިގޮތައް 13"


def test_every_corpus_codepoint_is_mapped():
    """Guards against an incomplete table. If this fails, the unmapped
    codepoints are printed -- fill them in from the standard layout."""
    unmapped = sorted(
        ch for ch in k.CORPUS_CODEPOINTS if ch not in k.THAANA_TO_KEY
    )
    assert not unmapped, f"unmapped: {[f'{c}=U+{ord(c):04X}' for c in unmapped]}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_lang_keymap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.lang.keymap'`.

- [ ] **Step 3: Write the data table**

Create `search/lang/data/keymap.tsv`. Format: `key<TAB>codepoint<TAB>name`. The core layout below is verified against real decoded examples; the extended Arabic-loanword consonants at the bottom are the ones most likely to need correction, and Step 1's `test_every_corpus_codepoint_is_mapped` will name any that are wrong or missing.

```
h	0780	haa
S	0781	shaviyani
n	0782	noonu
r	0783	raa
b	0784	baa
L	0785	lhaviyani
k	0786	kaafu
w	0787	alifu
v	0788	vaavu
m	0789	meemu
f	078A	faafu
d	078B	dhaalu
t	078C	thaa
l	078D	laamu
g	078E	gaafu
N	078F	gnaviyani
s	0790	seenu
D	0791	daviyani
z	0792	zaviyani
T	0793	taviyani
y	0794	yaa
p	0795	paviyani
j	0796	javiyani
c	0797	chaviyani
X	0798	ttaa
H	0799	hhaa
K	079A	khaa
J	079B	thaalu
R	079C	zaa
C	079D	sheenu
M	079E	saadhu
Y	079F	daadhu
Z	07A0	to
B	07A1	zo
G	07A2	ainu
Q	07A3	ghainu
F	07A4	qaafu
V	07A5	waavu
a	07A6	abafili
A	07A7	aabaafili
i	07A8	ibifili
I	07A9	eebeefili
u	07AA	ubufili
U	07AB	ooboofili
e	07AC	ebefili
E	07AD	eybeyfili
o	07AE	obofili
O	07AF	oaboafili
q	07B0	sukun
```

- [ ] **Step 4: Write the module**

Create `search/lang/keymap.py`:

```python
"""Thaana keyboard-layout transliteration. Spec 6.4.

This is NOT phonetic transliteration -- it is the Latin key sequence that
produces Thaana under the standard layout, a strict 1:1 bijection. `migotawq`
is `މިގޮތައް`. Many Maldivians type this way when no Thaana keyboard is
installed.

Query input only. Never store keyboard space: it collides with the phonetic
Latin Dhivehi in `text_latin`, and no language model has learned it.
"""

from __future__ import annotations

import re
from pathlib import Path

from search.lang.normalize import FILI, is_thaana_char

_DATA = Path(__file__).parent / "data" / "keymap.tsv"


def _load() -> dict[str, str]:
    table: dict[str, str] = {}
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, codepoint, _name = line.split("\t")
        table[key] = chr(int(codepoint, 16))
    return table


KEY_TO_THAANA: dict[str, str] = _load()
THAANA_TO_KEY: dict[str, str] = {v: k for k, v in KEY_TO_THAANA.items()}

# Every Thaana codepoint observed in the corpus (38 consonants + 11 fili).
CORPUS_CODEPOINTS: frozenset[str] = frozenset(
    [chr(c) for c in range(0x0780, 0x07A6)] + sorted(FILI)
)

# Characters allowed to pass through a decode untouched.
_PASSTHROUGH = re.compile(r"[\s0-9\-/.,()]")


def decode_keys(s: str) -> str | None:
    """Decode keyboard space to Thaana, or return None if `s` is not
    keyboard space. Failure is clean: either every character maps or none of
    it does."""
    if not s:
        return None
    out: list[str] = []
    mapped = 0
    for ch in s:
        if ch in KEY_TO_THAANA:
            out.append(KEY_TO_THAANA[ch])
            mapped += 1
        elif _PASSTHROUGH.match(ch):
            out.append(ch)
        else:
            return None
    if mapped == 0:
        return None
    return "".join(out)


def encode_keys(s: str) -> str:
    """Encode Thaana to keyboard space. Used to generate test fixtures and
    ASCII-safe slugs, never to build an index."""
    return "".join(THAANA_TO_KEY.get(ch, ch) for ch in s or "")


def _is_well_formed_thaana(s: str) -> bool:
    """Thaana is fully vocalized: **every consonant carries exactly one fili**,
    either a vowel mark or sukun. That orthographic rule is what makes keyboard
    detection decisive rather than statistical.

    Verified against the corpus and against adversarial input. It accepts every
    genuine keyboard-space string and rejects every English word and every
    phonetic Latin-Dhivehi word tested:

        migotawq   -> މިގޮތައް   accept    washing    -> އަސހިނގ    reject
        vazIfA     -> ވަޒީފާ     accept    machine    -> މަޗހިނެ    reject
        kuwqyaSq   -> ކުއްޔަށް   accept    kuyyah     -> ކުޔޔަހ     reject
        hakata     -> ހަކަތަ     accept    bahattaden -> ބަހަތތަދެނ reject

    A looser rule -- "a fili must follow a consonant" -- accepts all five of
    those right-hand cases and silently mis-decodes ordinary English into
    Thaana. Do not weaken this function.
    """
    i = 0
    saw_consonant = False
    while i < len(s):
        ch = s[i]
        if not is_thaana_char(ch):
            i += 1
            continue
        if ch in FILI:
            return False           # a fili with no consonant carrying it
        if i + 1 >= len(s) or s[i + 1] not in FILI:
            return False           # a bare consonant
        saw_consonant = True
        i += 2
    return saw_consonant


def looks_like_keys(s: str) -> bool:
    """Detection is decisive rather than heuristic: attempt the decode and
    check the result is well-formed Thaana. No wordlist, no threshold."""
    decoded = decode_keys(s)
    if decoded is None:
        return False
    return _is_well_formed_thaana(decoded)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_lang_keymap.py -v`
Expected: PASS, 28 tests (5 + 9 + 7 parametrized, plus 7 others).

If `test_every_corpus_codepoint_is_mapped` fails, the assertion prints the unmapped codepoints — add them to `keymap.tsv` from a Thaana layout reference and rerun. Do not guess: a wrong mapping silently mis-decodes queries.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(lang): thaana keyboard-layout decoding with decisive detection"
```

---

### Task 3: Phonetic transliteration

**Files:**
- Create: `search/lang/translit.py`, `search/lang/data/translit.tsv`, `search/tests/test_lang_translit.py`, `search/tests/test_translit_golden.py`
- Test: both test modules

**Interfaces:**
- Consumes: `search.lang.normalize`.
- Produces: `translit_dv_to_latin(s) -> str`, `translit_latin_variants(s) -> list[str]`, `translit_latin_to_dv_variants(s) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_lang_translit.py`:

```python
from search.lang import translit as t


def test_thaana_to_latin_produces_readable_output():
    assert t.translit_dv_to_latin("ކުއްޔަށް") == "kuyyah"


def test_transliteration_is_many_to_one_so_variants_are_generated():
    """ށ and ސ both reach `sh`/`s`; a single string would lose recall."""
    variants = t.translit_latin_to_dv_variants("sh")
    assert len(variants) > 1
    assert any("ށ" in v for v in variants)


def test_variant_generation_is_bounded():
    """Combinatorial explosion would make long queries unusable."""
    variants = t.translit_latin_to_dv_variants("bahattaden")
    assert 0 < len(variants) <= t.MAX_VARIANTS


def test_long_vowels_map_to_doubled_latin():
    assert "aa" in t.translit_dv_to_latin("ސާ")


def test_empty_input_is_safe():
    assert t.translit_dv_to_latin("") == ""
    assert t.translit_latin_to_dv_variants("") == []


def test_latin_input_passes_through_dv_to_latin_unchanged():
    assert t.translit_dv_to_latin("iphone") == "iphone"
```

Create `search/tests/test_translit_golden.py`:

```python
import pytest
from search.lang import translit as t
from search.lang.normalize import contains_thaana


@pytest.mark.django_db
def test_office_names_transliterate_without_crashing():
    """Golden-file source: Office rows already pair Thaana with English, so
    the corpus supplies free fixtures (spec 6.3)."""
    from gazette.models import Office

    checked = 0
    for office in Office.objects.exclude(name="").iterator(chunk_size=100):
        if not contains_thaana(office.name):
            continue
        latin = t.translit_dv_to_latin(office.name)
        assert latin
        assert not contains_thaana(latin), f"{office.name!r} -> {latin!r}"
        checked += 1
    assert checked > 0, "no Thaana office names found to check"


@pytest.mark.django_db
def test_transliteration_is_deterministic():
    from gazette.models import Office

    for office in Office.objects.exclude(name="")[:50]:
        assert t.translit_dv_to_latin(office.name) == t.translit_dv_to_latin(
            office.name
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_lang_translit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.lang.translit'`.

- [ ] **Step 3: Write the data table**

Create `search/lang/data/translit.tsv`. Format: `codepoint<TAB>primary<TAB>alternates` (alternates comma-separated, may be empty).

```
0780	h
0781	sh	s
0782	n
0783	r
0784	b
0785	lh	l
0786	k
0787
0788	v	w
0789	m
078A	f
078B	dh	d
078C	th	t
078D	l
078E	g
078F	gn	n
0790	s
0791	d	dh
0792	z
0793	t	th
0794	y
0795	p
0796	j
0797	ch	c
0798	t
0799	h
079A	kh	h
079B	z	dh
079C	z
079D	sh	s
079E	s
079F	z	d
07A0	t
07A1	z
07A2	a
07A3	gh	g
07A4	q	k
07A5	w	v
07A6	a
07A7	aa	a
07A8	i
07A9	ee	i
07AA	u
07AB	oo	u
07AC	e
07AD	ey	e
07AE	o
07AF	oa	o
07B0
```

`0787` (alifu) and `07B0` (sukun) map to nothing: alifu is a vowel carrier and sukun marks absence of a vowel. Both are structural, not sounds.

- [ ] **Step 4: Write the module**

Create `search/lang/translit.py`:

```python
"""Phonetic Thaana/Latin transliteration. Spec 6.3.

Unlike the keyboard mapping in `keymap`, this is many-to-one in both
directions, so the Latin-to-Thaana path returns a bounded *set* of candidate
spellings rather than one answer.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from pathlib import Path

from search.lang.normalize import normalize_text

_DATA = Path(__file__).parent / "data" / "translit.tsv"

MAX_VARIANTS = 24
_MAX_LATIN_TOKEN = 24   # refuse to expand absurdly long tokens


def _load() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = defaultdict(list)
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        codepoint = parts[0]
        primary = parts[1] if len(parts) > 1 else ""
        alternates = (
            [a for a in parts[2].split(",") if a] if len(parts) > 2 else []
        )
        ch = chr(int(codepoint, 16))
        forward[ch] = [primary] + alternates
        for latin in [primary] + alternates:
            if latin and ch not in reverse[latin]:
                reverse[latin].append(ch)
    return forward, dict(reverse)


DV_TO_LATIN, LATIN_TO_DV = _load()

# Longest-first so `sh` is matched before `s` when scanning Latin input.
_LATIN_KEYS = sorted(LATIN_TO_DV, key=len, reverse=True)


def translit_dv_to_latin(s: str) -> str:
    """Deterministic Thaana to Latin using the primary reading of each
    character. Non-Thaana characters pass through."""
    if not s:
        return ""
    return "".join(
        DV_TO_LATIN[ch][0] if ch in DV_TO_LATIN else ch for ch in s
    )


def _segment(token: str) -> list[list[str]] | None:
    """Greedy longest-match segmentation of a Latin token into per-segment
    Thaana candidate lists."""
    out: list[list[str]] = []
    i = 0
    while i < len(token):
        for key in _LATIN_KEYS:
            if token.startswith(key, i):
                out.append(LATIN_TO_DV[key])
                i += len(key)
                break
        else:
            return None
    return out


def translit_latin_to_dv_variants(s: str) -> list[str]:
    """Candidate Thaana spellings for a Latin token, capped at MAX_VARIANTS.

    Consonant-only output: fili are not reconstructed, because the reader
    cannot know which vowel was intended. That is fine -- the skeleton half of
    `vector_dv` (weight C) is exactly what this matches against (spec 6.2).
    """
    token = normalize_text(s)
    if not token or len(token) > _MAX_LATIN_TOKEN:
        return []
    segments = _segment(token)
    if not segments:
        return []
    total = 1
    for seg in segments:
        total *= len(seg)
        if total > MAX_VARIANTS:
            # Too ambiguous to expand; fall back to primary readings only.
            return ["".join(seg[0] for seg in segments)]
    return ["".join(combo) for combo in itertools.product(*segments)]


def translit_latin_variants(s: str) -> list[str]:
    """Latin spellings of a Thaana string, primary first."""
    if not s:
        return []
    per_char = [DV_TO_LATIN.get(ch, [ch]) for ch in s]
    total = 1
    for options in per_char:
        total *= len(options)
        if total > MAX_VARIANTS:
            return [translit_dv_to_latin(s)]
    return ["".join(combo) for combo in itertools.product(*per_char)]
```

- [ ] **Step 5: Run both test modules**

```bash
./venv/bin/pytest search/tests/test_lang_translit.py search/tests/test_translit_golden.py -v
```
Expected: PASS. The golden test needs the gazette `Office` rows loaded from P1 Task 3.

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(lang): phonetic transliteration with bounded variant sets"
```

---

### Task 4: Script detection

**Files:**
- Create: `search/lang/script.py`, `search/tests/test_lang_script.py`
- Test: `search/tests/test_lang_script.py`

**Interfaces:**
- Consumes: `normalize`, `keymap`.
- Produces: `detect_script(token) -> str` returning one of `dv-Thaa`, `dv-Keys`, `dv-Latn`, `en`; `detect_query_script(q) -> tuple[str, list[tuple[str, str]]]` returning the dominant label and per-token labels.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_lang_script.py`:

```python
from search.lang import script as s


def test_thaana_is_detected():
    assert s.detect_script("ވަޒީފާ") == "dv-Thaa"


def test_keyboard_space_is_detected_before_phonetic():
    assert s.detect_script("migotawq") == "dv-Keys"


def test_phonetic_latin_dhivehi_is_detected():
    for token in ("kuyyah", "bahattaden", "firihen", "vikkanee"):
        assert s.detect_script(token) == "dv-Latn"


def test_plain_english_is_detected():
    for token in ("washing", "apartment", "delivery"):
        assert s.detect_script(token) == "en"


def test_digits_and_model_numbers_are_english():
    assert s.detect_script("13") == "en"


def test_labels_are_per_token_not_per_query():
    """Real queries are mixed: `iPhone 13 vikkan` is half English (spec 6.1)."""
    dominant, tokens = s.detect_query_script("iPhone 13 vikkan")
    labels = dict(tokens)
    assert labels["iphone"] == "en"
    assert labels["vikkan"] == "dv-Latn"
    assert dominant in {"en", "dv-Latn"}


def test_dominant_label_of_pure_thaana_query():
    dominant, _ = s.detect_query_script("ވަޒީފާގެ ފުރުޞަތު")
    assert dominant == "dv-Thaa"


def test_empty_query_is_english():
    assert s.detect_query_script("") == ("en", [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_lang_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.lang.script'`.

- [ ] **Step 3: Write the module**

Create `search/lang/script.py`:

```python
"""Per-token script detection. Spec 6.1.

Order matters: keyboard space is checked before phonetic Latin because its
test is exact (a decode either succeeds or fails), while the phonetic test is
a heuristic. Running the heuristic first would let it claim tokens the exact
test could have resolved.
"""

from __future__ import annotations

import re

from search.lang.keymap import looks_like_keys
from search.lang.normalize import contains_thaana, normalize_text

THAANA = "dv-Thaa"
KEYS = "dv-Keys"
LATIN_DV = "dv-Latn"
ENGLISH = "en"

# Markers drawn from real corpus titles: "Halaalukuvefa hunna", "kuyyah
# dhinun", "firihen kudhin bahattaden", "iPhone 13 vikkan".
_MARKER_WORDS = frozenset("""
beynun beynunvaa vikkan vikkanee vikkaa gannan hoadhan kuyyah kuyyah's
hunna huri hifun dhinun dhookuran libey libeyne nulibey
firihen anhen kudhin bahattan bahattaden baithibbaa thibbaa
vazeefaa vazeefa masakkaiy mauloomaathu dhennevun
laari rufiyaa mihaaru miadhu adhi noon
ge ah aa akah eh ekey thakah kah
""".split())

_DIGRAPHS = ("aa", "ee", "oo", "dh", "th", "lh", "gn", "sh", "ey", "oa")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _phonetic_score(token: str) -> float:
    if token in _MARKER_WORDS:
        return 1.0
    hits = sum(token.count(d) for d in _DIGRAPHS)
    if not hits:
        return 0.0
    # Normalize by length so short tokens are not over-rewarded.
    return min(1.0, (hits * 2.0) / max(len(token), 1))


def detect_script(token: str) -> str:
    token = normalize_text(token)
    if not token:
        return ENGLISH
    if contains_thaana(token):
        return THAANA
    if token.isdigit():
        return ENGLISH
    if looks_like_keys(token):
        return KEYS
    if _phonetic_score(token) >= 0.5:
        return LATIN_DV
    return ENGLISH


def detect_query_script(q: str) -> tuple[str, list[tuple[str, str]]]:
    """Return `(dominant_label, [(token, label), ...])`."""
    tokens = _TOKEN.findall(normalize_text(q))
    if not tokens:
        return ENGLISH, []
    labelled = [(t, detect_script(t)) for t in tokens]

    counts: dict[str, int] = {}
    for _token, label in labelled:
        counts[label] = counts.get(label, 0) + 1
    # Any Thaana at all dominates: it is unambiguous evidence.
    if counts.get(THAANA):
        return THAANA, labelled
    dominant = max(counts, key=lambda k: (counts[k], k != ENGLISH))
    return dominant, labelled
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_lang_script.py -v`
Expected: PASS, 8 tests. If a marker word test fails, add the token to `_MARKER_WORDS` — that list is meant to grow from real query logs once P5 ships.

- [ ] **Step 5: Commit**

```bash
jj commit -m "feat(lang): per-token script detection across four input modes"
```

---

### Task 5: Query aliases and settings

**Files:**
- Create: `search/migrations/0004_queryalias.py`, `search/tests/test_query_alias.py`
- Modify: `search/models.py`, `search/admin.py`, `beynunehcheh/settings.py`
- Test: `search/tests/test_query_alias.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `search.models.QueryAlias(term, expands_to, is_active)`; `settings.SEARCH_RANKING` dict; `settings.SEARCH_DV_INDEX_MODE`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_query_alias.py`:

```python
import pytest
from django.conf import settings
from search.models import QueryAlias


@pytest.mark.django_db
def test_alias_expands_a_term():
    QueryAlias.objects.create(term="phone", expands_to=["mobile", "ފޯނު"])
    alias = QueryAlias.objects.get(term="phone")
    assert "ފޯނު" in alias.expands_to


@pytest.mark.django_db
def test_term_is_unique():
    from django.db import IntegrityError
    QueryAlias.objects.create(term="phone", expands_to=["mobile"])
    with pytest.raises(IntegrityError):
        QueryAlias.objects.create(term="phone", expands_to=["handset"])


def test_ranking_weights_are_configured():
    r = settings.SEARCH_RANKING
    for key in ("w_en", "w_dv", "w_latin", "w_trigram", "w_same_lang",
                "w_freshness", "w_quality", "w_phrase"):
        assert key in r


def test_freshness_half_lives_cover_every_doc_type():
    hl = settings.SEARCH_RANKING["freshness_half_life_days"]
    assert hl == {"news": 7, "job": 14, "shopping": 30, "property": 45}


def test_dv_index_mode_defaults_to_dual():
    assert settings.SEARCH_DV_INDEX_MODE == "dual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_query_alias.py -v`
Expected: FAIL — `ImportError: cannot import name 'QueryAlias'`.

- [ ] **Step 3: Add the model**

Append to `search/models.py`:

```python
class QueryAlias(models.Model):
    """Curated synonym expansion. Spec 6.5.

    Seeded by hand and grown from the zero-result query list that P5's logging
    produces -- that list is the highest-signal input for this table.
    """

    term = models.CharField(max_length=128, unique=True)
    expands_to = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=256, blank=True)

    class Meta:
        verbose_name_plural = "query aliases"
        ordering = ["term"]

    def __str__(self):
        return self.term
```

- [ ] **Step 4: Add the settings**

Append to `beynunehcheh/settings.py`:

```python
# --- search ---------------------------------------------------------------
# `dual` indexes fili-preserved lexemes at weight A and the consonant skeleton
# at weight C, so a correctly-filied query outranks a skeleton collision while
# a mis-filied one still matches. `skeleton` and `fili` exist so the strategy
# can be changed with a reindex rather than a migration. Spec 6.2.
SEARCH_DV_INDEX_MODE = os.environ.get("SEARCH_DV_INDEX_MODE", "dual")

SEARCH_RANKING = {
    "w_en": 1.0,
    "w_dv": 1.0,
    "w_latin": 0.6,
    "w_trigram": 0.4,
    "w_same_lang": 0.5,
    "w_freshness": 0.3,
    "w_quality": 0.2,
    "w_phrase": 0.5,
    "trigram_threshold": 0.25,
    "candidate_limit": 500,
    "freshness_half_life_days": {
        "news": 7,
        "job": 14,
        "shopping": 30,
        "property": 45,
    },
    "expired_penalty": 0.5,
}
```

- [ ] **Step 5: Register in the admin and migrate**

Append to `search/admin.py`:

```python
from search.models import QueryAlias


@admin.register(QueryAlias)
class QueryAliasAdmin(admin.ModelAdmin):
    list_display = ("term", "expands_to", "is_active")
    search_fields = ("term",)
    list_editable = ("is_active",)
```

```bash
./venv/bin/python manage.py makemigrations search --name queryalias
./venv/bin/python manage.py migrate search
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_query_alias.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(search): query alias table and tunable ranking weights"
```

---

### Task 6: Query expansion

**Files:**
- Create: `search/lang/expand.py`, `search/tests/test_lang_expand.py`
- Test: `search/tests/test_lang_expand.py`

**Interfaces:**
- Consumes: `script`, `keymap`, `translit`, `normalize`, `QueryAlias`.
- Produces: `QueryPlan` dataclass with `raw, lang, response_lang, terms_en, terms_dv, terms_latin, phrases`; `build_query_plan(q, *, use_aliases=True) -> QueryPlan`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_lang_expand.py`:

```python
import pytest
from search.lang.expand import build_query_plan


def test_english_query_populates_english_terms():
    plan = build_query_plan("washing machine", use_aliases=False)
    assert plan.lang == "en"
    assert "washing" in plan.terms_en
    assert plan.response_lang == "en"


def test_thaana_query_populates_dv_and_latin_terms():
    plan = build_query_plan("ކުއްޔަށް", use_aliases=False)
    assert plan.lang == "dv-Thaa"
    assert plan.terms_dv
    assert plan.terms_latin, "a Thaana query must also probe the latin vector"
    assert plan.response_lang == "dv"


def test_keyboard_query_is_decoded_into_thaana_terms():
    plan = build_query_plan("migotawq", use_aliases=False)
    assert plan.lang == "dv-Keys"
    assert any("މިގޮތ" in t for t in plan.terms_dv)
    assert plan.response_lang == "dv"


def test_phonetic_latin_query_yields_thaana_candidates():
    plan = build_query_plan("kuyyah dhinun", use_aliases=False)
    assert plan.lang == "dv-Latn"
    assert plan.terms_latin
    assert plan.terms_dv, "phonetic latin must probe the dv skeleton"


def test_mixed_query_populates_both_sides():
    plan = build_query_plan("iphone vikkan", use_aliases=False)
    assert "iphone" in plan.terms_en
    assert plan.terms_latin


def test_quoted_phrases_are_extracted_and_not_expanded():
    plan = build_query_plan('"exact phrase" other', use_aliases=False)
    assert plan.phrases == ["exact phrase"]
    assert "other" in plan.terms_en


def test_empty_query_is_empty_everywhere():
    plan = build_query_plan("", use_aliases=False)
    assert not plan.terms_en and not plan.terms_dv and not plan.terms_latin


@pytest.mark.django_db
def test_aliases_are_applied_when_enabled():
    from search.models import QueryAlias
    QueryAlias.objects.create(term="phone", expands_to=["mobile"])
    plan = build_query_plan("phone", use_aliases=True)
    assert "mobile" in plan.terms_en


@pytest.mark.django_db
def test_inactive_aliases_are_ignored():
    from search.models import QueryAlias
    QueryAlias.objects.create(
        term="phone", expands_to=["mobile"], is_active=False
    )
    plan = build_query_plan("phone", use_aliases=True)
    assert "mobile" not in plan.terms_en
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_lang_expand.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search.lang.expand'`.

- [ ] **Step 3: Write the module**

Create `search/lang/expand.py`:

```python
"""Query expansion. Spec 6.5.

Cheapest-first, short-circuiting: normalize, then the exact keyboard decode,
then phonetic transliteration, then curated aliases. The translation call is
deliberately absent here -- it belongs in P4 once the enrichment client exists,
and the three lexical paths cover most queries without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from search.lang.keymap import decode_keys
from search.lang.normalize import normalize_text, strip_fili
from search.lang.script import ENGLISH, KEYS, LATIN_DV, THAANA, detect_query_script
from search.lang.translit import (
    translit_dv_to_latin,
    translit_latin_to_dv_variants,
)

_PHRASE = re.compile(r'"([^"]+)"')
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

MAX_TERMS_PER_SIDE = 32


@dataclass(slots=True)
class QueryPlan:
    raw: str
    lang: str = ENGLISH
    response_lang: str = "en"
    terms_en: list[str] = field(default_factory=list)
    terms_dv: list[str] = field(default_factory=list)
    terms_latin: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)


def _dedupe(items: list[str], cap: int = MAX_TERMS_PER_SIDE) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
        if len(out) >= cap:
            break
    return out


def _alias_expansions(tokens: list[str]) -> list[str]:
    from search.models import QueryAlias

    rows = QueryAlias.objects.filter(term__in=tokens, is_active=True)
    out: list[str] = []
    for row in rows:
        out.extend(row.expands_to or [])
    return out


def build_query_plan(q: str, *, use_aliases: bool = True) -> QueryPlan:
    raw = q or ""
    phrases = [p.strip() for p in _PHRASE.findall(raw) if p.strip()]
    without_phrases = _PHRASE.sub(" ", raw)

    lang, labelled = detect_query_script(without_phrases)
    plan = QueryPlan(raw=raw, lang=lang, phrases=phrases)
    if not labelled:
        return plan

    plan.response_lang = "en" if lang == ENGLISH else "dv"

    en: list[str] = []
    dv: list[str] = []
    latin: list[str] = []

    for token, label in labelled:
        if label == THAANA:
            dv.append(token)
            dv.append(strip_fili(token))
            latin.append(translit_dv_to_latin(token))
        elif label == KEYS:
            decoded = decode_keys(token)
            if decoded:
                dv.append(decoded)
                dv.append(strip_fili(decoded))
                latin.append(translit_dv_to_latin(decoded))
        elif label == LATIN_DV:
            latin.append(token)
            dv.extend(translit_latin_to_dv_variants(token))
        else:
            en.append(token)
            latin.append(token)

    if use_aliases:
        all_tokens = [t for t, _ in labelled]
        for expansion in _alias_expansions(all_tokens):
            norm = normalize_text(expansion)
            if not norm:
                continue
            for sub, sub_label in detect_query_script(norm)[1]:
                if sub_label == THAANA:
                    dv.append(sub)
                    dv.append(strip_fili(sub))
                else:
                    en.append(sub)
                    latin.append(sub)

    plan.terms_en = _dedupe(en)
    plan.terms_dv = _dedupe(dv)
    plan.terms_latin = _dedupe(latin)
    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_lang_expand.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Export the public surface**

Replace `search/lang/__init__.py` with:

```python
from search.lang.expand import QueryPlan, build_query_plan  # noqa: F401
from search.lang.keymap import decode_keys, encode_keys, looks_like_keys  # noqa: F401
from search.lang.normalize import (  # noqa: F401
    FILI,
    contains_thaana,
    normalize_dv,
    normalize_text,
    strip_fili,
    strip_html,
)
from search.lang.script import detect_query_script, detect_script  # noqa: F401
from search.lang.translit import (  # noqa: F401
    translit_dv_to_latin,
    translit_latin_to_dv_variants,
    translit_latin_variants,
)
```

- [ ] **Step 6: Commit**

```bash
jj commit -m "feat(lang): query expansion across thaana, keyboard and phonetic input"
```

---

### Task 7: Index the Dhivehi vectors

**Files:**
- Modify: `search/indexing.py`, `search/adapters/gazette.py`
- Test: `search/tests/test_indexing_dv.py`

**Interfaces:**
- Consumes: `search.lang`, `DocumentDraft`.
- Produces: populated `vector_dv`, `vector_latin`, `title_latin` on every indexed row.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_indexing_dv.py`:

```python
import pytest
from django.db import connection
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search.models import SearchDocument


def _index(**kw):
    d = dict(
        source="gazette", source_key="1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
        title_dv="ވަޒީފާގެ ފުރުޞަތު",
        text_dv="ވަޒީފާގެ ފުރުޞަތު މިނިސްޓްރީ",
    )
    d.update(kw)
    upsert_drafts([DocumentDraft(**d)])
    return SearchDocument.objects.get(source_key=d["source_key"])


def _vector(doc, column):
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {column}::text FROM search_searchdocument WHERE id = %s",
            [doc.id],
        )
        return cur.fetchone()[0] or ""


@pytest.mark.django_db
def test_dv_vector_is_populated():
    doc = _index()
    assert _vector(doc, "vector_dv")


@pytest.mark.django_db
def test_dv_vector_contains_both_fili_and_skeleton_forms():
    """Dual weighting, spec 6.2: A carries fili-preserved lexemes, C the
    consonant skeleton."""
    doc = _index(title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    vec = _vector(doc, "vector_dv")
    assert "ހަކަތަ" in vec
    assert "ހކތ" in vec
    assert ":" in vec and "A" in vec


@pytest.mark.django_db
def test_latin_vector_and_title_are_populated_from_thaana():
    doc = _index()
    assert _vector(doc, "vector_latin")
    assert doc.title_latin
    assert "ވ" not in doc.title_latin


@pytest.mark.django_db
def test_no_body_text_column_appeared():
    """Spec 12.1 still holds after adding Dhivehi indexing."""
    columns = {f.name for f in SearchDocument._meta.get_fields()}
    assert "text_dv" not in columns
    assert "text_latin" not in columns


@pytest.mark.django_db
def test_english_only_document_gets_no_dv_vector():
    doc = _index(source_key="2", title_dv="", text_dv="",
                 title_en="Washing machine", text_en="washing machine")
    assert not _vector(doc, "vector_dv").strip()


@pytest.mark.django_db
def test_skeleton_mode_omits_the_fili_form(settings):
    settings.SEARCH_DV_INDEX_MODE = "skeleton"
    doc = _index(source_key="3", title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    vec = _vector(doc, "vector_dv")
    assert "ހކތ" in vec
    assert "ހަކަތަ" not in vec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_indexing_dv.py -v`
Expected: FAIL — `vector_dv` is empty because only `vector_en` is built.

- [ ] **Step 3: Replace the vector rebuild in `search/indexing.py`**

Replace `_rebuild_vectors` with the following, and add the imports `from django.conf import settings` and `from search.lang import normalize_text, strip_fili, translit_dv_to_latin` at the top:

```python
_VECTOR_SQL = """
UPDATE search_searchdocument AS d SET
    vector_en    = setweight(to_tsvector('english', v.title_en), 'A')
                || setweight(to_tsvector('english', v.text_en), 'B'),
    vector_dv    = {dv_expr},
    vector_latin = setweight(to_tsvector('simple', v.title_latin), 'A')
                || setweight(to_tsvector('simple', v.text_latin), 'B'),
    title_latin  = v.title_latin
FROM (VALUES {values}) AS v(
    source, source_key, title_en, text_en,
    title_dv, text_dv, title_dv_skel, text_dv_skel,
    title_latin, text_latin
)
WHERE d.source = v.source AND d.source_key = v.source_key
"""

# Dual weighting (spec 6.2): fili-preserved at A so an exactly-typed query
# outranks a skeleton collision, skeleton at C so a mis-typed one still
# matches. The two alternatives exist so the strategy is a settings change
# plus a reindex, never a migration.
_DV_EXPRS = {
    "dual": (
        "setweight(to_tsvector('simple', v.title_dv), 'A') "
        "|| setweight(to_tsvector('simple', v.text_dv), 'B') "
        "|| setweight(to_tsvector('simple', v.title_dv_skel), 'C') "
        "|| setweight(to_tsvector('simple', v.text_dv_skel), 'C')"
    ),
    "fili": (
        "setweight(to_tsvector('simple', v.title_dv), 'A') "
        "|| setweight(to_tsvector('simple', v.text_dv), 'B')"
    ),
    "skeleton": (
        "setweight(to_tsvector('simple', v.title_dv_skel), 'A') "
        "|| setweight(to_tsvector('simple', v.text_dv_skel), 'B')"
    ),
}


def _vector_params(draft: DocumentDraft) -> tuple:
    title_dv = normalize_text(draft.title_dv)
    text_dv = normalize_text(draft.text_dv)
    # Thaana documents get a Latin probe for free; a document that is already
    # Latin keeps whatever the adapter supplied.
    title_latin = normalize_text(
        draft.title_latin or (translit_dv_to_latin(title_dv) if title_dv else "")
    )
    text_latin = normalize_text(
        draft.text_latin or (translit_dv_to_latin(text_dv) if text_dv else "")
    )
    return (
        draft.source,
        draft.source_key,
        normalize_text(draft.title_en),
        normalize_text(draft.text_en),
        title_dv,
        text_dv,
        strip_fili(title_dv),
        strip_fili(text_dv),
        title_latin,
        text_latin,
    )


def _rebuild_vectors(drafts: list[DocumentDraft]) -> None:
    """Build every vector in one statement per batch.

    A VALUES join rather than per-row updates: the text these vectors are
    built from is never stored (spec 12.1), so it has to be supplied at index
    time, and one round trip per batch keeps that affordable.
    """
    if not drafts:
        return
    mode = getattr(settings, "SEARCH_DV_INDEX_MODE", "dual")
    dv_expr = _DV_EXPRS.get(mode, _DV_EXPRS["dual"])

    rows = [_vector_params(d) for d in drafts]
    placeholder = "(" + ", ".join(["%s"] * 10) + ")"
    values = ", ".join([placeholder] * len(rows))
    sql = _VECTOR_SQL.format(dv_expr=dv_expr, values=values)

    params: list = []
    for row in rows:
        params.extend(row)

    with connection.cursor() as cur:
        cur.execute(sql, params)
```

Add `from django.db import connection, transaction` to the imports (replacing the existing `from django.db import transaction`), and delete the now-unused `SearchVector` and `Q` imports.

- [ ] **Step 4: Pass drafts rather than rows to the rebuild**

In `upsert_drafts`, the call site currently passes `batch` (model instances). Change the function so the draft list is materialized once and used for both:

```python
def upsert_drafts(drafts: Iterable[DocumentDraft]) -> int:
    materialized = list(drafts)
    if not materialized:
        return 0

    with transaction.atomic():
        SearchDocument.objects.bulk_create(
            [_row(d) for d in materialized],
            update_conflicts=True,
            unique_fields=["source", "source_key"],
            update_fields=_UPDATE_FIELDS,
            batch_size=500,
        )
        _rebuild_vectors(materialized)
    return len(materialized)
```

`list()` here is bounded by the caller's batch size (500 by default), so the streaming discipline in spec 12.4 still holds — the constraint is never materializing a *queryset*.

- [ ] **Step 5: Populate the Latin fields in the gazette adapter**

In `search/adapters/gazette.py`, add the import `from search.lang import translit_dv_to_latin` and, in `to_document`, set:

```python
            title_latin=translit_dv_to_latin(i.title or ""),
            text_latin=translit_dv_to_latin(text_dv),
```

immediately after the `text_dv=text_dv,` line.

- [ ] **Step 6: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_indexing_dv.py search/tests/test_indexing.py -v`
Expected: PASS. The P1 indexing tests must still pass unchanged.

- [ ] **Step 7: Reindex the real corpus**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
time ./venv/bin/python manage.py reindex
docker compose exec db psql -U beynunehcheh -c "
SELECT count(*) FILTER (WHERE vector_dv IS NOT NULL AND vector_dv != '') AS with_dv,
       count(*) FILTER (WHERE vector_latin IS NOT NULL AND vector_latin != '') AS with_latin,
       count(*) AS total
FROM search_searchdocument;"
```
Expected: `with_dv` at least 306 (every gazette document), `total` 20,751.

- [ ] **Step 8: Commit**

```bash
jj commit -m "feat(search): index dhivehi vectors with dual fili weighting"
```

---

### Task 8: Multi-vector ranking

**Files:**
- Modify: `search/query.py`
- Test: `search/tests/test_query_dv.py`

**Interfaces:**
- Consumes: `QueryPlan`, `settings.SEARCH_RANKING`.
- Produces: `search.query.search(q, *, doc_type=None, limit=20, candidate_limit=None) -> list[SearchResult]` — same signature as P1, now trilingual. `SearchResult` gains `matched_lang: str`.

- [ ] **Step 1: Write the failing test**

Create `search/tests/test_query_dv.py`:

```python
import pytest
from datetime import timedelta
from django.utils import timezone
from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search import query


def _index(**kw):
    d = dict(
        source="gazette", source_key="1", doc_type="news",
        url="https://gazette.gov.mv/iulaan/1",
    )
    d.update(kw)
    upsert_drafts([DocumentDraft(**d)])


@pytest.mark.django_db
def test_thaana_query_finds_a_thaana_document():
    _index(title_dv="ވަޒީފާގެ ފުރުޞަތު", text_dv="ވަޒީފާގެ ފުރުޞަތު")
    assert len(query.search("ވަޒީފާގެ")) == 1


@pytest.mark.django_db
def test_keyboard_query_finds_the_same_thaana_document():
    _index(title_dv="މިގޮތައް", text_dv="މިގޮތައް")
    assert len(query.search("migotawq")) == 1


@pytest.mark.django_db
def test_mis_filied_query_still_matches_via_the_skeleton():
    """Recall half of the dual weighting (spec 6.2)."""
    _index(title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    assert len(query.search("ހިކަތި")) == 1


@pytest.mark.django_db
def test_correctly_filied_query_outranks_a_skeleton_collision():
    """Precision half. This is the minimal-pair regression guard from spec 14 --
    if it fails, skeleton indexing has silently taken over."""
    _index(source_key="1", title_dv="ހަކަތަ", text_dv="ހަކަތަ")
    _index(source_key="2", title_dv="ހިކަތި", text_dv="ހިކަތި")
    results = query.search("ހަކަތަ")
    assert [r.source_key for r in results][:1] == ["1"]
    assert len(results) == 2, "the collision should still be found, just lower"


@pytest.mark.django_db
def test_phonetic_latin_query_finds_a_thaana_document():
    _index(title_dv="ކުއްޔަށް", text_dv="ކުއްޔަށް ދިނުން")
    assert len(query.search("kuyyah")) >= 1


@pytest.mark.django_db
def test_english_search_still_works():
    _index(title_en="Washing machine", text_en="washing machine for sale")
    assert len(query.search("washing")) == 1


@pytest.mark.django_db
def test_same_language_match_outranks_a_cross_language_one():
    _index(source_key="1", title_dv="ފޯނު", text_dv="ފޯނު")
    _index(source_key="2", title_latin="fonu", text_latin="fonu")
    results = query.search("ފޯނު")
    assert results[0].source_key == "1"


@pytest.mark.django_db
def test_fresher_documents_rank_higher_all_else_equal():
    now = timezone.now()
    _index(source_key="1", title_en="Ferry notice", text_en="ferry notice",
           published_at=now - timedelta(days=200))
    _index(source_key="2", title_en="Ferry notice", text_en="ferry notice",
           published_at=now)
    assert [r.source_key for r in query.search("ferry")] == ["2", "1"]


@pytest.mark.django_db
def test_response_language_follows_the_query():
    _index(title_dv="ވަޒީފާ", text_dv="ވަޒީފާ")
    assert query.plan_for("ވަޒީފާ").response_lang == "dv"
    assert query.plan_for("washing").response_lang == "en"


@pytest.mark.django_db
def test_result_reports_which_language_matched():
    _index(title_dv="ވަޒީފާ", text_dv="ވަޒީފާ")
    assert query.search("ވަޒީފާ")[0].matched_lang == "dv"


@pytest.mark.django_db
def test_empty_query_returns_nothing():
    _index(title_en="anything", text_en="anything")
    assert query.search("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_query_dv.py -v`
Expected: FAIL — Thaana queries return nothing, and `plan_for` does not exist.

- [ ] **Step 3: Rewrite `search/query.py`**

```python
"""Trilingual retrieval and blended ranking. Spec 7.

One SQL statement produces the candidate set, capped at 500 rows so ranking
cost is independent of corpus size (spec 12.3). Snippets come from the stored
summaries, never `ts_headline` -- no body text is read at query time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import connection

from search.lang import QueryPlan, build_query_plan


@dataclass(slots=True)
class SearchResult:
    id: int
    source: str
    source_key: str
    doc_type: str
    url: str
    title: str
    summary: str
    card: dict[str, Any]
    score: float
    matched_lang: str


def plan_for(q: str) -> QueryPlan:
    return build_query_plan(q)


def _tsquery(terms: list[str]) -> str:
    """OR the terms. Every term is already normalized, so quoting them keeps
    punctuation from being read as tsquery syntax."""
    return " | ".join(f"'{t}'" for t in terms if t)


_SQL = """
WITH q AS (
    SELECT
        CASE WHEN %(has_en)s    THEN to_tsquery('english', %(q_en)s)    END AS q_en,
        CASE WHEN %(has_dv)s    THEN to_tsquery('simple',  %(q_dv)s)    END AS q_dv,
        CASE WHEN %(has_latin)s THEN to_tsquery('simple',  %(q_latin)s) END AS q_latin
),
candidates AS (
    SELECT d.*,
           COALESCE(ts_rank_cd(d.vector_en,    q.q_en),    0) AS r_en,
           COALESCE(ts_rank_cd(d.vector_dv,    q.q_dv),    0) AS r_dv,
           COALESCE(ts_rank_cd(d.vector_latin, q.q_latin), 0) AS r_latin,
           GREATEST(
               similarity(d.title_en,    %(raw)s),
               similarity(d.title_dv,    %(raw)s),
               similarity(d.title_latin, %(raw)s)
           ) AS trg
    FROM search_searchdocument d, q
    WHERE d.is_active
      AND (
            (q.q_en    IS NOT NULL AND d.vector_en    @@ q.q_en)
         OR (q.q_dv    IS NOT NULL AND d.vector_dv    @@ q.q_dv)
         OR (q.q_latin IS NOT NULL AND d.vector_latin @@ q.q_latin)
      )
      AND (%(doc_type)s IS NULL OR d.doc_type = %(doc_type)s)
    LIMIT %(candidate_limit)s
)
SELECT id, source, source_key, doc_type, url,
       title_en, title_dv, summary_en, summary_dv, card,
       r_en, r_dv, r_latin, trg,
       (
           %(w_en)s    * r_en
         + %(w_dv)s    * r_dv
         + %(w_latin)s * r_latin
         + %(w_trigram)s * trg
         + %(w_same_lang)s * CASE
               WHEN %(response_lang)s = 'dv' AND r_dv > 0 THEN 1
               WHEN %(response_lang)s = 'en' AND r_en > 0 THEN 1
               ELSE 0 END
         + %(w_freshness)s * CASE
               WHEN published_at IS NULL THEN 0
               ELSE exp(
                   -ln(2) *
                   EXTRACT(EPOCH FROM (now() - published_at)) / 86400.0 /
                   CASE doc_type
                       WHEN 'news'     THEN %(hl_news)s
                       WHEN 'job'      THEN %(hl_job)s
                       WHEN 'property' THEN %(hl_property)s
                       ELSE %(hl_shopping)s
                   END
               ) END
         + %(w_quality)s * quality
         - CASE WHEN expires_at IS NOT NULL AND expires_at < now()
                THEN %(expired_penalty)s ELSE 0 END
       ) AS score
FROM candidates
ORDER BY score DESC, id DESC
LIMIT %(limit)s
"""


def search(
    q: str,
    *,
    doc_type: str | None = None,
    limit: int = 20,
    candidate_limit: int | None = None,
) -> list[SearchResult]:
    plan = build_query_plan(q)
    if not (plan.terms_en or plan.terms_dv or plan.terms_latin):
        return []

    r = settings.SEARCH_RANKING
    hl = r["freshness_half_life_days"]
    params = {
        "raw": plan.raw,
        "has_en": bool(plan.terms_en),
        "has_dv": bool(plan.terms_dv),
        "has_latin": bool(plan.terms_latin),
        "q_en": _tsquery(plan.terms_en) or "x",
        "q_dv": _tsquery(plan.terms_dv) or "x",
        "q_latin": _tsquery(plan.terms_latin) or "x",
        "doc_type": doc_type,
        "response_lang": plan.response_lang,
        "candidate_limit": candidate_limit or r["candidate_limit"],
        "limit": limit,
        "w_en": r["w_en"], "w_dv": r["w_dv"], "w_latin": r["w_latin"],
        "w_trigram": r["w_trigram"], "w_same_lang": r["w_same_lang"],
        "w_freshness": r["w_freshness"], "w_quality": r["w_quality"],
        "expired_penalty": r["expired_penalty"],
        "hl_news": hl["news"], "hl_job": hl["job"],
        "hl_shopping": hl["shopping"], "hl_property": hl["property"],
    }

    with connection.cursor() as cur:
        cur.execute(_SQL, params)
        rows = cur.fetchall()

    prefer_dv = plan.response_lang == "dv"
    results: list[SearchResult] = []
    for (
        doc_id, source, source_key, dtype, url,
        title_en, title_dv, summary_en, summary_dv, card,
        r_en, r_dv, r_latin, _trg, score,
    ) in rows:
        if r_dv and r_dv >= max(r_en, r_latin):
            matched = "dv"
        elif r_latin and r_latin >= r_en:
            matched = "latin"
        else:
            matched = "en"
        results.append(
            SearchResult(
                id=doc_id,
                source=source,
                source_key=source_key,
                doc_type=dtype,
                url=url,
                title=(title_dv or title_en) if prefer_dv else (title_en or title_dv),
                summary=(summary_dv or summary_en) if prefer_dv
                        else (summary_en or summary_dv),
                card=card or {},
                score=float(score),
                matched_lang=matched,
            )
        )
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest search/tests/test_query_dv.py search/tests/test_query.py -v`
Expected: PASS. The P1 English tests still pass; `test_result_carries_the_card_payload` and the rest are unchanged.

- [ ] **Step 5: Confirm the planner still uses the GIN indexes**

```bash
docker compose exec db psql -U beynunehcheh -c "
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM search_searchdocument
WHERE is_active AND (vector_dv @@ to_tsquery('simple','ވަޒީފާ')
                  OR vector_en @@ to_tsquery('english','ministry'))
LIMIT 500;"
```
Expected: `Bitmap Index Scan` on `sd_vec_dv_gin` and/or `sd_vec_en_gin`. A sequential scan means the partial-index predicate is not matching — fix before proceeding.

- [ ] **Step 6: Search the real corpus in three input modes**

```bash
export DATABASE_URL=postgres://beynunehcheh:beynunehcheh@localhost:5432/beynunehcheh
./venv/bin/python manage.py shell -c "
from search import query
for q in ['ވަޒީފާ', 'vazeefaa', 'washing machine', 'iphone']:
    rs = query.search(q, limit=3)
    print(f'--- {q!r} ({query.plan_for(q).lang}) -> {len(rs)}')
    for r in rs:
        print('   ', round(r.score,3), r.matched_lang, r.title[:55])
"
```
Expected: the Thaana and phonetic queries both return gazette job notices; the English ones return iBay listings.

- [ ] **Step 7: Commit**

```bash
jj commit -m "feat(search): trilingual blended ranking with freshness decay"
```

---

### Task 9: The relevance evaluation set

**Files:**
- Create: `search/eval/__init__.py`, `search/eval/queries.yaml`, `search/tests/test_eval_set.py`
- Test: `search/tests/test_eval_set.py`

**Interfaces:**
- Consumes: `search.query.search`.
- Produces: a recall@5 regression gate. Spec 14 makes this the only defence against ranking changes that feel better and measure worse.

- [ ] **Step 1: Write the harness test**

Create `search/eval/__init__.py` (empty) and `search/tests/test_eval_set.py`:

```python
"""Relevance regression. Spec 14.

The fixtures are synthetic but the *queries* are real input shapes: Thaana,
keyboard space, phonetic Latin, English, and mixed. A weight change that
lowers recall@5 here is rejected regardless of how it looks by eye.
"""

import pytest
import yaml
from pathlib import Path

from search.adapters.base import DocumentDraft
from search.indexing import upsert_drafts
from search import query

FIXTURE = Path(__file__).parent.parent / "eval" / "queries.yaml"
MIN_RECALL_AT_5 = 0.80


@pytest.fixture
def corpus(db):
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    upsert_drafts([DocumentDraft(**doc) for doc in data["documents"]])
    return data


def _recall_at_5(cases) -> tuple[float, list[str]]:
    hits, misses = 0, []
    for case in cases:
        found = [r.source_key for r in query.search(case["q"], limit=5)]
        if case["expect"] in found:
            hits += 1
        else:
            misses.append(f"{case['q']!r} -> {found} (wanted {case['expect']})")
    return hits / len(cases), misses


@pytest.mark.django_db
def test_recall_at_5_meets_the_bar(corpus):
    recall, misses = _recall_at_5(corpus["cases"])
    assert recall >= MIN_RECALL_AT_5, (
        f"recall@5={recall:.2f} below {MIN_RECALL_AT_5}\n" + "\n".join(misses)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["dv-Thaa", "dv-Keys", "dv-Latn", "en"])
def test_every_input_mode_is_represented_and_works(corpus, lang):
    cases = [c for c in corpus["cases"] if c["lang"] == lang]
    assert cases, f"no evaluation cases for {lang}"
    recall, misses = _recall_at_5(cases)
    assert recall >= MIN_RECALL_AT_5, f"{lang}: {recall:.2f}\n" + "\n".join(misses)


@pytest.mark.django_db
def test_minimal_pairs_rank_correctly(corpus):
    """The fili precision guard, promoted into the eval set so a weight change
    cannot quietly regress it."""
    for pair in corpus["minimal_pairs"]:
        top = query.search(pair["q"], limit=5)
        assert top, f"no results for {pair['q']!r}"
        assert top[0].source_key == pair["expect_first"], (
            f"{pair['q']!r} ranked {top[0].source_key} first, "
            f"wanted {pair['expect_first']}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest search/tests/test_eval_set.py -v`
Expected: FAIL — `FileNotFoundError` for `queries.yaml`.

- [ ] **Step 3: Install pyyaml if absent**

```bash
./venv/bin/pip show pyyaml >/dev/null 2>&1 || ./venv/bin/pip install pyyaml
grep -qi '^PyYAML' requirements.txt || echo "PyYAML==6.0.3" >> requirements.txt
```

- [ ] **Step 4: Write the evaluation fixture**

Create `search/eval/queries.yaml`:

```yaml
# Relevance evaluation set. Spec 14.
# Every `expect` is a source_key in `documents` below.
documents:
  - {source: gazette, source_key: g-job-1, doc_type: job,
     url: "https://gazette.gov.mv/iulaan/1",
     title_dv: "ވަޒީފާގެ ފުރުޞަތު", title_en: "Job Opportunity",
     text_dv: "ވަޒީފާގެ ފުރުޞަތު މިނިސްޓްރީ އޮފް އެޑިއުކޭޝަން",
     text_en: "job opportunity ministry of education"}
  - {source: gazette, source_key: g-rent-1, doc_type: property,
     url: "https://gazette.gov.mv/iulaan/2",
     title_dv: "ކުއްޔަށް ދިނުން", title_en: "For Rent",
     text_dv: "ކުއްޔަށް ދިނުން ގެ ހުޅުމާލެ",
     text_en: "for rent house hulhumale"}
  - {source: gazette, source_key: g-news-1, doc_type: news,
     url: "https://gazette.gov.mv/iulaan/3",
     title_dv: "ޢާންމު މަޢުލޫމާތު", title_en: "Public Information",
     text_dv: "ޢާންމު މަޢުލޫމާތު ފެން ކެނޑުން",
     text_en: "public information water supply interruption"}
  - {source: gazette, source_key: g-energy-1, doc_type: news,
     url: "https://gazette.gov.mv/iulaan/4",
     title_dv: "ހަކަތަ", title_en: "Energy",
     text_dv: "ހަކަތަ ސަރުކާރު", text_en: "energy government"}
  - {source: gazette, source_key: g-collide-1, doc_type: news,
     url: "https://gazette.gov.mv/iulaan/5",
     title_dv: "ހިކަތި", text_dv: "ހިކަތި"}
  - {source: ibay, source_key: i-phone-1, doc_type: shopping,
     url: "https://ibay.com.mv/1", title_en: "Apple iPhone 13 Pro 256GB",
     text_en: "apple iphone 13 pro 256gb unlocked"}
  - {source: ibay, source_key: i-wash-1, doc_type: shopping,
     url: "https://ibay.com.mv/2", title_en: "Front load washing machine",
     text_en: "front load washing machine halaalukuvefa hunna"}
  - {source: ibay, source_key: i-bed-1, doc_type: property,
     url: "https://ibay.com.mv/3",
     title_en: "Sharing Bed Space Available",
     text_en: "sharing bed space firihen kudhin bahattaden hulhumale phase 2"}
  - {source: ibay, source_key: i-psu-1, doc_type: shopping,
     url: "https://ibay.com.mv/4",
     title_en: "KICO METAL POWER SUPPLY 24V-5A-120W",
     text_en: "kico metal power supply 24v 5a 120w"}
  - {source: ibay, source_key: i-clean-1, doc_type: job,
     url: "https://ibay.com.mv/5",
     title_en: "Cleaning work daily worker",
     text_en: "cleaning jobs household office hotels daily wage"}

cases:
  # Thaana
  - {q: "ވަޒީފާ",              expect: g-job-1,    lang: dv-Thaa}
  - {q: "ވަޒީފާގެ ފުރުޞަތު",    expect: g-job-1,    lang: dv-Thaa}
  - {q: "ކުއްޔަށް",             expect: g-rent-1,   lang: dv-Thaa}
  - {q: "ޢާންމު",              expect: g-news-1,   lang: dv-Thaa}
  - {q: "ހަކަތަ",              expect: g-energy-1, lang: dv-Thaa}
  - {q: "ފެން",                expect: g-news-1,   lang: dv-Thaa}
  # Keyboard space
  - {q: "vazIfA",              expect: g-job-1,    lang: dv-Keys}
  - {q: "kuwqyaSq",            expect: g-rent-1,   lang: dv-Keys}
  - {q: "hakata",              expect: g-energy-1, lang: dv-Keys}
  # Phonetic Latin Dhivehi
  - {q: "kuyyah dhinun",       expect: g-rent-1,   lang: dv-Latn}
  - {q: "bahattaden",          expect: i-bed-1,    lang: dv-Latn}
  - {q: "firihen kudhin",      expect: i-bed-1,    lang: dv-Latn}
  - {q: "halaalukuvefa hunna", expect: i-wash-1,   lang: dv-Latn}
  # English
  - {q: "iphone 13",           expect: i-phone-1,  lang: en}
  - {q: "washing machine",     expect: i-wash-1,   lang: en}
  - {q: "power supply",        expect: i-psu-1,    lang: en}
  - {q: "cleaning job",        expect: i-clean-1,  lang: en}
  - {q: "water supply",        expect: g-news-1,   lang: en}
  - {q: "for rent hulhumale",  expect: g-rent-1,   lang: en}
  - {q: "bed space",           expect: i-bed-1,    lang: en}

minimal_pairs:
  # Both share the skeleton ހކތ. The correctly-filied query must rank its own
  # document first, while the collision is still findable (spec 6.2).
  - {q: "ހަކަތަ", expect_first: g-energy-1}
  - {q: "ހިކަތި", expect_first: g-collide-1}
```

- [ ] **Step 5: Run the evaluation**

Run: `./venv/bin/pytest search/tests/test_eval_set.py -v`
Expected: PASS.

If recall@5 falls short, tune `settings.SEARCH_RANKING` — that is what the weights are for. Record what you changed and why in the commit message; do not lower `MIN_RECALL_AT_5` to make the test pass. If a keyboard-space case fails, verify its expected decoding with `./venv/bin/python -c "from search.lang import decode_keys; print(decode_keys('vazIfA'))"` before touching weights — a wrong fixture is more likely than wrong ranking.

- [ ] **Step 6: Run the whole suite**

Run: `./venv/bin/pytest -q`
Expected: everything passes, P1 included.

- [ ] **Step 7: Commit**

```bash
jj commit -m "test(search): relevance evaluation set with recall@5 gate"
```

---

## Out of scope for this plan

- Query-time translation via the LLM client. Spec 6.5 step 5 places it after the three lexical paths; it needs the enrichment client, so it lands in P4.
- Background translation of titles and summaries (spec 5.5) — also P4.
- Attachment text feeding the Dhivehi vectors — P3 produces that text, and reindexing picks it up with no change to this code.
- Zero-result progressive relaxation (spec 7) — P8, because it wants the query logs P5 produces to know what actually returns nothing.
- `ts_headline` snippets. Deliberately never: summaries are the snippet (spec 12.1).

## Handoff to P3

P3 adds `Attachment.text` to the gazette adapter's `text_dv`. No change to
`search/lang/` or `search/query.py` is required — a reindex is enough, which is
the rebuildability property from spec 3.1 doing its job.
