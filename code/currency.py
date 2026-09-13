"""Currency conversion. The expert on money-across-currencies math.

ASSUMPTION (documented, not explicitly stated in the spec): rates are only
snapshotted on ~monthly dates, so we use the most recent available rate ON
OR BEFORE the needed date. If the direct pair isn't found, try the inverse.
If neither exists, raise -- never silently guess.
"""


def get_rate(store, from_ccy: str, to_ccy: str, on_date):
    if from_ccy == to_ccy:
        return 1.0

    direct = store.rate_index.get((from_ccy, to_ccy))
    if direct:
        best = None
        for d, r in direct:
            if d <= on_date:
                best = r
        if best is not None:
            return best

    inverse = store.rate_index.get((to_ccy, from_ccy))
    if inverse:
        best = None
        for d, r in inverse:
            if d <= on_date:
                best = r
        if best is not None:
            return 1.0 / best

    raise ValueError(f"No exchange rate found for {from_ccy}->{to_ccy} on/before {on_date}")


def convert(store, amount: float, from_ccy: str, to_ccy: str, on_date):
    return amount * get_rate(store, from_ccy, to_ccy, on_date)