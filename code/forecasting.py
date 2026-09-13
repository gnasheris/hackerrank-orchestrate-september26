"""90-day balance forecasting. The expert on 'what will this user's balance
look like over time', and the core safety-check math.

KNOWN GAP: recurring expense EXTRAPOLATION beyond what's explicitly dated in
financial_events.csv is NOT implemented yet. If a user's rent/groceries only
has history up to request_date with no future-dated rows, this will NOT
project future recurring debits -- amount_safe_to_pay will look too generous
for those users until this is added.
"""

from datetime import timedelta

from events import resolve_events_for_user
from currency import convert
from recurring import detect_and_project_recurring
from message_interpretation import get_message_driven_income_adjustments


def _get_full_event_list(store, user_id, start_date, end_date):
    """
    Shared logic for both timeline builders: real events + recurring
    projections + message-driven adjustments, all combined consistently.
    """
    events = resolve_events_for_user(store, user_id)
    projected_events = detect_and_project_recurring(events, start_date, end_date)

    extra_events, salary_ended = get_message_driven_income_adjustments(store, user_id)
    if salary_ended:
        # A message says this employment/income stream has ended -- don't
        # keep projecting recurring salary forward even if the historical
        # pattern would otherwise look clean and established.
        projected_events = [e for e in projected_events if not e["event_id"].startswith("projected_salary_")]

    return events + projected_events + extra_events


def build_daily_timeline(store, user_id, start_date, days=90):
    """
    Returns (dates, balances): parallel lists, one entry per day from
    start_date to start_date + days inclusive, in the user's home currency.
    """
    end_date = start_date + timedelta(days=days)
    all_events = _get_full_event_list(store, user_id, start_date, end_date)
    return _balances_from_events(store, user_id, all_events, start_date, days)


def build_daily_timeline_with_changes(store, user_id, start_date, changes, days=90):
    """
    Same as build_daily_timeline, but first applies hypothetical spending
    changes to every event (real or projected) matching a given category.
    changes: dict category -> ('stop', None) or ('reduce', new_amount)
    Used only to TEST whether a spending change would unlock a safe plan --
    the baseline (unchanged) timeline is still what amount_safe_to_pay and
    earliest_date_for_full_payment are reported from, per spec.
    """
    end_date = start_date + timedelta(days=days)
    all_events = _get_full_event_list(store, user_id, start_date, end_date)

    modified = []
    for e in all_events:
        cat = e["category"]
        if cat in changes:
            action, value = changes[cat]
            e = dict(e)
            if action == "stop":
                e["resolved_amount"] = 0.0
            elif action == "reduce":
                e["resolved_amount"] = min(float(e["resolved_amount"]), value)
        modified.append(e)

    return _balances_from_events(store, user_id, modified, start_date, days)


def _balances_from_events(store, user_id, events, start_date, days):
    profile = store.profile_by_user[user_id]
    home_ccy = profile["home_currency"]
    starting_balance = float(profile["current_available_balance"])
    end_date = start_date + timedelta(days=days)

    deltas = {}
    for e in events:
        settle = _parse_date_safe(e["settlement_date"])
        if settle is None or settle < start_date or settle > end_date:
            continue
        if e["status"] not in ("settled", "pending", "scheduled"):
            continue
        if e["status"] == "pending" and e["direction"] == "credit":
            continue
        if e["status"] == "scheduled" and e["direction"] == "credit" and e["category"] != "salary":
            continue  # never invent/count scheduled-but-unconfirmed non-salary credit

        amt_home = convert(store, e["resolved_amount"], e["currency"], home_ccy, settle)
        signed = amt_home if e["direction"] == "credit" else -amt_home
        deltas[settle] = deltas.get(settle, 0.0) + signed

    dates = [start_date + timedelta(days=i) for i in range(days + 1)]
    balances = []
    running = starting_balance
    for d in dates:
        running += deltas.get(d, 0.0)
        balances.append(running)

    return dates, balances


def _parse_date_safe(s):
    from config import parse_date
    return parse_date(s)


def suffix_min(values):
    out = [0.0] * len(values)
    running_min = float("inf")
    for i in range(len(values) - 1, -1, -1):
        running_min = min(running_min, values[i])
        out[i] = running_min
    return out


def compute_safe_amount_and_earliest_date(dates, balances, min_balance, requested_amount, desired_completion_date):
    smin = suffix_min(balances)
    amount_safe = max(0.0, min(requested_amount, smin[0] - min_balance))

    earliest_date = None
    for i, d in enumerate(dates):
        if smin[i] - requested_amount >= min_balance:
            earliest_date = d
            break

    return amount_safe, earliest_date


def apply_payments(dates, balances, payments):
    new_balances = list(balances)
    for pay_date, amount in payments:
        for i, d in enumerate(dates):
            if d >= pay_date:
                new_balances[i] -= amount
    return new_balances


def plan_is_safe(dates, balances, payments, min_balance):
    adjusted = apply_payments(dates, balances, payments)
    return min(adjusted) >= min_balance