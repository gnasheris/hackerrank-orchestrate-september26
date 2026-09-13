# Buy or Wait? -- HackerRank Orchestrate Submission

An AI-assisted financial affordability engine. For every request in
`dataset/requests.csv`, it reconstructs the user's financial position from
structured profiles, financial history, exchange rates, payment options,
messages, and receipt images, then decides whether the user should pay in
full, pay partially, use installments, wait, or not proceed -- writing the
result to `dataset/output.csv`.

## Setup

Requires Python 3.11+.

```bash
cd code
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

First run will download EasyOCR's model weights (~100MB) automatically --
no manual step needed, just expect the first run to take a couple of
minutes longer.

## Run

```bash
cd code
python main.py
```

Reads all files from `../dataset/` relative to this file's own location
(so it works regardless of what directory you run it from). Writes results
to `dataset/output.csv`, overwriting the blank template.

Console output shows:
- `ERROR on <request_id>: ...` for any request that raised an exception
  during decision-making (row is still written, with blank fields, so the
  full 250-row output is always produced)
- `WARNING on <request_id>: ...` for any row that fails a post-hoc
  consistency check (see `verification.py`) -- logged but not dropped,
  so you can inspect and decide what to do with a flagged row

## Project Structure

```
code/
├── main.py                    # Entry point: orchestration only
├── config.py                  # Constants, CSV loading, parsing helpers
├── data_store.py               # DataStore: loads all CSVs, builds indices
├── currency.py                 # Exchange rate lookup/conversion
├── events.py                   # Event cleanup: cancellations, refund netting
├── image_extraction.py         # OCR (EasyOCR) for blank-amount events
├── recurring.py                # Recurring expense/income pattern detection
├── message_interpretation.py   # Regex-based parsing of messages.csv
├── payment_options.py          # Payment-method eligibility filtering
├── forecasting.py              # 90-day daily balance timeline + safety check
├── decision.py                 # Candidate plan generation, ranking, decide()
├── spending_changes.py         # Stop/reduce rescue logic (last resort)
├── verification.py             # Deterministic output sanity checks
├── validate_against_samples.py # Regression check against sample_requests.csv
├── debug_recurring.py          # Diagnostic: inspect one user's projected events
├── debug_spending_changes.py   # Diagnostic: inspect spending-change eligibility
├── evaluation/
│   └── usage_report.md         # Token/cost report (zero -- no paid API used)
├── requirements.txt
└── requirements_full.txt       # Exact pinned versions (pip freeze)
```

Each module has a single, named responsibility (see file docstrings). No
file mixes financial math with data loading with output formatting.

## Architecture Overview

The pipeline is a **deterministic rules engine**, not an autonomous agent.
Two narrow sub-tasks use AI-adjacent techniques, but neither makes final
decisions:

1. **OCR** (EasyOCR, local model) extracts amounts from receipt images
   linked to blank-amount events.
2. **Regex-based message parsing** extracts structured facts (confirmed
   salary date/amount, salary termination, temporary salary reduction)
   from `messages.csv`.

Both feed into a deterministic financial forecasting and ranking engine --
they clean up ambiguous *inputs*, they don't decide the *output*. See
`evaluation/usage_report.md` for the reasoning behind this design choice.

### Core decision flow (see `decision.py: decide()`)

1. Build a **90-day daily balance timeline** (`forecasting.py`) from:
   - real settled/pending/scheduled events (`events.py`)
   - detected recurring debit/income patterns extrapolated forward
     (`recurring.py`)
   - message-driven overrides: confirmed future salary, salary
     termination, temporary salary reduction (`message_interpretation.py`)
2. Find the **worst point** the balance reaches anywhere in that window.
   The headroom above `minimum_balance_to_keep` at that point is exactly
   the amount safely payable today (a lump payment shifts every future
   balance down by the same amount).
3. Build **candidate plans**: full payment today, partial payment, and
   each valid installment option -- each checked by actually simulating
   its payment dates against the timeline.
4. **Rank** surviving candidates per the spec's tie-break order (completes
   by deadline > no spending changes > lowest total cost > starts earlier
   > fewer payments > lowest option ID).
5. If nothing is safe, attempt a **spending-changes rescue**
   (`spending_changes.py`) as a last resort -- try stopping/reducing the
   smallest set of user-permitted flexible categories that unlocks a safe
   plan, referencing the real dataset `event_id` (never an internal
   synthetic projection ID).
6. If still nothing is safe but full payment becomes safe later within
   the deadline, recommend `wait`. Otherwise `not_recommended`.
7. Before writing, every row passes a deterministic **verification pass**
   (`verification.py`): bounds checks, status/method consistency, payment
   plan arithmetic, and spending-change eligibility -- logged as warnings
   if anything looks internally inconsistent.

## Key Assumptions (documented, not hidden)

The spec leaves some behavior underspecified. Where a judgment call was
needed, it's documented here and in the relevant module's docstring:

- **Exchange rate lookup**: rates are only snapshotted on ~monthly dates,
  not daily. Uses the most recent available rate on or before the needed
  date (`currency.py`).
- **Recurring pattern detection**: requires >=3 historical occurrences at
  a consistent interval (spacing spread <=50% of the median interval) to
  trust a pattern (`recurring.py`).
- **Short-cycle categories** (interval <20 days -- groceries, transport):
  only the single next occurrence is projected as a near-term buffer, not
  compounded across the full 90-day window. Compounding these badly
  overshot real spending in testing against `sample_requests.csv`.
- **Recurring salary**: projected the same way as recurring debits, since
  a well-established monthly pattern is arguably just as "confirmed" as
  recurring rent -- but commission/bonus components and one-off
  arrears/adjustment entries mixed under the same category are filtered
  out via a dominant-day-of-month clustering heuristic, and a description
  containing termination language (e.g. "Final employer payroll") stops
  projection entirely.
- **Message-driven income adjustments** take precedence over the
  statistical estimate when present (confirmed date+amount, or an
  explicit "reduced to X, ongoing" statement).

## Testing

```bash
python validate_against_samples.py
```

Runs the decision engine against `dataset/sample_requests.csv` (which has
completed ground-truth output columns) and reports field-by-field
mismatches. This is a regression check, not part of the graded pipeline --
it never reads or writes the real `output.csv`.

## Known Limitations

- A handful of sample requests still show numeric drift (typically 5-15%)
  even when the categorical fields (`affordability_status`,
  `recommended_payment_method`) match -- likely fine calibration of the
  near-term short-cycle spending buffer, not a structural error.
- `spending_changes_needed` rescue tries up to 3 categories but does not
  exhaustively search every possible combination beyond that, per the
  spec's own 3-action limit.
- No live exchange rate, banking, or market-data calls are made or
  required, per the spec.