"""Event resolution: cleaning up financial_events.csv rows for a user.
The expert on 'what actually happened to this user's cash'."""

from config import parse_amount
from image_extraction import extract_amount_from_image


def resolve_events_for_user(store, user_id):
    """
    Returns a cleaned list of event dicts for a user:
      - cancelled events dropped
      - linked cancellation/refund pairs netted (both dropped if they cancel out)
      - blank amounts filled from linked images
    Each returned event gets extra keys: 'resolved_amount', 'signed_amount'
    (positive for credit, negative for debit, in ORIGINAL currency).
    """
    raw = store.events_by_user.get(user_id, [])
    by_id = {e["event_id"]: e for e in raw}

    dropped = set()

    for e in raw:
        if e["status"] == "cancelled":
            dropped.add(e["event_id"])
            continue
        linked = e.get("linked_event_id")
        if linked and linked in by_id:
            parent = by_id[linked]
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
            amt = extract_amount_from_image(store, e["event_id"])
        signed = amt if e["direction"] == "credit" else -amt
        e = dict(e)
        e["resolved_amount"] = amt
        e["signed_amount"] = signed
        resolved.append(e)

    return resolved