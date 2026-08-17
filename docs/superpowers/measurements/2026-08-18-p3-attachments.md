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
