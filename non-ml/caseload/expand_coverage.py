"""Attach reported caseloads and backtest same-site baselines for documented gaps."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from baseline_coverage import ROOT, read_json, release_sha256
from build import count_value, month_index, month_label


GRAINS = {"month": (1, 3, 4), "fiscal_year": (12, 2, 2), "calendar_year": (12, 2, 2)}
METHODS = ("last_observed", "recent_median")


def read_series(series, sources):
    step, _, _ = GRAINS[series["grain"]]
    observed = {}
    seen = set()
    for batch in series["observations"]:
        start = month_index(batch["start"])
        if series["grain"] == "fiscal_year" and start % 12 != 3:
            raise ValueError(f"Fiscal-year data must start in April: {series['id']}")
        if series["grain"] == "calendar_year" and start % 12 != 0:
            raise ValueError(f"Calendar-year data must start in January: {series['id']}")
        if batch["source"] not in sources:
            raise ValueError(f"Unknown source {batch['source']}")
        for offset, count in enumerate(batch["counts"]):
            period = start + offset * step
            if period in seen:
                raise ValueError(f"Repeated period {month_label(period)} in {series['id']}")
            seen.add(period)
            if count is not None:
                observed[period] = (count_value(count), batch["source"])
    return observed


def predictors(values, target, lag, grain):
    step, min_history, _ = GRAINS[grain]
    anchor = target - lag * step
    window = 12 if step == 1 else 3 * step
    months = sorted(m for m in values if anchor - window < m <= anchor)
    if anchor not in values or len(months) < min_history:
        return None
    return {"last_observed": values[anchor], "recent_median": median(values[m] for m in months)}, months


def backtest(values, target, lag, grain):
    scores = []
    for period in sorted(m for m in values if m < target):
        candidate = predictors(values, period, lag, grain)
        if candidate:
            predictions, _ = candidate
            scores.append({
                "period": month_label(period), "actual": values[period], **predictions,
            })
    if not scores:
        return {"scored_periods": 0, "method_mae": {}, "rows": []}
    return {
        "scored_periods": len(scores),
        "method_mae": {
            method: mean(abs(row[method] - row["actual"]) for row in scores)
            for method in METHODS
        },
        "rows": scores,
    }


def check_link(site, release):
    if not site.get("facility_id"):
        return "no_reviewed_project_facility_link"
    if not all(site.get(k) for k in ("identity_url", "identity_basis", "state")):
        return "incomplete_identity_review"
    rows = release.get(site["facility_id"], [])
    if not rows:
        return "facility_id_missing_from_release"
    if any(row["pin"].strip() != site["release_pin"].strip()
           or row["state"].strip().casefold() != site["state"].strip().casefold() for row in rows):
        return "project_identity_or_pin_mismatch"
    if not any(row["routing_eligible"].strip().casefold() == "true" for row in rows):
        return "project_location_hold"
    return None


def assess_target(series, site, observed, sources, target, cutoff, release):
    step, _, min_scored = GRAINS[series["grain"]]
    period = month_index(target["period"])
    if step == 12 and period % 12 != (3 if series["grain"] == "fiscal_year" else 0):
        raise ValueError(f"Invalid annual target: {target['period']}")
    data = {
        "series_id": series["id"],
        "site": site,
        "service": series["service"],
        "scope": series["scope"],
        "unit": series["unit"],
        "grain": series["grain"],
        "period_start": target["period"],
        "period_end": month_label(period + step - 1),
        "target_source_review": target["basis"],
    }
    known = {m: count / step for m, (count, _) in observed.items() if m < period}
    last = max(known, default=None)
    if last is not None and (period - last) % step:
        raise ValueError(f"Target and observations use different period boundaries: {series['id']}")
    lag = (period - last) // step if last is not None else None
    if lag is not None:
        data["forecast_horizon_periods"] = lag
        data["latest_prior_period_start"] = month_label(last)
        data["backtest"] = backtest(known, period, lag, series["grain"])

    reason = check_link(site, release)
    if period + step > cutoff:
        reason = "period_not_closed"
    if reason:
        return {**data, "status": "withheld", "reason": reason}
    if period in observed:
        count, source_id = observed[period]
        return {
            **data, "status": "reported", "reported_count": count,
            "typical_month": round(count / step, 2), "source": sources[source_id],
        }

    gap_source = target.get("gap_source")
    data["gap_kind"] = "listed_report_unreadable" if gap_source else "no_matching_total_recovered"
    if gap_source:
        data["gap_source"] = sources[gap_source]
    validation = data.get("backtest", {})
    if validation.get("scored_periods", 0) < min_scored:
        return {**data, "status": "withheld", "reason": "insufficient_matched_horizon_tests"}
    candidate = predictors(known, period, lag, series["grain"])
    if candidate is None:
        return {**data, "status": "withheld", "reason": "insufficient_recent_observations"}
    predictions, window = candidate
    method = min(METHODS, key=lambda name: validation["method_mae"][name])
    used = [window[-1]] if method == "last_observed" else window
    return {
        **data, "status": "baseline_estimate", "method": method,
        "typical_month": round(predictions[method], 2),
        "prior_periods": [
            {"period_start": month_label(m), "reported_count": observed[m][0],
             "source": sources[observed[m][1]]}
            for m in used
        ],
    }


def run_expansion(manifests, release, previous, care_options, as_of):
    cutoff = month_index(as_of)
    rows = []
    keys = set()
    series_ids = set()
    for manifest in manifests:
        sources = manifest["sources"]
        for series in manifest["series"]:
            if series["id"] in series_ids or series["service"] not in care_options:
                raise ValueError(f"Duplicate series ID or unmapped service: {series['id']}")
            series_ids.add(series["id"])
            site = manifest["sites"][series["site"]]
            observed = read_series(series, sources)
            for target in series["targets"]:
                row = assess_target(series, site, observed, sources, target, cutoff, release)
                if row["status"] != "withheld":
                    key = (site["facility_id"], row["service"], row["period_start"], row["period_end"])
                    if key in keys:
                        raise ValueError(f"Repeated linked facility-service-period: {key}")
                    keys.add(key)
                rows.append(row)

    previous_by_service = defaultdict(set)
    for prior in previous["estimates"]:
        previous_by_service[prior["service"]].add(prior["facility_id"])
    before = set().union(*previous_by_service.values())
    reported = {r["site"]["facility_id"] for r in rows if r["status"] == "reported"}
    estimated = {r["site"]["facility_id"] for r in rows if r["status"] == "baseline_estimate"}
    summary = []
    for service in care_options:
        selected = [r for r in rows if r["service"] == service]
        summary.append({
            "care_needed": service,
            "previous_estimated_facilities": len(previous_by_service[service]),
            "reported_facilities": len({r["site"]["facility_id"] for r in selected if r["status"] == "reported"}),
            "estimated_facilities": len({r["site"]["facility_id"] for r in selected if r["status"] == "baseline_estimate"}),
            "withheld_targets": sum(r["status"] == "withheld" for r in selected),
        })
    return {
        "as_of_month": as_of,
        "dataset": previous["dataset"],
        "summary": summary,
        "expansion_target_counts": dict(Counter(row["status"] for row in rows)),
        "previous_baseline_facilities": len(before),
        "additional_distinct_facilities": len((reported | estimated) - before),
        "additional_distinct_facilities_with_reported_counts": len(reported - before),
        "additional_distinct_facilities_with_estimates": len(estimated - before),
        "total_distinct_facilities_with_estimates": len(before | estimated),
        "total_distinct_facilities_with_caseload": len(before | reported | estimated),
        "targets": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    previous = read_json("baseline_coverage.json")
    if release_sha256(args.facilities) != previous["dataset"]["sha256"]:
        parser.error("First rebuild the baseline audit for this exact facility release")
    release = defaultdict(list)
    with args.facilities.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            release[row["facility_id"].strip()].append({k: row[k] for k in ("state", "pin", "routing_eligible")})
    targets = read_json("observations.json")
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "expansion").glob("*.json"))
    ]
    if not manifests:
        parser.error("No expansion source manifests found")
    results = run_expansion(manifests, release, previous, targets["targets"], targets["as_of_month"])
    text = json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
