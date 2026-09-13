# Token Usage and Cost Report

## Summary

This solution makes **zero calls to a paid LLM API**. Total cost: **$0.00**.

| Metric | Value |
|---|---|
| Model provider(s) | None (no external LLM API used) |
| Model name(s) | N/A |
| Total model calls | 0 |
| Total input tokens | 0 |
| Total output tokens | 0 |
| Average tokens per request | 0 |
| Estimated total cost | $0.00 |
| Estimated cost per request | $0.00 |

## Why no LLM API calls

Two components of this system could plausibly have used an LLM, and both were
deliberately built as deterministic, local alternatives instead:

### 1. Image amount extraction (blank-amount events)

Handled by **EasyOCR** (`easyocr` Python package), an open-source, locally-run
OCR model. It downloads its own model weights once (~100MB) on first run and
performs inference entirely on-device (CPU in this environment) -- no network
calls to a paid API, no per-image cost. The extracted text is then parsed with
a small custom number-normalization routine (`image_extraction.py`) to handle
both comma-decimal and dot-decimal formatting conventions present across the
dataset's currencies (IDR, EUR, USD, INR, ZAR).

### 2. Message interpretation (messages.csv)

Handled by deterministic regex-based parsing (`message_interpretation.py`),
not an LLM call. This was a considered design choice, not a shortcut:

- The project contract (AGENTS.md / problem_statement.md, section 6.4)
  explicitly asks to "keep behavior deterministic where possible."
- Every date referenced in messages.csv is already written in literal
  ISO format (`YYYY-MM-DD`) inside the message text, and the recurring
  archetypes (salary confirmation, salary termination, temporary salary
  reduction) follow a small number of consistent templates across both
  English and Indonesian phrasing. This made a template-aware regex
  approach both accurate and fully auditable, rather than paying for
  LLM inference to solve a pattern-matching problem regex handles well.
- A deterministic approach also means the same input always produces the
  same output on every run, which matters for a financial decision system
  being graded partly on reliability across the full dataset.

## If a paid API had been used instead

Had a vision-capable LLM been used for image extraction instead of local
OCR, this section would report: model name, per-image input/output token
counts (an image + short instruction prompt, typically ~300-500 input
tokens and ~20-50 output tokens per call), the number of blank-amount
events processed, and the resulting estimated cost at the provider's
published per-token rate. This was evaluated and intentionally not used,
given EasyOCR's zero marginal cost and sufficient accuracy for this
dataset's receipt-style images.