"""
Buy or Wait? -- HackerRank Orchestrate submission
Entry point: reads dataset/*, writes dataset/output.csv (or ./output.csv, see OUTPUT_PATH)

Run:
    python main.py

Structure:
    1. Data loading (CSV -> dicts/lists, minimal deps, uses only csv + datetime)
    2. Currency conversion (dated snapshot lookup with documented fallback)
    3. Event resolution (link chains, cancellations, blank amounts -> image extraction)
    4. Message interpretation (LLM-backed; stubbed here)
    5. 90-day cash-flow forecast per user
    6. Payment-option evaluation + ranking (tie-break ladder from spec)
    7. Output row assembly + CSV write

NOTE: This is a first-pass skeleton. Sections marked TODO need to be filled in
against your actual dataset once you've run it and inspected real output.
"""

import csv
import os
from datetime import date, datetime, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATASET_DIR = "dataset"
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

# ---------------------------------------------------------------------------
# 1. Data loading
# ---------------------------------------------------------------------------

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
    if s is None or s.strip() == "":
        return None
    return float(s)


def parse_bool(s):
    return str(s).strip().lower() == "true"


class DataStore:
    """Holds all loaded tables and precomputed indices."""

    def __init__(self):
        self.requests = load_csv("requests.csv")
        self.profiles = load_csv("financial_profiles.csv")
        self.events = load_csv("financial_events.csv")
        self.rates = load_csv("exchange_rates.csv")
        self.payment_options = load_csv("request_payment_options.csv")
        self.messages = load_csv("messages.csv")
        self.images = load_csv("images.csv")

        self.profile_by_user = {p["user_id"]: p for p in self.profiles}

        self.events_by_user = defaultdict(list)
        for e in self.events:
            self.events_by_user[e["user_id"]].append(e)

        self.options_by_request = defaultdict(list)
        for o in self.payment_options:
            self.options_by_request[o["request_id"]].append(o)

        self.messages_by_user = defaultdict(list)
        self.messages_by_request = defaultdict(list)
        for m in self.messages:
            self.messages_by_user[m["user_id"]].append(m)
            if m.get("request_id"):
                self.messages_by_request[m["request_id"]].append(m)

        self.images_by_request = defaultdict(list)
        for img in self.images:
            self.images_by_request[img["request_id"]].append(img)

        # rate index: (from_ccy, to_ccy) -> sorted list of (date, rate)
        self.rate_index = defaultdict(list)
        for r in self.rates:
            key = (r["from_currency"], r["to_currency"])
            self.rate_index[key].append((parse_date(r["rate_date"]), float(r["rate"])))
        for key in self.rate_index:
            self.rate_index[key].sort()


# ---------------------------------------------------------------------------
# 2. Currency conversion
# ---------------------------------------------------------------------------

def get_rate(store: DataStore, from_ccy: str, to_ccy: str, on_date: date):
    """
    Look up the conversion rate from_ccy -> to_ccy for a given date.

    ASSUMPTION (documented, not explicitly stated in the spec): since exchange
    rates are only snapshotted on ~monthly dates, we use the most recent
    available rate ON OR BEFORE `on_date`. If the direct pair isn't found,
    try the inverse pair. If neither exists, raise -- do NOT silently guess.
    """
    if from_ccy == to_ccy:
        return 1.0

    direct = store.rate_index.get((from_ccy, to_ccy))
    if direct:
        candidates = [r for d, r in direct if d <= on_date]
        if candidates:
            # most recent d <= on_date; since list sorted by date, take last valid
            best = None
            for d, r in direct:
                if d <= on_date:
                    best = r
            if best is not None:
                return best

    inverse = store.rate_index.get((to_ccy, from_ccy))
    if inverse:
        best = None
        for d, r in inverse:
            if d <= on_date:
                best = r
        if best is not None:
            return 1.0 / best

    raise ValueError(f"No exchange rate found for {from_ccy}->{to_ccy} on/before {on_date}")


def convert(store: DataStore, amount: float, from_ccy: str, to_ccy: str, on_date: date):
    return amount * get_rate(store, from_ccy, to_ccy, on_date)


# ---------------------------------------------------------------------------
# 3. Event resolution
# ---------------------------------------------------------------------------

def resolve_events_for_user(store: DataStore, user_id: str):
    """
    Return a cleaned list of event dicts for a user:
      - cancelled events dropped
      - linked cancellation/refund pairs netted (both dropped if they cancel out)
      - blank amounts filled from linked images (TODO: wire up vision extraction)
    Each returned event gets extra keys: 'resolved_amount', 'signed_amount'
    (positive for credit, negative for debit, in ORIGINAL currency).
    """
    raw = store.events_by_user.get(user_id, [])
    by_id = {e["event_id"]: e for e in raw}

    # Track which event_ids are neutralized (cancelled or exactly reversed)
    dropped = set()

    for e in raw:
        if e["status"] == "cancelled":
            dropped.add(e["event_id"])
            continue
        linked = e.get("linked_event_id")
        if linked and linked in by_id:
            parent = by_id[linked]
            # If parent was cancelled, and this is the real settled replacement -> keep this one, parent already dropped
            # If this is a refund that reverses parent 1:1 -> drop both (net zero)
            if e["event_type"] == "refund" and parent["status"] == "settled":
                try:
                    if float(e["amount"]) == float(parent["amount"]):
                        dropped.add(e["event_id"])
                        dropped.add(linked)
                except (TypeError, ValueError):
                    pass  # blank amount -- leave for image resolution before netting

    resolved = []
    for e in raw:
        if e["event_id"] in dropped:
            continue
        amt = parse_amount(e.get("amount"))
        if amt is None:
            amt = extract_amount_from_image(store, e["event_id"])  # TODO stub below
        signed = amt if e["direction"] == "credit" else -amt
        e = dict(e)
        e["resolved_amount"] = amt
        e["signed_amount"] = signed
        resolved.append(e)

    return resolved


def extract_amount_from_image(store: DataStore, event_id: str):
    """
    TODO: find the image whose related_event_id == event_id, load
    dataset/media/images/<image_id>.png, and call a vision-capable model
    to extract the amount. Stubbed for now.
    """
    for img in store.images:
        if img.get("related_event_id") == event_id:
            image_path = os.path.join(DATASET_DIR, "media", "images", f"{img['image_id']}.png")
            raise NotImplementedError(
                f"Need vision extraction for {image_path} (event {event_id})"
            )
    raise ValueError(f"No amount and no linked image for event {event_id}")


# ---------------------------------------------------------------------------
# 4. Message interpretation (LLM-backed; stubbed)
# ---------------------------------------------------------------------------

def get_message_adjustments(store: DataStore, user_id: str, request_id: str):
    """
    TODO: for each message tied to this user/request, classify it into a
    structured adjustment, e.g.:
        {"type": "salary_change", "new_amount": ..., "currency": ...,
         "effective_date": ..., "ends": False}
        {"type": "salary_ended"}
        {"type": "ignore_scam"}
        {"type": "pending_not_cash", "related_event_id": ...}
        {"type": "realized_credit", "related_event_id": ...}
        {"type": "recurring_expense_amended", "category": "rent",
         "multiplier": 1.12, "effective_date": ...}
    This almost certainly wants an LLM call per message (or batched per user)
    since the phrasing varies and includes Indonesian text. Stub returns [].
    """
    msgs = store.messages_by_user.get(user_id, [])
    # Placeholder: no adjustments applied yet.
    return []


# ---------------------------------------------------------------------------
# 5. 90-day forecast
# ---------------------------------------------------------------------------

def forecast_balance(store: DataStore, user_id: str, from_date: date, days: int = FORECAST_DAYS):
    """
    Returns a list of (date, projected_balance) for each day in the forecast
    window, applying:
      - starting balance from profile
      - settled/scheduled/pending events already known
      - recurring patterns detected from history (TODO)
      - message-derived adjustments (TODO)

    This is intentionally simple right now: it applies known dated events
    within the window and does NOT yet extrapolate recurring monthly items
    beyond what's explicitly in financial_events.csv. Extend this once we
    see how far financial_events.csv extends past `from_date` for a given user.
    """
    profile = store.profile_by_user[user_id]
    home_ccy = profile["home_currency"]
    balance = float(profile["current_available_balance"])

    events = resolve_events_for_user(store, user_id)
    to_date = from_date + timedelta(days=days)

    # Only forward-looking, cash-affecting events
    relevant = []
    for e in events:
        settle = parse_date(e["settlement_date"])
        if settle is None or settle < from_date or settle > to_date:
            continue
        if e["status"] not in ("settled", "pending", "scheduled"):
            continue
        if e["status"] == "pending" and e["direction"] == "credit":
            continue  # don't count pending credits
        relevant.append(e)

    relevant.sort(key=lambda e: parse_date(e["settlement_date"]))

    timeline = [(from_date, balance)]
    running = balance
    for e in relevant:
        amt_home = convert(store, e["resolved_amount"], e["currency"], home_ccy, parse_date(e["settlement_date"]))
        signed = amt_home if e["direction"] == "credit" else -amt_home
        running += signed
        timeline.append((parse_date(e["settlement_date"]), running))

    return timeline


# ---------------------------------------------------------------------------
# 6. Payment-option evaluation
# ---------------------------------------------------------------------------

def eligible_payment_methods(profile):
    return set(profile["payment_methods_user_will_consider"].split("|"))


def installment_span_months(option):
    n = int(option["number_of_payments"])
    freq = int(option["payment_frequency_days"]) if option["payment_frequency_days"] else 0
    total_days = (n - 1) * freq
    return total_days / 30.0  # approx


def filter_valid_options(profile, options):
    allowed_methods = eligible_payment_methods(profile)
    max_months = profile.get("max_installment_months")
    max_months = float(max_months) if max_months else None

    valid = []
    for o in options:
        if o["payment_method"] not in allowed_methods:
            continue
        if o["payment_method"] == "installments":
            if max_months is None:
                continue  # user won't consider installments at all
            if installment_span_months(o) > max_months:
                continue
        valid.append(o)
    return valid


def rank_options(options, desired_completion_date):
    """
    Apply the tie-break ladder from the spec:
      1. Completes by desired_completion_date
      2. No spending changes required (TODO: wire in once spending-change logic exists)
      3. Minimize total amount paid
      4. Start earlier
      5. Fewer payments
      6. Lowest payment_option_id
    """
    def sort_key(o):
        first_pay = parse_date(o["first_payment_date"])
        n = int(o["number_of_payments"])
        freq = int(o["payment_frequency_days"]) if o["payment_frequency_days"] else 0
        last_pay = first_pay + timedelta(days=(n - 1) * freq)
        completes_on_time = 0 if last_pay <= desired_completion_date else 1
        total = float(o["total_payable_amount"])
        return (
            completes_on_time,
            total,
            first_pay,
            n,
            o["payment_option_id"],
        )

    return sorted(options, key=sort_key)


# ---------------------------------------------------------------------------
# 7. Per-request decision (core logic -- TODO: this is the heart of the task)
# ---------------------------------------------------------------------------

def decide(store: DataStore, request: dict) -> dict:
    user_id = request["user_id"]
    profile = store.profile_by_user[user_id]
    home_ccy = profile["home_currency"]
    request_date = parse_date(request["request_date"])
    desired_completion = parse_date(request["desired_completion_date"])
    requested_amount = float(request["requested_amount"])
    allows_partial = parse_bool(request["allows_partial_payment"])
    min_balance = float(profile["minimum_balance_to_keep"])

    timeline = forecast_balance(store, user_id, request_date)
    options = store.options_by_request.get(request["request_id"], [])
    valid_options = filter_valid_options(profile, options)

    # TODO: apply message adjustments to timeline before using it
    adjustments = get_message_adjustments(store, user_id, request["request_id"])

    # --- placeholder decision logic ---
    # This is where the real affordability engine goes: walk the timeline,
    # find amount_safe_to_pay on request_date, find earliest_date_for_full_payment,
    # then choose among full_payment / partial_payment / installments / wait /
    # not_recommended per the eligibility + ranking rules.

    balance_on_request_date = timeline[0][1]
    amount_safe = max(0.0, min(requested_amount, balance_on_request_date - min_balance))

    if amount_safe >= requested_amount:
        status = "affordable_now"
        method = "full_payment" if "full_payment" in eligible_payment_methods(profile) else "not_recommended"
        plan = f"{request_date.isoformat()}:{requested_amount}" if method == "full_payment" else "none"
        earliest_full = request_date.isoformat()
    else:
        status = "not_affordable"  # TODO: distinguish affordable_later / affordable_with_plan
        method = "not_recommended"
        plan = "none"
        earliest_full = ""

    explanation = (
        f"Balance on {request_date} is {balance_on_request_date:.2f} {home_ccy}; "
        f"minimum to keep is {min_balance:.2f}; requested {requested_amount:.2f}."
    )

    return {
        "request_id": request["request_id"],
        "amount_safe_to_pay": round(amount_safe, 2),
        "affordability_status": status,
        "recommended_payment_method": method,
        "payment_plan": plan,
        "earliest_date_for_full_payment": earliest_full,
        "spending_changes_needed": "none",
        "decision_explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    store = DataStore()
    rows = []
    for request in store.requests:
        try:
            row = decide(store, request)
        except Exception as exc:
            # Fail loudly per-request but keep going so one bad row doesn't
            # kill the whole run. Log to stderr for visibility.
            print(f"ERROR on {request['request_id']}: {exc}")
            row = {c: "" for c in OUTPUT_COLUMNS}
            row["request_id"] = request["request_id"]
        rows.append(row)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()