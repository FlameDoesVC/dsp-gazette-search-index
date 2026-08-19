# Identifier Linking and Spelling Correction Design

Date: 2026-08-19
Status: awaiting review
Depends on: the catalog normalization project (entity layer, provenance ladder,
`EnrichClient` stage-2 profiles), P2 (`search/lang/`), P5 (`QueryAlias`).

## 1. Goal

Two defects in what the system currently shows, sharing one extraction pass.

A gazette notice cites reference numbers that identify the thing it is about --
a project, an announcement, a bid committee meeting. Those numbers appear in
other notices too, and today nothing connects them. A reader who wants the
amendment, the results sheet and the original award has no way from one to the
others.

Separately, Thaana writes English brand and company names ambiguously, so the
translation of a name is frequently wrong in a way that is invisible without
outside knowledge: `Rosewear` for `RoseWare`, `Multigo` for `Maltego`. A wrong
name is unsearchable and quietly misinforms.

## 2. Scope

In:

- **Identifier extraction and linking, gazette only.** Every reference number a
  notice states or cites, with the kind of number it is, indexed for exact
  match, rendered as a link that searches for it.
- **Spelling correction, gazette and iBay.** Detect and fix misspelled brand and
  company names, rewrite the affected text in place, and keep a correction table
  that also fixes user queries.

Out:

- **Links on company or brand names.** Only numbers become links. Correcting the
  spelling is worth the effort; building a company registry, a promotion queue
  and per-company result pages is not.
- **A relationship table between documents.** A number links documents by being
  searchable, not by a stored edge. See section 6.
- **Identifiers in iBay.** Its listings carry no reference numbers. The spelling
  half applies to both corpora; the identifier half does not.
- **Any dependency on an external registry.** Considered and rejected: an
  authoritative business registry exists but is not a dependency a search engine
  should acquire, and it answers a different question than "how is this name
  spelled in the wild".

## 3. Measured evidence

All figures from the live corpus on 2026-08-19.

### 3.1 One document proves the whole design: iulaan 408123

Its translated body contains three identifiers, in three formats, of three
kinds:

```
Project Number:        PC-171/2026/T327
Announcement Number:   171-Y(FMB2)/IUL/2026/166
...decision was made by the Bid Committee in meeting number BC-171/2026/094...
```

Three things follow, and each one closes off an easier design:

- **A shape rule cannot classify these.** `BC-171/2026/094` is identifiable as a
  bid-committee meeting number only from the prose around it. Nothing in the
  string says so.
- **A shape rule tuned on one of them drops another.** `PC-171/2026/T327` ends
  in `T327`, not digits.
- **The same identifier appears spelled two ways inside one document.** The
  scraped `ނަންބަރު` field reads `171-Y(FBM2)/IUL/2026/166` while the body reads
  `171-Y(FMB2)/IUL/2026/166` -- `FBM2` against `FMB2`. Both forms occur in the
  **Thaana** body as well as the translation, so this is the gazette's own
  inconsistency, not a translation artifact. Exact string matching would split
  one thread into two.

The same document names `Rosewear Corporation Private Limited`, which is
`RoseWare Corporation Pvt. Ltd.` So one fixture exercises identifier extraction,
identifier normalization and spelling correction together.

### 3.2 Identifier formats across the corpus

The document's own number is already scraped: `ނަންބަރު` is present on 121 of
125 local iulaan, and `enrich/pipeline.py` already lifts it into `reference_no`.
Its shapes vary widely:

```
674-A/2026/46      FSM-ADV/2026/171        (IUL)142-A5/142/2026/183
(IUL)179-4/1/2026/15   (IUL)340/340/2026/43   171-Y(FBM2)/IUL/2026/166
```

Numbers cited *inside* bodies, which is where the linking value is, are
plentiful: across 125 iulaan, 131 year/sequence forms, 80 slashed codes and 28
`(IUL)` forms.

### 3.3 DuckDuckGo corrects, given the whole name

The signal is a link, not prose, which makes it parseable rather than scraped:

```
q "Rosewear Corporation Private Limited"
  "Search only for" link -> "Rosewear" Corporation Private Limited   <- a correction happened
  sibling /html/?q= href -> roseware corporation private limited     <- the correction

q "Multigo"
  sibling /html/?q= href -> maltego
```

Two calibrations matter. The bare token `Rosewear` produced **no** correction;
`Rosewear Corporation Private Limited` did -- so the probe must use the full
extracted name. And an exact-phrase search for the misspelled full name returned
**zero** results, which is a second, independent signal that a spelling is wrong.

### 3.4 What cannot be used as a guard

`search/lang/translit.py` cannot validate these corrections.
`translit_latin_variants("rosewear")` returns only the input, and
`translit_latin_to_dv_variants` finds no Thaana form shared between `rosewear`
and `roseware`. That module handles Latin-Dhivehi input, not English names
written in Thaana. Measured before relying on it.

## 4. Identifier extraction

**The model classifies; regex only proposes.** This is the reverse of the
project's usual order, and section 3.1 is why: the kind of a number is carried by
the words around it, not by its shape.

Stage-2 output gains an `identifiers` list. Each entry:

```
value        verbatim, exactly as it appears
kind         project | announcement | bid_committee | invoice | contract |
             tender | lot | other
label_raw    the words that identified it ("Project Number", "meeting number")
```

Rules the prompt states and the validator enforces:

- **`value` must appear verbatim** in the document's title, body, translated
  title, translated body, or `additional_info`. A value that does not is dropped,
  not demoted -- unlike a spec, an invented identifier is not "possibly true
  about the world", it is a broken link. This is stricter than
  `catalog/tiers.py`, deliberately.
- **`kind` outside the enum becomes `other`.** The list is closed; a new kind is
  a schema change and a prompt change, reviewed together.
- **Regex is a recall net.** A candidate pattern proposes identifier-shaped
  strings the model did not return; those are stored with `kind="other"` and
  `label_raw=""`. They are still linkable, they just carry no claim about what
  they are. The pattern never overrides a model classification.

The document's own scraped `ނަންބަރު` is inserted directly with
`kind="announcement"` and needs no model call.

## 5. Normalization: `value_key`

```
value_key = <digits in order, joined> "|" <letters, sorted>
```

`171-Y(FBM2)/IUL/2026/166` and `171-Y(FMB2)/IUL/2026/166` both key to
`171-2-2026-166|BFILMUY`. Letters are where the noise lives -- office codes,
transpositions, stray parentheses -- while digits carry the identity, and sorting
the letters absorbs a transposition without discarding the letters entirely.

Discarding letters altogether was the obvious simplification and it is wrong:
`BC-171/2026/094` and `PC-171/2026/094` would collide, and those are a bid
committee meeting and a project. Verified behaviour:

| pair | result |
|---|---|
| `171-Y(FBM2)/...166` vs `171-Y(FMB2)/...166` | match |
| `(IUL)142-A5/142/2026/183` vs `IUL)142-A5/142/2026/183` | match |
| `BC-171/2026/094` vs `PC-171/2026/094` | differ |
| `674-A/2026/46` vs `674-A/2026/44` | differ |

## 6. Data model

```python
class DocumentIdentifier(models.Model):
    """One identifier occurrence. Keyed on (source, source_key) rather than a
    document FK: SearchDocument is LIST-partitioned and these must survive a
    reindex, the same reasoning as catalog.EntityLink."""

    source = models.CharField(max_length=32)
    source_key = models.CharField(max_length=128)
    value_raw = models.CharField(max_length=128)     # for display
    value_key = models.CharField(max_length=160)     # for matching, section 5
    kind = models.CharField(max_length=24, choices=KINDS)
    label_raw = models.CharField(max_length=64, blank=True)
    is_own = models.BooleanField(default=False)      # the document's own number
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [UniqueConstraint(
            fields=["source", "source_key", "value_key", "kind"],
            name="uniq_identifier_occurrence")]
        indexes = [Index(fields=["value_key"], name="identifier_value_key"),
                   Index(fields=["source", "source_key"], name="identifier_doc")]
```

No relationship table. Two documents are related because they share a
`value_key`, which a single indexed lookup answers, and a number cited by a
document nobody thought to link still finds its siblings.

```python
class SpellingCorrection(models.Model):
    """A misspelling and its correction, with the evidence that produced it.

    This is the durable half of the spelling work: the text rewrite is
    destructive, so this table is the audit trail and the input to query-side
    correction."""

    # The full name as extracted, normalized to lowercase. Full, not tokenized:
    # DuckDuckGo only corrects when given the whole name (section 3.3). The
    # token-level QueryAlias rows are derived from this, not stored here.
    wrong = models.CharField(max_length=128, unique=True)
    right = models.CharField(max_length=128)
    skeleton = models.CharField(max_length=128, db_index=True)
    evidence = models.CharField(max_length=32)   # ddg_correction | manual
    probe_query = models.CharField(max_length=256, blank=True)
    occurrences = models.IntegerField(default=0)
    status = models.CharField(max_length=16, default="active")  # active|rejected
    created_at / updated_at
```

## 7. Search and display

**Retrieval.** A query is identifier-shaped when it contains a digit and a `/`
or `-`. For such a query, `value_key` is computed and `DocumentIdentifier` is
matched exactly; those documents are unioned into the candidate set and boosted
above lexical matches, because a reader who pastes a reference number wants that
number, not documents that happen to share its digits. A query that is not
identifier-shaped never touches this path, so ordinary search is unaffected.

**Display.** The detail response carries the document's identifiers with
`value_raw` and `kind`. The frontend renders each as a link to
`/search?q=<value_raw>`, and search resolves it through `value_key`, so clicking
the `FMB2` form finds the `FBM2` document. Rendering the raw form keeps the page
faithful to the notice while matching stays tolerant.

## 8. Spelling correction

Runs at profile time, inside the stage-2 pass, once per distinct extracted name.

1. **Collect the name.** Full form as extracted -- `Rosewear Corporation Private
   Limited`, not `Rosewear`. Section 3.3 measured that the bare token yields no
   correction.
2. **Probe.** One DuckDuckGo HTML request. A `Search only for "<original>"` link
   means a correction was offered; the corrected string is the sibling
   `/html/?q=` href.
3. **Guard.** Accept only if the correction shares the original's consonant
   skeleton, `re.sub(r"[aeiou]+", "", s.lower())`. Thaana short vowels are
   diacritics, so they are what gets dropped or misread while consonants survive
   -- which is why the guard fits the failure mode rather than merely being a
   similarity threshold.

   | original | correction | skeletons | verdict |
   |---|---|---|---|
   | rosewear | roseware | `rswr` = `rswr` | accept |
   | multigo | maltego | `mltg` = `mltg` | accept |
   | rosewear | rosewe | `rswr` vs `rsw` | reject |
   | multigo | mango | `mltg` vs `mng` | reject |

   Without it, DuckDuckGo's own top results would have "corrected" `Rosewear` to
   `ROSEWE`, a different company.
4. **Record** in `SpellingCorrection`, then **rewrite destructively**:
   `translated_title` and `translated_body` for gazette, the extracted brand and
   canonical title for iBay. No shadow copy.

   Destructive is safe **because the rewritten fields are derived**.
   `Iulaan.title` and `Iulaan.body` hold the Thaana source and are untouched, so
   a bad correction is undone by re-translating. The same reasoning does not
   extend to source fields and must not be applied to them.
5. **Feed the query side, per token.** `search/lang/expand.py` matches aliases
   with `term__in=tokens`, so an alias keyed on a full name would never fire --
   `QueryAlias` is a token vocabulary, not a phrase table. So the correction is
   aligned token by token and a row is written only for the positions that
   actually differ:

   ```
   rosewear corporation private limited
   roseware corporation private limited
   ^^^^^^^^ differs           -> QueryAlias(term="rosewear", expands_to=["roseware"])
            everything else identical, no rows
   ```

   Each token pair must independently pass the skeleton guard, so an alignment
   that happens to pair unrelated words emits nothing. If the two sides have
   different token counts -- a correction that rewords rather than respells --
   alignment is abandoned and only the name-level `SpellingCorrection` is kept,
   because a wrong alias silently corrupts every future query containing that
   word. `build_query_plan` then applies it with no query-path change.

### 8.1 Cost and failure

The probe is not billed but it is rate-limited by politeness, not by contract, so:

- **Content-addressed cache**, keyed on the probe query, mirroring
  `search/extract/cache.py`. A name seen twice is probed once, and a re-run of
  the profile pass costs nothing.
- **A delay between live probes** and a per-pass ceiling.
- **Failure is silence.** A probe that errors, times out or is throttled yields
  no correction and never fails the profile. Enrichment does not block on this
  (spec 5.2), and an uncorrected name is the status quo, not a regression.
- **`SpellingCorrection` is checked before probing**, so the table warms up and
  the network path quiets down over time.

## 9. Error handling and idempotency

- An identifier that fails verbatim grounding is dropped and counted, not stored.
- Re-running extraction is idempotent: the unique constraint is the occurrence,
  so a repeat pass updates nothing.
- A `SpellingCorrection` marked `rejected` is never applied again, and the
  rewrite pass skips names already corrected.
- Deleting a `SpellingCorrection` does not restore the text. Re-translation
  does. This is stated in the model docstring, because it is the one place the
  destructive choice can surprise someone.

## 10. Testing

The canonical fixture is iulaan **408123**, because one real document covers the
whole design: three identifier kinds, a shape a naive pattern would miss
(`T327`), the `FBM2`/`FMB2` within-document variant, and a company name DDG
corrects.

- Extraction: all three identifiers found with correct kinds; `BC-171/2026/094`
  classified from prose, not shape.
- Grounding: an identifier the model invents is dropped.
- Normalization: the four pairs in section 5, both directions.
- Retrieval: searching either spelling of the announcement number returns the
  document; an identifier-shaped query outranks lexical matches; a normal query
  does not touch the identifier path.
- Correction: `Rosewear Corporation Private Limited -> RoseWare` accepted;
  `Rosewear -> ROSEWE` rejected by the skeleton guard; a probe failure leaves the
  document unchanged and the profile successful.
- Query side: searching `rosewear` finds documents now saying `RoseWare`, via a
  token-level alias; and a correction whose two sides have different token counts
  writes no alias at all.

Measurements to record: identifiers per document by kind, the share of cited
numbers that resolve to another document in the corpus (the linking payoff),
corrections proposed against corrections accepted by the guard, and probe cache
hit rate.

## 11. Relationship to other work

The catalog project supplies the stage-2 pass this extends, so identifiers and
names cost no extra model call -- the reason for specifying them together rather
than as two projects.

`QueryAlias` is P5's, and this is the first thing that writes to it
automatically; it was designed to be grown from the zero-result query list, and a
misspelling table is the same idea with a different source.

P10 task 3's gazetteer reads `Category` labels and is unaffected.

## 12. Open seams

- **`kind` is a closed enum seeded from one document.** Expect additions once the
  full gazette corpus is ingested; the review queue for that is the count of
  `kind="other"` rows carrying a non-empty `label_raw`.
- **The identifier path assumes gazette shapes.** If iBay ever carries reference
  numbers, `is_own` and the scraped-field shortcut need revisiting.
- **DuckDuckGo is a single point of failure** for corrections. The table is the
  durable artifact, so losing the probe degrades to "no new corrections" rather
  than losing existing ones. A second evidence source is a later addition, not a
  redesign.
