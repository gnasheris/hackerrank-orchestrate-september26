"""
Spending-changes rescue: if no baseline (unchanged) plan is safe, try
stopping/reducing permitted flexible recurring expenses to see if that
unlocks affordability. Only invoked as a LAST RESORT -- the spec's ranking
puts "no spending changes" above cost/timing when choosing between safe
plans, so a changeless plan always wins if one exists.

Spec grounding:
  - "Only non-protected, flexible events in a category the user permits
    may be changed."
  - "Stopping and reducing the same financial event are mutually exclusive...
    must reference different events."
  - Up to three actions: stop:<event_id> / reduce_to:<event_id>:<new_amount>

ASSUMPTION: the reported event_id anchors to the most recent REAL event of
that category (never one of our synthetic 'projected_*' ids, which don't
exist in the dataset and can't legitimately be cited as evidence). The
stop/reduce effect is still applied to every future occurrence of that
category -- real and projected -- when testing whether the change actually
unlocks a safe plan; we just report a real id as the anchor for "this
recurring commitment."
"""

from itertools import combinations


def get_eligible_flexible_changes(profile, events):
    """
    Returns dict: category -> {'action': 'stop'|'reduce', 'event_id': str,
    'reduce_to': float|None}, for every category where a real historical
    event is stoppable/reducible AND not protected AND permitted by the
    user's stop-list/reduce-list. Uses the most recent real event per
    category as the id anchor.
    """
    protect = set(profile.get("expense_categories_to_protect", "").split("|"))
    stop_ok = set(profile.get("expense_categories_user_is_willing_to_stop", "").split("|"))
    reduce_ok = set(profile.get("expense_categories_user_is_willing_to_reduce", "").split("|"))

    latest_by_category = {}
    for e in events:
        cat = e["category"]
        flexibility = e.get("flexibility", "")
        if cat in protect:
            continue
        if flexibility not in ("stoppable", "reducible"):
            continue
        existing = latest_by_category.get(cat)
        if existing is None or e["settlement_date"] > existing["settlement_date"]:
            latest_by_category[cat] = e

    eligible = {}
    for cat, e in latest_by_category.items():
        flexibility = e["flexibility"]
        if flexibility == "stoppable" and cat in stop_ok:
            eligible[cat] = {"action": "stop", "event_id": e["event_id"], "reduce_to": None}
        elif flexibility == "reducible" and cat in reduce_ok:
            min_amt = e.get("minimum_allowed_amount")
            if min_amt not in (None, ""):
                eligible[cat] = {
                    "action": "reduce",
                    "event_id": e["event_id"],
                    "reduce_to": float(min_amt),
                }
    return eligible


def changes_to_string(selected_changes):
    """selected_changes: dict category -> {'action','event_id','reduce_to'}"""
    if not selected_changes:
        return "none"
    parts = []
    for c in selected_changes.values():
        if c["action"] == "stop":
            parts.append(f"stop:{c['event_id']}")
        else:
            parts.append(f"reduce_to:{c['event_id']}:{c['reduce_to']}")
    return "|".join(parts)


def find_minimal_rescue(eligible, test_fn):
    """
    eligible: dict category -> change info
    test_fn: function(subset_dict) -> True/False, whether applying this
             subset of changes makes some candidate plan safe.
    Tries increasing subset sizes (1, 2, 3) and returns the smallest safe
    subset, or None if nothing up to size 3 works.
    """
    categories = list(eligible.keys())
    for size in range(1, min(3, len(categories)) + 1):
        for combo in combinations(categories, size):
            subset = {c: eligible[c] for c in combo}
            if test_fn(subset):
                return subset
    return None