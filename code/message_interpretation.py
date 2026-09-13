"""
Message interpretation. The expert on messages.csv -- specifically,
detecting two well-evidenced archetypes we catalogued in the dataset:

  1. CONFIRMED FUTURE SALARY: a new-employer message stating an explicit
     amount and date ("Your first salary will be INR 115000. The confirmed
     credit date is 2025-02-15."). This matters when a user is too new for
     our recurring-pattern detector to have enough history (<3 occurrences),
     but the actual next payment IS a known, confirmed fact -- just not one
     we can infer from repetition.

  2. SALARY ENDED: an employer message stating employment/contract has
     ended with no renewal confirmed. This should SUPPRESS any recurring
     salary projection for that user going forward, even if their prior
     history would otherwise look like a clean established pattern.

DESIGN CHOICE: this is regex-based, not an LLM call. The spec explicitly
says to "keep behavior deterministic where possible" (project contract
6.4), and every message we've seen states dates in literal ISO format
(YYYY-MM-DD) inside the text, so a template-aware regex is a legitimate,
explainable fit here rather than overkill.

SCOPE: only source_type == 'employer' messages are used for salary facts --
gig/investment/prize/merchant messages are deliberately out of scope here,
since the spec already tells us not to count that kind of income until
settled, and those archetypes don't need date/amount extraction the way
salary confirmations do.
"""

import re

from config import parse_date

ENDED_KEYWORDS = ("ended", "berakhir")
CONFIRMED_KEYWORDS = (
    "confirmed credit date",
    "confirmed for",
    "scheduled for",
    "resumes on",
    "is confirmed for",
    "dikonfirmasi untuk",
    "dijadwalkan pada",
    "berlaku mulai",
)
REDUCED_ONGOING_KEYWORDS = ("reduced", "berkurang")
RENT_KEYWORDS = ("rent", "sewa")
RENT_INCREASE_KEYWORDS = ("increase", "menaikkan", "naik")
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")

DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
AMOUNT_PATTERN = re.compile(r"\b([A-Z]{3})\s*([\d]+(?:[.,]\d+)?)")


def extract_date(text):
    m = DATE_PATTERN.search(text)
    return m.group(0) if m else None


def extract_amount(text):
    m = AMOUNT_PATTERN.search(text)
    if not m:
        return None
    ccy, amt = m.groups()
    return ccy, float(amt.replace(",", ""))


def is_salary_ended(text):
    lower = text.lower()
    if any(k in lower for k in ENDED_KEYWORDS):
        # Only treat as "ended" if there's no fresh confirmed date+amount
        # in the SAME message -- a message can both close out an old
        # arrangement and confirm a new one in one breath.
        if extract_date(text) is None or extract_amount(text) is None:
            return True
    return False


def is_rent_increase(text):
    """
    Matches messages like 'The renewed lease increases monthly rent by
    12%. The new amount applies from the next rent payment.' Returns the
    percentage increase as a float (e.g. 12.0), or None if no match.
    """
    lower = text.lower()
    if not any(k in lower for k in RENT_KEYWORDS):
        return None
    if not any(k in lower for k in RENT_INCREASE_KEYWORDS):
        return None
    m = PERCENT_PATTERN.search(text)
    if not m:
        return None
    return float(m.group(1))


def get_message_driven_expense_adjustments(store, user_id):
    """
    Returns dict: category -> (multiplier, effective_from_date).
    Currently detects rent-increase messages. Scans ALL source_types for
    this user (not restricted to a specific one), since rent-increase
    notices come from varied service-provider names across the dataset.
    If multiple such messages exist, the most recently SENT one wins,
    per the spec's "newer record from the same source" tie-break rule.
    """
    adjustments = {}
    for m in store.messages_by_user.get(user_id, []):
        text = m.get("message_text", "")
        pct = is_rent_increase(text)
        if pct is None:
            continue

        sent_at = m.get("sent_at", "")
        effective_date = parse_date(sent_at[:10]) if len(sent_at) >= 10 else None
        multiplier = 1 + (pct / 100.0)

        existing = adjustments.get("rent")
        if existing is None or (
            effective_date is not None
            and existing[1] is not None
            and effective_date > existing[1]
        ):
            adjustments["rent"] = (multiplier, effective_date)

    return adjustments


def is_salary_confirmation(text):
    lower = text.lower()
    if "salary" not in lower and "gaji" not in lower:
        return False
    if not any(k in lower for k in CONFIRMED_KEYWORDS):
        return False
    return extract_date(text) is not None and extract_amount(text) is not None


def is_salary_reduced_ongoing(text):
    """
    Matches messages like 'Your next salary is reduced to EUR 1422.85. The
    adjustment is due to approved unpaid leave.' or 'Your temporary monthly
    pay is EUR 1037.52. The reduced amount continues for the next payroll.'
    These state the real GOING-FORWARD rate, which can differ from what a
    naive 'minimum of recent occurrences' statistic would pick up -- e.g. a
    single anomalously-low prorated month shouldn't be mistaken for the new
    steady state.
    """
    lower = text.lower()
    if "salary" not in lower and "gaji" not in lower and "pay" not in lower:
        return False
    return any(k in lower for k in REDUCED_ONGOING_KEYWORDS)


def get_message_driven_income_adjustments(store, user_id):
    """
    Returns (extra_events, salary_ended, salary_override_amount):
      extra_events: list of synthetic scheduled-credit event dicts for any
                    message-confirmed future salary payment.
      salary_ended: bool -- if True, the caller should suppress recurring
                    salary PROJECTION (real historical rows are untouched).
      salary_override_amount: float|None -- if present, use this as the
                    conservative ongoing salary estimate INSTEAD OF the
                    naive statistical minimum, since the message states the
                    real going-forward rate explicitly.
    """
    extra_events = []
    salary_ended = False
    salary_override_amount = None

    for m in store.messages_by_user.get(user_id, []):
        if m.get("source_type") != "employer":
            continue
        text = m.get("message_text", "")

        if is_salary_confirmation(text):
            date = extract_date(text)
            ccy_amt = extract_amount(text)
            if date and ccy_amt:
                ccy, amt = ccy_amt
                extra_events.append({
                    "event_id": f"message_salary_{m['message_id']}",
                    "user_id": user_id,
                    "event_type": "income",
                    "description": "Message-confirmed salary",
                    "category": "salary",
                    "direction": "credit",
                    "resolved_amount": amt,
                    "currency": ccy,
                    "event_date": date,
                    "settlement_date": date,
                    "status": "scheduled",
                    "linked_event_id": "",
                    "flexibility": "fixed",
                    "minimum_allowed_amount": "",
                })
        elif is_salary_ended(text):
            salary_ended = True
        elif is_salary_reduced_ongoing(text):
            ccy_amt = extract_amount(text)
            if ccy_amt:
                _, amt = ccy_amt
                salary_override_amount = amt

    return extra_events, salary_ended, salary_override_amount