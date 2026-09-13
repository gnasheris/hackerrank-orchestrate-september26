"""
Debug tool: show exactly what get_eligible_flexible_changes() finds for a
given user, so we can see why the spending-changes rescue does or doesn't
fire, instead of guessing.

Run:
    python debug_spending_changes.py <user_id>

Example:
    python debug_spending_changes.py user_11
"""

import sys
from data_store import DataStore
from events import resolve_events_for_user
from spending_changes import get_eligible_flexible_changes


def main():
    user_id = sys.argv[1] if len(sys.argv) > 1 else "user_11"

    store = DataStore()
    profile = store.profile_by_user[user_id]

    print(f"User: {user_id}")
    print(f"expense_categories_to_protect: {profile.get('expense_categories_to_protect')}")
    print(f"expense_categories_user_is_willing_to_stop: {profile.get('expense_categories_user_is_willing_to_stop')}")
    print(f"expense_categories_user_is_willing_to_reduce: {profile.get('expense_categories_user_is_willing_to_reduce')}")
    print()

    events = resolve_events_for_user(store, user_id)

    print("--- All stoppable/reducible events for this user (regardless of eligibility) ---")
    for e in events:
        if e.get("flexibility") in ("stoppable", "reducible"):
            print(f"  {e['event_id']}  category={e['category']:20s}  flexibility={e['flexibility']:10s}  "
                  f"min_allowed={e.get('minimum_allowed_amount')}  date={e['settlement_date']}  "
                  f"amount={e['resolved_amount']}")

    print()
    eligible = get_eligible_flexible_changes(profile, events)
    print(f"--- ELIGIBLE changes found: {len(eligible)} ---")
    for cat, c in eligible.items():
        print(f"  category={cat}  action={c['action']}  event_id={c['event_id']}  reduce_to={c['reduce_to']}")

    if not eligible:
        print("  (none -- rescue cannot help this user at all)")


if __name__ == "__main__":
    main()