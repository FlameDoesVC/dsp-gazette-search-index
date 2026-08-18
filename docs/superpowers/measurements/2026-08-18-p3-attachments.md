# P3 Attachment extraction measurements — 2026-08-18

## Scanned fraction (spec 5.6.2)

Measured by `extract_attachments --no-transcribe` over the whole synced corpus
(336 attachment references across 306 iulaan):

| status | method | count | avg pages |
|---|---|---|---|
| ok | pdftotext | 132 | 4.9 |
| ocr_failed | none | 89 | 2.7 |
| pending (application_form, excluded) | none | 85 | — |
| ok | docx | 28 | — |
| fetch_failed | none | 2 | — |

**Scanned fraction among PDFs: 89 / (132 + 89) ≈ 40%.** The spec's 44-PDF
sample put it at 45%; the full run lands just under that. The transcription
budget should be sized against ~40% of PDF attachments, not 45%.

Extraction outcomes otherwise: 160 attachments extracted with a usable text
layer (132 PDF + 28 docx), 2 failed to fetch.

## Transcription smoke test (2026-08-18)

Validated the CLAUDE_API_KEY end to end on a single real scanned PDF
(attachment id 2, iulaan 407568, 2 pages, 878,606 bytes). A synchronous
`messages.create` with native PDF input returned Thaana text. Observed failure
mode: the model over-produced (~508k chars on a 2-page document — repetition
looping), which the 20,000-char `TEXT_CAP` and the CER gate exist to contain.

The cer_harness has not yet been run (needs a batch of text-layer PDFs
re-transcribed); `TRANSCRIBE_MAX_CER` remains at the 0.15 default.

## Scanned fraction, measured 2026-08-18

Taken with `extract_attachments --no-transcribe` before the defect A fix, so
the counts are read out of the resulting statuses rather than the summary line.

| | Count | Share of PDFs |
|---|---|---|
| scanned (no text layer) | 89 | 40.3% |
| text layer via pdftotext | 132 | 59.7% |
| docx | 28 | - |
| fetch_failed | 2 | - |
| not yet processed | 85 | - |

Spec 5.6.2 estimated 45% from a 44-file sample. Measured 40.3% over 221 PDFs,
so the sample was close and the transcription budget in that section holds.

## Recovery of the --no-transcribe rows (P5 task 0)

Defect A shipped `ocr_failed` (terminal, spec 5.7) for every measured scanned
PDF, so the paid transcription run that followed would have been a silent
no-op. `reset_no_transcribe` recovered all 89 rows to `pending`, preserving
`page_count` and `chars_per_page`. Verified 2026-08-18 with the dry-run count
(89) then a live run (89 reset).
## OCR backend evaluation, 2026-08-18

Superseded an earlier draft of this section that concluded "Vision: reject".
That conclusion was wrong in an important way: Google Vision fails **alone**
and succeeds as the **grounding layer** for a repair step. Both claims are
below.

Four Thaana-dense gazette PDFs with an existing text layer supplied a free
answer key via `pdftotext` (spec 5.6.1), plus two genuinely scanned pages
scored by anchor overlap, which needs no reference.

Every backend received **rasterized pixels** except where noted. Handing a
model the original PDF lets it read the embedded text layer and score near
zero having measured nothing.

### Metrics

- **CER** against `pdftotext`. Both sides stripped of Unicode format
  characters: `pdftotext` emits bidi controls (U+202A/B/C) into 61% of this
  corpus, 6.5% of characters in the densest file, and OCR can never produce
  them. Leaving them in inflates CER ~6.5 points before any real error.
- **fili/consonant.** Thaana is fully vocalized, so the corpus scores 0.99.
  Detects dropped diacritics and, above ~1.05, runaway generation. **It does
  not detect fabrication** — see below.
- **anchor overlap.** Fraction of the iulaan's known title+office vocabulary
  appearing in the output. Title and office come from the gazette HTML and are
  never OCR'd, so this works with no reference text and is the only metric
  that caught fabrication.

### Text-layer documents, CER

| Backend | Input | CER mean | fili | Cost/page |
|---|---|---|---|---|
| Claude Haiku 4.5 | **native PDF** | 0.058-0.281 | 0.99 | $0.0173 |
| Google Cloud Vision (`dv`) | rasterized PNG | 0.378 | 0.72-0.79 | $0.0015 |
| Vision + Claude repair | rasterized PNG | 0.276-0.348 | 0.96 | $0.0139 |
| LiteParse 2.13 (text layer) | PDF | 0.655 | 1.00 | free |
| Tesseract 5.5 `div` fast/best | rasterized PNG | 0.844-0.881 | 0.10-0.16 | free |
| Claude Haiku 4.5 | rasterized PNG | 1.043 | 0.93-1.00 | $0.0125 |

### Scanned documents, anchor overlap — the case that decides

Text-layer CER is a proxy. Production only ever sends genuinely scanned pages,
and the ranking inverts there.

| Pipeline | anchor | fili | Speed | Cost/page | Verifiable |
|---|---|---|---|---|---|
| Claude Haiku, native PDF | **0%** | 0.97 | ~10s | $0.0173 | no |
| Vision alone | 80% | 0.81 | — | $0.0015 | — |
| Vision -> Claude repair | **93%** | 0.94 | ~10s | $0.0139 | no |
| Vision -> gemmatranslate:12b | 73% | 0.95 | 157s | $0.0015 | no |
| **Vision -> T5 corrector, gated** | **87%** | 0.90 | **0.8s** | **$0.0015** | **yes** |

### Why the LLM-only path was rejected

**On a pristine scan, Claude fabricates.** Attachment 28 is an ADh. Mandhoo
Council notice about a futsal team. Haiku returned 18,550 characters of looping
boilerplate from one page; Opus 4.5 returned a fluent, confident notice about
Addu City Gender Ministry building repairs, including the `މިނިސްޓްރީ` that
users had already reported seeing in place of `މިސްކިތް`. The page was
rendered and inspected: pristine 300 DPI, crisply typeset, fully legible. This
is a Thaana capability limit, not an input-quality problem.

**Both fabrications scored 0.99-1.00 on fili/consonant.** The invented words
are orthographically perfect. Anchor overlap separated them cleanly: 0% for
the fabrication, 83-93% for faithful transcription.

**Digit corruption is independently disqualifying.** Vision returned
`446332441` where a seven-digit Maldivian number belongs. Spec 5.2 layer 0
makes fabricated salaries impossible by extracting digits with regex *before*
the model sees anything; corrupted digits mean the grounding validator
confirms wrong values with full confidence.

### Why the free local options were rejected

**Tesseract** (and therefore LiteParse's OCR path, which is Tesseract by
default) scores fili 0.10-0.16 — it barely detects diacritics. `tessdata_best`
at 4.5 MB is no better than the packaged 1.7 MB model. On the scanned page it
scored 0% anchor overlap: no usable consonant skeleton to repair.

**LiteParse text extraction** preserves every diacritic (fili 1.00) but emits
Thaana in the wrong character order — `ިއްލަދޫ` for `ފިއްލަދޫ`. 35% of words
match exactly, 56% as anagrams, **0% reversed**. Whole-string, per-line and
Thaana-run reversal all made CER worse; two targeted fili-placement repairs
changed word recovery by nothing. Recovering the order needs glyph-run
reconstruction. Worth revisiting if fixed upstream — it is free, local, and
returns bounding boxes, which is what the salary-table work in 5.6 wants.

**gemmatranslate:12b** restores fili well (0.95) but drops anchor to 73% and
takes 157s/page. It re-voweled Vision's own error `ލަންދޫ` (Landhoo) where the
document says `މަންދޫ` (Mandhoo) — the mistake was Vision's, faithfully
preserved.

### The chosen pipeline

`alakxender/t5-dhivehi-typo-corrector-asr` — a 60M `t5-small` fine-tuned on
Dhivehi ASR error correction. Ungated, MIT, and its tokenizer round-trips
Thaana with zero `<unk>`.

Fed Vision's output line by line it restores fili from 0.81 to **0.98** in
0.8s per page **on CPU**, leaving the GPU free for translation. Every repaired
word is then gated: accepted only where its consonant skeleton matches the
OCR, aligned with `difflib` on the skeleton sequence so a single inserted word
does not invalidate the page.

The gate is what makes this the only verifiable option. It rejected
`އދ.މަންދޫ` -> `އާދަމުގެފާނމަންދޫ` and **raised** anchor from 80% to 87%,
at the cost of fili (0.98 -> 0.90) because rejected words revert to Vision's
unvoweled form. That trade is worth taking given what unverified repair
produced above.

    truth:   އަރިއަތޮޅު ދެކުނުބުރީ މަންދޫ ކައުންސިލްގެ އިދާރާ
    output:  އަރިއަތޮޅު ދެކުނުބުރީ ލަންދޫ ކައުންސިލްގެ އިދާރާ

Every word exact but `ލަންދޫ`, which is Vision's consonant error and beyond
any repairer's reach.

**Corpus economics at 40,500 scanned pages:** ~$61, versus $563 with Claude
repair or $700 with Claude native. Roughly 17 hours single-threaded on CPU,
trivially parallel.

### Residual gap and the cheap way to close it

The remaining 13 points to Claude's 93% are entirely Vision's consonant
errors; no repairer can recover characters that were never read. Attachment 23
caps at 69% for the same reason. If that matters later, gate at page level:
run T5 everywhere and escalate only pages below an anchor threshold to Claude,
bounding paid work to genuinely hard documents.

### Caveats

Two scanned documents and four text-layer documents. Indicative, not
conclusive; re-measure over a larger sample during the backfill.

### Not evaluated

**Gemini** — the account returned `429: prepayment credits are depleted`.
Different training data, still the most promising untested cloud option.

**`naturecodeproject/dhivehi`** (Qwen3-8B + Dhivehi LoRA) — gated, needs
manual approval, no GGUF, and would need to beat a 60M model already at 0.98
fili while being slower and requiring the GPU. Deprioritized.

**`aya-expanse`** — its 23 supported languages do not include Dhivehi.
