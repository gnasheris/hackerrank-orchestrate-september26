"""DataStore: the single source of truth for loaded CSV data and lookup indices.
This is the 'expert' on what data exists -- it doesn't make decisions."""

from collections import defaultdict
from config import load_csv, parse_date


class DataStore:
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