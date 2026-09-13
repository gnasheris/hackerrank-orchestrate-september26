"""
Buy or Wait? -- HackerRank Orchestrate submission
Entry point. Just orchestration: load data, loop requests, write output.

Run:
    python main.py
"""

import csv

from config import OUTPUT_PATH, OUTPUT_COLUMNS
from data_store import DataStore
from decision import decide


def main():
    store = DataStore()
    rows = []
    for request in store.requests:
        try:
            row = decide(store, request)
        except Exception as exc:
            print(f"ERROR on {request['request_id']}: {exc}")
            row = {c: "" for c in OUTPUT_COLUMNS}
            row["request_id"] = request["request_id"]
        rows.append(row)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()