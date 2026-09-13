"""Shared constants and low-level parsing helpers. No business logic here."""

import csv
import os
from datetime import datetime

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dataset")
OUTPUT_PATH = os.path.join(DATASET_DIR, "output.csv")
FORECAST_DAYS = 90

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def load_csv(filename):
    """Load a CSV from DATASET_DIR into a list of dicts."""
    path = os.path.join(DATASET_DIR, filename)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_date(s):
    if not s:
        return None
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def parse_amount(s):
    if s is None or str(s).strip() == "":
        return None
    return float(s)


def parse_bool(s):
    return str(s).strip().lower() == "true"


def format_amount(a):
    """Format a number the way the sample data does: no unnecessary
    trailing '.0', but preserve real decimals (e.g. 25256 not 25256.0,
    but 17229139.2 stays as-is)."""
    r = round(float(a), 2)
    if r == int(r):
        return str(int(r))
    s = f"{r:.2f}"
    if s.endswith("0"):
        s = s[:-1]
    return s