"""Build dated workload baselines from published Indian facility reports."""

import argparse
import json
import re
from collections import Counter
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import mean, median


DATA = Path(__file__).with_name("observations.json")
WINDOW_MONTHS = 12
MIN_ESTIMATE_MONTHS = 3
MIN_BACKTEST_MONTHS = 6


def month_index(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError(f"Expected a YYYY-MM period, got {value!r}")
    return date.fromisoformat(f"{value}-01").year * 12 + int(value[-2:]) - 1


def month_label(index):
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def count_value(value, *, allow_fraction=False):
    if type(value) is int and value >= 0:
        return value
    if allow_fraction and type(value) is float and isfinite(value) and value >= 0:
        return value
    raise ValueError(f"Invalid activity count: {value!r}")


def monthly_counts(chunks):
    values = {}
    seen = set()
    for chunk in chunks:
        first = month_index(chunk["start"])
        for month, count in enumerate(chunk["counts"], start=first):
            if month in seen:
                raise ValueError(f"Duplicate observation for {month_label(month)}")
            seen.add(month)
            if count is not None:
                values[month] = count_value(count)
    return values


def trailing_counts(values, end):
    return [value for month, value in values.items() if end - WINDOW_MONTHS < month <= end]


def backtest(values):
    """Compare rolling median and latest month on the same chronological targets."""
    scored = []
    for month, count in sorted(values.items()):
        history = trailing_counts(values, month - 1)
        if len(history) >= MIN_BACKTEST_MONTHS and month - 1 in values:
            scored.append((
                month_label(month), abs(median(history) - count), abs(values[month - 1] - count),
            ))
    if not scored:
        return None
    return {
        "scored_months": len(scored),
        "first_scored_month": scored[0][0],
        "last_scored_month": scored[-1][0],
        "rolling_median_mae": round(mean(row[1] for row in scored), 2),
        "previous_month_mae": round(mean(row[2] for row in scored), 2),
    }


def estimate(series, cutoff):
    values = {month: count for month, count in monthly_counts(series.get("monthly", [])).items() if month < cutoff}
    last = max(values, default=None)
    recent = trailing_counts(values, last) if last is not None else []
    direct = {
        "typical_month": None,
        "observed_monthly_min": None,
        "observed_monthly_max": None,
        "months_used": 0,
        "period_months": None,
        "first_month": None,
        "last_month": None,
    }
    if len(recent) >= MIN_ESTIMATE_MONTHS:
        first = min(month for month in values if last - WINDOW_MONTHS < month)
        direct.update(
            method="median_of_reported_months", typical_month=median(recent),
            observed_monthly_min=min(recent), observed_monthly_max=max(recent), months_used=len(recent),
            first_month=month_label(first), last_month=month_label(last),
        )
    elif "period" in series:
        period = series["period"]
        first = month_index(period["start"])
        end = month_index(period["end"])
        if first > end:
            raise ValueError(f"Reversed reporting period for {series['id']}")
        if end < cutoff:
            calendar_months = end - first + 1
            if ("total" in period) == ("monthly_average" in period):
                raise ValueError(f"Expected one reported period measure for {series['id']}")
            if "total" in period:
                months = period.get("exposure_months", calendar_months)
                if type(months) is not int or not 1 <= months <= calendar_months:
                    raise ValueError(f"Invalid exposure duration for {series['id']}")
                value = count_value(period["total"]) / months
                method = "reported_total_divided_by_period_months"
                direct["reported_period_total"] = period["total"]
            else:
                months = None
                value = count_value(period["monthly_average"], allow_fraction=True)
                method = "publisher_reported_monthly_average"
            direct.update(method=method, typical_month=round(value, 2), period_months=months,
                          first_month=period["start"], last_month=period["end"])
    result = {
        **{key: value for key, value in series.items() if key not in {"monthly", "period", "transformation"}},
        "method": "insufficient_reported_months",
        **direct,
        "reported_months": len(values),
        "backtest": backtest(values),
    }
    result["months_since_last_reported_period"] = (
        cutoff - 1 - month_index(direct["last_month"]) if direct["last_month"] else None
    )
    return result


def build(data, as_of):
    cutoff = month_index(as_of)
    sources = data["sources"]
    target_counts = Counter()
    estimates = []
    ids = set()
    for series in data["series"]:
        if series["service"] not in data["targets"]:
            raise ValueError(f"Unknown Care needed option: {series['service']}")
        if series["id"] in ids:
            raise ValueError(f"Duplicate series ID: {series['id']}")
        ids.add(series["id"])
        result = estimate(series, cutoff)
        result["source"] = sources[series["source"]]
        estimates.append(result)
        if result["typical_month"] is not None:
            target_counts[series["service"]] += 1
    return {
        "as_of_month": as_of,
        "targets": {name: {**target, "benchmarks": target_counts[name]} for name, target in data["targets"].items()},
        "estimates": estimates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA, help="Observation manifest")
    parser.add_argument("--as-of", help="Exclude this month and later months (YYYY-MM)")
    parser.add_argument("--output", type=Path, help="Write JSON results to this path instead of stdout")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    result = build(data, args.as_of or data["as_of_month"])
    serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
