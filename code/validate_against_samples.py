"""
Validation harness. NOT training -- there's no model being fit here, this
is a regression check: run decide() against sample_requests.csv (which
already has ground-truth output columns filled in) and report where our
logic disagrees, so bugs like the recurring-expense overcorrection get
caught systematically instead of by spot-checking random rows.

Per the spec: sample_requests.csv is for understanding format/decision
style, not for scoring against the real evaluation set (requests.csv).
This script only touches sample_requests.csv -- it never writes to or
reads from the real output.csv, so it can't contaminate your submission.

Run:
    python validate_against_samples.py
"""

import csv
from config import load_csv
from data_store import DataStore
from decision import decide


FIELDS_TO_COMPARE = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


def close_enough(a, b, tol=0.01):
    """Numeric comparison with small tolerance for float rounding."""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def main():
    store = DataStore()
    samples = load_csv("sample_requests.csv")

    mismatches = 0
    total = 0

    for sample in samples:
        total += 1
        try:
            our_result = decide(store, sample)
        except Exception as exc:
            print(f"[{sample['request_id']}] CRASHED: {exc}")
            mismatches += 1
            continue

        row_mismatches = []
        for field in FIELDS_TO_COMPARE:
            expected = sample.get(field, "")
            actual = our_result.get(field, "")
            if field == "amount_safe_to_pay":
                if not close_enough(expected, actual):
                    row_mismatches.append((field, expected, actual))
            else:
                if str(expected).strip() != str(actual).strip():
                    row_mismatches.append((field, expected, actual))

        if row_mismatches:
            mismatches += 1
            print(f"\n[{sample['request_id']}] MISMATCH:")
            for field, expected, actual in row_mismatches:
                print(f"    {field}:")
                print(f"        expected: {expected}")
                print(f"        got:      {actual}")

    print(f"\n{'=' * 50}")
    print(f"{total - mismatches}/{total} sample requests matched exactly")
    print(f"{mismatches}/{total} had at least one field mismatch")


if __name__ == "__main__":
    main()