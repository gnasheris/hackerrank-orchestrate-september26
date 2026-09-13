"""Payment option eligibility. The expert on request_payment_options.csv
vs. a user's financial_profiles.csv preferences."""


def eligible_payment_methods(profile):
    return set(profile["payment_methods_user_will_consider"].split("|"))


def installment_span_months(option):
    n = int(option["number_of_payments"])
    freq = int(option["payment_frequency_days"]) if option["payment_frequency_days"] else 0
    total_days = (n - 1) * freq
    return total_days / 30.0  # approx


def filter_valid_options(profile, options):
    allowed_methods = eligible_payment_methods(profile)
    max_months = profile.get("max_installment_months")
    max_months = float(max_months) if max_months else None

    valid = []
    for o in options:
        if o["payment_method"] not in allowed_methods:
            continue
        if o["payment_method"] == "installments":
            if max_months is None:
                continue  # user won't consider installments at all
            if installment_span_months(o) > max_months:
                continue
        valid.append(o)
    return valid