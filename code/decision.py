"""Decision logic. The expert on turning a forecast + payment options into
the final recommendation for one request.

KNOWN GAPS:
  - spending_changes_needed always returns 'none' (not wired in).
  - Message-driven adjustments not applied yet (messages.get_message_adjustments
    is still a stub).
"""

from datetime import timedelta

from config import parse_date, parse_bool, format_amount
from payment_options import eligible_payment_methods, filter_valid_options
from forecasting import build_daily_timeline, build_daily_timeline_with_changes, compute_safe_amount_and_earliest_date, plan_is_safe
from events import resolve_events_for_user
from spending_changes import get_eligible_flexible_changes, changes_to_string, find_minimal_rescue


def generate_installment_schedule(option, requested_amount):
    n = int(option["number_of_payments"])
    freq = int(option["payment_frequency_days"]) if option["payment_frequency_days"] else 0
    per_payment = float(option["payment_amount"])
    first = parse_date(option["first_payment_date"])
    return [(first + timedelta(days=i * freq), per_payment) for i in range(n)]


def build_candidates(store, request, dates, balances, amount_safe, earliest_full_date):
    profile = store.profile_by_user[request["user_id"]]
    min_balance = float(profile["minimum_balance_to_keep"])
    eligible = eligible_payment_methods(profile)
    requested_amount = float(request["requested_amount"])
    request_date = parse_date(request["request_date"])
    desired_completion = parse_date(request["desired_completion_date"])
    allows_partial = parse_bool(request["allows_partial_payment"])

    candidates = []

    if "full_payment" in eligible:
        payments = [(request_date, requested_amount)]
        if plan_is_safe(dates, balances, payments, min_balance):
            candidates.append({
                "method": "full_payment",
                "payments": payments,
                "total_paid": requested_amount,
                "start_date": request_date,
                "num_payments": 1,
                "payment_option_id": None,
                "completes_by_deadline": request_date <= desired_completion,
            })

    if (allows_partial and "partial_payment" in eligible
            and 0 < amount_safe < requested_amount
            and earliest_full_date is not None
            and earliest_full_date <= desired_completion):
        remainder = requested_amount - amount_safe
        payments = [(request_date, amount_safe), (earliest_full_date, remainder)]
        if plan_is_safe(dates, balances, payments, min_balance):
            candidates.append({
                "method": "partial_payment",
                "payments": payments,
                "total_paid": requested_amount,
                "start_date": request_date,
                "num_payments": 2,
                "payment_option_id": None,
                "completes_by_deadline": earliest_full_date <= desired_completion,
            })

    if "installments" in eligible:
        options = store.options_by_request.get(request["request_id"], [])
        valid_options = filter_valid_options(profile, [o for o in options if o["payment_method"] == "installments"])
        for opt in valid_options:
            schedule = generate_installment_schedule(opt, requested_amount)
            if plan_is_safe(dates, balances, schedule, min_balance):
                last_payment_date = schedule[-1][0]
                candidates.append({
                    "method": "installments",
                    "payments": schedule,
                    "total_paid": float(opt["total_payable_amount"]),
                    "start_date": schedule[0][0],
                    "num_payments": len(schedule),
                    "payment_option_id": opt["payment_option_id"],
                    "completes_by_deadline": last_payment_date <= desired_completion,
                })

    return candidates


def rank_candidates(candidates):
    def key(c):
        return (
            0 if c["completes_by_deadline"] else 1,
            c["total_paid"],
            c["start_date"],
            c["num_payments"],
            c["payment_option_id"] or "",
        )
    return sorted(candidates, key=key)


def decide(store, request):
    user_id = request["user_id"]
    profile = store.profile_by_user[user_id]
    home_ccy = profile["home_currency"]
    min_balance = float(profile["minimum_balance_to_keep"])
    request_date = parse_date(request["request_date"])
    desired_completion = parse_date(request["desired_completion_date"])
    requested_amount = float(request["requested_amount"])

    dates, balances = build_daily_timeline(store, user_id, request_date)
    amount_safe, earliest_full_date = compute_safe_amount_and_earliest_date(
        dates, balances, min_balance, requested_amount, desired_completion
    )

    candidates = build_candidates(store, request, dates, balances, amount_safe, earliest_full_date)
    ranked = rank_candidates(candidates)
    spending_changes_str = "none"

    if not ranked:
        # Last resort: try stopping/reducing permitted flexible categories.
        # A changeless plan always wins if one exists (handled above) --
        # this only runs when nothing safe was found without changes.
        events = resolve_events_for_user(store, user_id)
        eligible = get_eligible_flexible_changes(profile, events)

        if eligible:
            def test_fn(subset):
                change_map = {cat: (c["action"], c["reduce_to"]) for cat, c in subset.items()}
                d2, b2 = build_daily_timeline_with_changes(store, user_id, request_date, change_map)
                safe2, earliest2 = compute_safe_amount_and_earliest_date(
                    d2, b2, min_balance, requested_amount, desired_completion
                )
                return len(build_candidates(store, request, d2, b2, safe2, earliest2)) > 0

            rescue = find_minimal_rescue(eligible, test_fn)
            if rescue:
                change_map = {cat: (c["action"], c["reduce_to"]) for cat, c in rescue.items()}
                d2, b2 = build_daily_timeline_with_changes(store, user_id, request_date, change_map)
                safe2, earliest2 = compute_safe_amount_and_earliest_date(
                    d2, b2, min_balance, requested_amount, desired_completion
                )
                candidates = build_candidates(store, request, d2, b2, safe2, earliest2)
                ranked = rank_candidates(candidates)
                spending_changes_str = changes_to_string(rescue)

    if ranked:
        best = ranked[0]
        method = best["method"]
        plan_str = "|".join(f"{d.isoformat()}:{format_amount(a)}" for d, a in best["payments"])
        status = "affordable_now" if (method == "full_payment" and best["start_date"] == request_date) else "affordable_with_plan"
        explanation = (
            f"Lowest projected balance in the next 90 days determines safety. "
            f"Recommending {method} ({best['num_payments']} payment(s), "
            f"total {best['total_paid']:.2f} {home_ccy}), keeping balance at or "
            f"above the {min_balance:.2f} {home_ccy} minimum throughout the forecast."
        )
    elif earliest_full_date is not None and earliest_full_date <= desired_completion and "full_payment" in eligible_payment_methods(profile):
        method = "wait"
        status = "affordable_later"
        plan_str = f"{earliest_full_date.isoformat()}:{format_amount(requested_amount)}"
        explanation = (
            f"Not safe to pay {requested_amount:.2f} {home_ccy} today without breaking "
            f"the {min_balance:.2f} {home_ccy} minimum over the next 90 days. "
            f"Full payment becomes safe on {earliest_full_date.isoformat()}, "
            f"which is on or before the {desired_completion.isoformat()} deadline."
        )
    else:
        method = "not_recommended"
        status = "not_affordable"
        plan_str = "none"
        explanation = (
            f"No eligible payment method keeps the balance at or above the "
            f"{min_balance:.2f} {home_ccy} minimum within the 90-day forecast, "
            f"and/or full payment does not become safe before the "
            f"{desired_completion.isoformat()} deadline."
        )

    return {
        "request_id": request["request_id"],
        "amount_safe_to_pay": format_amount(amount_safe),
        "affordability_status": status,
        "recommended_payment_method": method,
        "payment_plan": plan_str,
        "earliest_date_for_full_payment": earliest_full_date.isoformat() if earliest_full_date else "",
        "spending_changes_needed": "none",  # TODO
        "decision_explanation": explanation,
    }