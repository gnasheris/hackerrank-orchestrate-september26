"""
Recurring expense and income detection and projection. The expert on
'what will this user's regular bills and salary look like going forward,
even past their explicit event history'.

Spec grounding (problem_statement.md / AGENTS.md 6.3):
  - "Detect recurrence only when history supports it."
  - "Forecast essential variable spending conservatively."
  - Recurring salary IS projected (unlike bonuses/commissions/gig payouts),
    since a well-established monthly pattern is arguably just as
    "confirmed by history" as recurring rent -- and the messages.csv
    dataset's heavy focus on salary continuation/change/termination only
    makes sense if salary is treated as an ongoing forecastable fact.

KNOWN GAPS:
  - spending_changes_needed is handled in spending_changes.py, not here.
  - Only ONE rent-style message adjustment (percentage increase) is wired
    in via message_interpretation.py / forecasting.py; other message
    archetypes mostly reinforce what an event's own status already says.
"""

import calendar
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from config import parse_date

# ---------------------------------------------------------------------------
# Date-stepping helpers
# ---------------------------------------------------------------------------

def add_one_month(d):
    """Step forward exactly one calendar month, keeping the same day-of-month
    where possible (clamped to the shorter month's last day if needed).
    Using this instead of 'add N days' avoids drift: monthly patterns landing
    on, say, the 15th every month will stay on the 15th indefinitely, rather
    than sliding earlier/later as 30/31-day steps compound over several
    projected occurrences."""
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day_of_month)
    return date(year, month, day)


def project_next_date(last_date, median_interval):
    """
    Step forward by the detected pattern. Genuinely monthly patterns
    (~27-32 day median, e.g. salary landing on the 15th, rent on the 2nd)
    use calendar-month stepping to avoid day-of-month drift. Other cadences
    (e.g. a 21-day dining cycle) don't have a 'day of month' to preserve,
    so stepping by the actual median interval is correct for those.
    """
    step = round(median_interval)
    if 27 <= step <= 32:
        return add_one_month(last_date)
    return last_date + timedelta(days=step)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECURRING_EVENT_TYPES = {"expense", "debt_payment", "subscription"}
TERMINATION_DESCRIPTION_KEYWORDS = ("final", "last payroll", "severance", "terminated")

MIN_OCCURRENCES = 3
MIN_INTERVAL_DAYS = 20          # ASSUMPTION: only extrapolate monthly-or-
                                 # slower patterns (rent, utilities, education,
                                 # debt, subscriptions) across the FULL 90-day
                                 # window. Short-cycle variable spending
                                 # (groceries, transport -- typically weekly-
                                 # ish) only gets a single near-term buffer
                                 # occurrence, not full compounding -- doing
                                 # so badly overshot real spending in testing
                                 # against sample_requests.csv.
MAX_SPREAD_RATIO = 0.5          # (max_interval - min_interval) / median_interval must be <= this

COMMISSION_KEYWORDS = ("commission", "bonus")


# ---------------------------------------------------------------------------
# Collecting and cleaning candidate recurring series
# ---------------------------------------------------------------------------

def _collect_recurring_series(events):
    """
    Groups events into two buckets:
      - debit series, keyed by category
      - the salary income series specifically (category == 'salary', credit) --
        EXCLUDING descriptions containing "commission"/"bonus". Some users'
        data tags both a constant monthly base salary AND a highly variable
        commission/bonus under the same category='salary', on a different
        day of month. Mixing them corrupts the interval-consistency check
        (alternating short/long gaps looks "inconsistent" even though the
        base salary alone is perfectly regular) and can cause the whole
        series -- including the reliable base salary -- to be rejected.
    """
    debit_by_category = defaultdict(list)
    salary_occurrences = []

    for e in events:
        settle = parse_date(e["settlement_date"])
        if settle is None or e["status"] != "settled":
            continue

        if e["event_type"] in RECURRING_EVENT_TYPES and e["direction"] == "debit":
            debit_by_category[e["category"]].append((settle, e))
        elif e["event_type"] == "income" and e["category"] == "salary" and e["direction"] == "credit":
            description = (e.get("description") or "").lower()
            if any(kw in description for kw in COMMISSION_KEYWORDS):
                continue
            salary_occurrences.append((settle, e))

    return debit_by_category, salary_occurrences


def _filter_by_dominant_day_of_month(occurrences, tolerance=3):
    """
    Keeps only occurrences within `tolerance` days of the most common
    day-of-month among them. Excludes one-off arrears/bonus/adjustment
    entries that share a category but land on a different day than the
    real recurring payroll date -- without needing to know their exact
    description wording in advance. More robust than a keyword blacklist,
    since it generalizes to wordings we haven't seen (e.g. "Promotion
    arrears payment", "August 2019 net salary").
    """
    if len(occurrences) < 2:
        return occurrences
    days = [d.day for d, _ in occurrences]
    mode_day = Counter(days).most_common(1)[0][0]
    return [(d, e) for d, e in occurrences if abs(d.day - mode_day) <= tolerance]


def _real_event_dates_by_category(events):
    """
    Maps category -> set of settlement dates already covered by a REAL
    event (settled, pending, or scheduled). Used to avoid generating a
    synthetic projected occurrence on a date a real event already covers --
    e.g. a data row explicitly confirming 'next salary on X' should not
    ALSO get a duplicate synthetic salary credit on that same date from
    pattern-based projection, which would double-count income.
    """
    lookup = defaultdict(set)
    for e in events:
        settle = parse_date(e["settlement_date"])
        if settle is not None and e["status"] in ("settled", "pending", "scheduled"):
            lookup[e["category"]].add(settle)
    return lookup


def _detect_pattern(occurrences):
    """Shared recurrence check: returns (median_interval, occurrences) or None
    if the pattern doesn't qualify. Interval-length decisions (full-quarter
    vs next-occurrence-only) are made by the caller, not here."""
    occurrences = sorted(occurrences, key=lambda x: x[0])
    dates = [d for d, _ in occurrences]

    if len(dates) < MIN_OCCURRENCES:
        return None

    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    median_interval = statistics.median(intervals)

    spread = max(intervals) - min(intervals)
    if spread > median_interval * MAX_SPREAD_RATIO:
        return None

    return median_interval, occurrences


# ---------------------------------------------------------------------------
# Main projection function
# ---------------------------------------------------------------------------

def detect_and_project_recurring(events, forecast_start, forecast_end):
    """
    events: resolved event dicts for one user (from events.resolve_events_for_user)
    Returns a list of synthetic event dicts, same shape as real ones, for
    projected future occurrences of:
      - recurring debit categories (rent, utilities, education, debt, subscriptions)
      - recurring salary income (if a consistent monthly pattern is established)
    ...that fall within [forecast_start, forecast_end]. Never duplicates a
    date already covered by a real event of the same category.
    """
    debit_by_category, salary_occurrences = _collect_recurring_series(events)
    real_dates = _real_event_dates_by_category(events)
    projected = []

    # --- Debit categories ---
    for category, occurrences in debit_by_category.items():
        result = _detect_pattern(occurrences)
        if result is None:
            continue
        median_interval, sorted_occurrences = result

        last_date, last_event = sorted_occurrences[-1]
        recent_amounts = [float(e["resolved_amount"]) for _, e in sorted_occurrences[-4:]]

        if median_interval < MIN_INTERVAL_DAYS:
            # Short-cycle essential variable spending (groceries, transport --
            # typically weekly-ish): compounding this across the full 90-day
            # window badly overshoots real spending. Include just the SINGLE
            # next upcoming occurrence as a modest near-term buffer instead.
            next_date = project_next_date(last_date, median_interval)
            if (forecast_start <= next_date <= forecast_end
                    and next_date not in real_dates.get(category, set())):
                synthetic = dict(last_event)
                synthetic["event_id"] = f"projected_{category}_{next_date.isoformat()}"
                synthetic["settlement_date"] = next_date.isoformat()
                synthetic["event_date"] = next_date.isoformat()
                synthetic["resolved_amount"] = statistics.mean(recent_amounts)
                synthetic["status"] = "settled"
                projected.append(synthetic)
            continue

        # Monthly-or-slower: project across the full window.
        step_days_approx = round(median_interval)
        days_remaining = (forecast_end - last_date).days
        projected_occurrence_count = days_remaining // step_days_approx if step_days_approx > 0 else 0

        # ASSUMPTION (revised): use the mean of recent occurrences uniformly,
        # rather than branching on occurrence count. For genuinely constant
        # categories (rent, debt -- same amount every month) mean equals
        # every value anyway, so this changes nothing there. For categories
        # that DO vary (healthcare, utilities), max was the most extreme
        # possible conservative choice; empirical testing against
        # sample_requests.csv showed we were trending slightly too
        # conservative (too LOW) more often than too generous, so mean is
        # a better-calibrated "conservative but not worst-case" choice.
        conservative_amount = statistics.mean(recent_amounts)

        next_date = project_next_date(last_date, median_interval)
        while next_date <= forecast_end:
            if next_date >= forecast_start and next_date not in real_dates.get(category, set()):
                synthetic = dict(last_event)
                synthetic["event_id"] = f"projected_{category}_{next_date.isoformat()}"
                synthetic["settlement_date"] = next_date.isoformat()
                synthetic["event_date"] = next_date.isoformat()
                synthetic["resolved_amount"] = conservative_amount
                synthetic["status"] = "settled"
                projected.append(synthetic)
            next_date = project_next_date(next_date, median_interval)

    # --- Salary ---
    salary_occurrences = _filter_by_dominant_day_of_month(salary_occurrences)
    salary_occurrences = sorted(salary_occurrences, key=lambda x: x[0])

    # A termination signal can live in the DATA itself, not just in
    # messages.csv -- e.g. a description like "Final employer payroll".
    # Check the most recent occurrence before deciding to extrapolate;
    # otherwise a clean, consistent pattern that's actually about to stop
    # gets projected forward as if it will continue indefinitely.
    if salary_occurrences:
        last_occurrence_desc = (salary_occurrences[-1][1].get("description") or "").lower()
        if any(kw in last_occurrence_desc for kw in TERMINATION_DESCRIPTION_KEYWORDS):
            salary_occurrences = []

    result = _detect_pattern(salary_occurrences)
    if result is not None:
        median_interval, sorted_occurrences = result
        last_date, last_event = sorted_occurrences[-1]
        recent_amounts = [float(e["resolved_amount"]) for _, e in sorted_occurrences[-4:]]
        # Conservative in the OPPOSITE direction from debits: use the
        # MINIMUM of recent occurrences, since overestimating future income
        # would make things look more affordable than they safely are.
        conservative_amount = min(recent_amounts)

        next_date = project_next_date(last_date, median_interval)
        while next_date <= forecast_end:
            if next_date >= forecast_start and next_date not in real_dates.get("salary", set()):
                synthetic = dict(last_event)
                synthetic["event_id"] = f"projected_salary_{next_date.isoformat()}"
                synthetic["settlement_date"] = next_date.isoformat()
                synthetic["event_date"] = next_date.isoformat()
                synthetic["resolved_amount"] = conservative_amount
                synthetic["status"] = "settled"
                projected.append(synthetic)
            next_date = project_next_date(next_date, median_interval)

    return projected