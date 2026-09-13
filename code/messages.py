"""Message interpretation. The expert on messages.csv.

TODO: not wired up yet. Needs to classify each message into a structured
adjustment (salary_change, salary_ended, ignore_scam, pending_not_cash,
realized_credit, recurring_expense_amended, etc.) -- almost certainly an
LLM call given the varied phrasing and Indonesian text. See conversation
history for the archetype table we identified in messages.csv.
"""


def get_message_adjustments(store, user_id, request_id):
    """
    Returns a list of structured adjustment dicts for this user/request.
    Stubbed -- always returns [] for now.
    """
    _msgs = store.messages_by_user.get(user_id, [])
    return []