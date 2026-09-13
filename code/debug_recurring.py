"""
Debug tool: dump exactly what detect_and_project_recurring() invents for a
given user, plus the resulting daily balance timeline. Use this to see
WHY amount_safe_to_pay comes out wrong, instead of guessing at thresholds.

Run:
    python debug_recurring.py <user_id> <request_date YYYY-MM-DD>

Example:
    python debug_recurring.py user_01 2024-03-03
"""

import sys
from datetime import timedelta

from config import parse_date
from data_store import DataStore
from events import resolve_events_for_user
from recurring import detect_and_project_recurring
from forecasting import build_daily_timeline, suffix_min


def main():
    user_id = sys.argv[1] if len(sys.argv) > 1 else "user_01"
    request_date = parse_date(sys.argv[2]) if len(sys.argv) > 2 else parse_date("2024-03-03")

    store = DataStore()
    profile = store.profile_by_user[user_id]
    print(f"User: {user_id}  Home currency: {profile['home_currency']}")
    print(f"Starting balance: {profile['current_available_balance']}")
    print(f"Minimum to keep: {profile['minimum_balance_to_keep']}")
    print(f"Request date: {request_date}")
    print()

    events = resolve_events_for_user(store, user_id)
    forecast_end = request_date + timedelta(days=90)

    projected = detect_and_project_recurring(events, request_date, forecast_end)

    print(f"--- {len(projected)} PROJECTED recurring events in [{request_date}, {forecast_end}] ---")
    for e in sorted(projected, key=lambda x: x["settlement_date"]):
        print(f"  {e['settlement_date']}  {e['category']:20s}  {e['resolved_amount']:>12.2f} {e['currency']}  ({e['event_id']})")

    print()
    total_projected = sum(float(e["resolved_amount"]) for e in projected)
    print(f"TOTAL projected debits over the window: {total_projected:.2f} {profile['home_currency']}")

    print()
    print("--- Real events (settled/pending/scheduled) already in the window ---")
    for e in sorted(events, key=lambda x: x["settlement_date"] or ""):
        settle = parse_date(e["settlement_date"]) if e["settlement_date"] else None
        if settle and request_date <= settle <= forecast_end:
            print(f"  {e['settlement_date']}  {e['category']:20s}  {e['direction']:6s}  "
                  f"{e['resolved_amount']:>12.2f} {e['currency']}  status={e['status']}")

    print()
    dates, balances = build_daily_timeline(store, user_id, request_date)
    smin = suffix_min(balances)
    print(f"Balance on request_date: {balances[0]:.2f}")
    print(f"Worst projected balance in 90-day window: {smin[0]:.2f}")
    print(f"Minimum required: {profile['minimum_balance_to_keep']}")
    print(f"Implied headroom (amount_safe_to_pay basis): {smin[0] - float(profile['minimum_balance_to_keep']):.2f}")

    # show the date the worst balance actually occurs on
    worst_idx = balances.index(min(balances))
    print(f"Worst point occurs on: {dates[worst_idx]}  (balance: {balances[worst_idx]:.2f})")


if __name__ == "__main__":
    main()