"""Test XGBoost on service-specific caseload and estimate missing site periods."""

import argparse
import json
from collections import Counter
from math import ceil, isfinite
from pathlib import Path
from statistics import mean, median

import numpy as np
import xgboost as xgb


ROOT = Path(__file__).resolve().parent
PARAMETERS = {
    "objective": "count:poisson",
    "tree_method": "hist",
    "max_depth": 2,
    "eta": 0.05,
    "reg_lambda": 10,
    "nthread": 1,
    "seed": 42,
}
BOOST_ROUNDS = 100


def unpack(cohort, key):
    return [dict(zip(cohort["columns"], row, strict=True)) for row in cohort[key]]


def numeric(value):
    return type(value) in {int, float} and isfinite(value)


def validate(cohort):
    observations = unpack(cohort, "observations")
    applications = unpack(cohort, "applications")
    columns = set(cohort["columns"])
    required = {"facility_id", "facility", "district", "period", "months", "total", "source"}
    if not required <= columns or len(columns) != len(cohort["columns"]):
        raise ValueError(f"Invalid columns in {cohort['cohort']}")
    if not observations or not cohort["features"] or not set(cohort["features"]) <= columns - required:
        raise ValueError(f"Missing or invalid model inputs in {cohort['cohort']}")
    if len({row["district"] for row in observations}) < 3:
        raise ValueError(f"District holdout needs at least three districts in {cohort['cohort']}")
    if cohort.get("capacity_field") and cohort["capacity_field"] not in cohort["features"]:
        raise ValueError(f"Capacity must be a model input in {cohort['cohort']}")
    extra = set(cohort.get("ablation_features", []))
    if not extra <= columns - required - set(cohort["features"]):
        raise ValueError(f"Invalid ablation inputs in {cohort['cohort']}")
    last_observed_period = max(row["period"] for row in observations)

    seen = set()
    district_by_facility = {}
    for row in [*observations, *applications]:
        key = (row["facility_id"], row["period"])
        if key in seen:
            raise ValueError(f"Repeated or already observed facility-period: {key}")
        seen.add(key)
        district = district_by_facility.setdefault(row["facility_id"], row["district"])
        if district != row["district"]:
            raise ValueError(f"Facility belongs to multiple districts: {row['facility_id']}")
        if row["source"] not in cohort["sources"]:
            raise ValueError(f"Unknown source for {key}")
        if type(row["months"]) is not int or not 1 <= row["months"] <= 12:
            raise ValueError(f"Invalid months covered for {key}")

    for row in observations:
        if type(row["total"]) is not int or row["total"] < 0:
            raise ValueError(f"Invalid observed count for {row['facility_id']}")
        for field in cohort["features"]:
            value = row[field]
            if not numeric(value) and not (isinstance(value, str) and value.strip()):
                raise ValueError(f"Invalid training feature {field} for {row['facility_id']}")
    if any(row["total"] is not None for row in applications):
        raise ValueError("Application rows must have missing counts")
    if any(row["period"] < last_observed_period for row in applications):
        raise ValueError("Use a cohort that ends by the application period")

    for field in cohort["features"]:
        if any(numeric(row[field]) for row in observations) and not all(numeric(row[field]) for row in observations):
            raise ValueError(f"Mixed numeric and categorical values for {field}")
    capacity = cohort.get("capacity_field")
    if capacity and any(not numeric(row[capacity]) or row[capacity] <= 0 for row in observations):
        raise ValueError(f"Invalid capacity in {cohort['cohort']}")
    return observations, applications


def monthly(row):
    return row["total"] / row["months"]


def fit_predict(train, test, features):
    categories = {
        field: sorted({row[field] for row in train})
        for field in features if isinstance(train[0][field], str)
    }

    def matrix(rows):
        return np.asarray([
            [value for field in features for value in (
                [row[field] == category for category in categories[field]]
                if field in categories else [row[field]]
            )]
            for row in rows
        ], dtype=np.float32)

    model = xgb.train(
        PARAMETERS,
        xgb.DMatrix(matrix(train), label=[monthly(row) for row in train], nthread=1),
        num_boost_round=BOOST_ROUNDS,
    )
    return model.predict(xgb.DMatrix(matrix(test), nthread=1)).tolist()


def baseline_predictions(train, test, features, capacity, *, prior_period=False):
    categories = [field for field in features if isinstance(train[0][field], str)]
    pooled = median(monthly(row) for row in train)
    predictions = []
    for row in test:
        peers = [p for p in train if all(p[field] == row[field] for field in categories)]
        if len(peers) < 2:
            peers = train
        defaults = {"pooled_median": pooled}
        if categories:
            defaults["peer_type_median"] = median(monthly(p) for p in peers)
        if capacity:
            rate = median(monthly(p) / p[capacity] for p in peers)
            defaults["capacity_scaled_median"] = row[capacity] * rate
        if prior_period:
            earlier = latest_before(train, row)
            fallback = defaults.get("capacity_scaled_median", defaults.get("peer_type_median", pooled))
            defaults["last_observed_or_peer"] = monthly(earlier) if earlier else fallback
        predictions.append(defaults)
    return predictions


def latest_before(observations, row):
    return max(
        (p for p in observations if p["facility_id"] == row["facility_id"] and p["period"] < row["period"]),
        key=lambda p: p["period"], default=None,
    )


def predictions(train, test, cohort, *, prior_period=False):
    features = cohort["features"]
    fitted = fit_predict(train, test, features)
    baselines = baseline_predictions(train, test, features, cohort.get("capacity_field"), prior_period=prior_period)
    return [{"xgboost": model, **baseline} for model, baseline in zip(fitted, baselines, strict=True)]


def score(train, test, cohort, *, prior_period=False):
    return [
        {
            "facility_id": row["facility_id"], "facility": row["facility"],
            "district": row["district"], "period": row["period"],
            "actual": monthly(row), "predictions": scored,
        }
        for row, scored in zip(test, predictions(train, test, cohort, prior_period=prior_period), strict=True)
    ]


def heldout(observations, cohort, *, future=False):
    latest_period = max(row["period"] for row in observations)
    target_rows = [row for row in observations if not future or row["period"] == latest_period]
    results = []
    for district in sorted({row["district"] for row in target_rows}):
        train = [
            row for row in observations
            if row["district"] != district and (not future or row["period"] < latest_period)
        ]
        test = [row for row in target_rows if row["district"] == district]
        if train:
            results.extend(score(train, test, cohort))
    return results


def forward_known_facilities(observations, cohort):
    """Score later workloads from equipment and site features known before that period."""
    records = []
    first_period = min(row["period"] for row in observations)
    for period in sorted({row["period"] for row in observations} - {first_period}):
        train = [row for row in observations if row["period"] < period]
        test = []
        for row in observations:
            if row["period"] == period and (prior := latest_before(train, row)):
                test.append({**row, **{field: prior[field] for field in cohort["features"]}})
        if test:
            records.extend(score(train, test, cohort, prior_period=True))
    return records


def summarize(records):
    if not records:
        return None
    metrics = {}
    for method in records[0]["predictions"]:
        errors = [row["predictions"][method] - row["actual"] for row in records]
        absolute = sorted(map(abs, errors))
        metrics[method] = {
            "mae": round(mean(absolute), 2),
            "median_absolute_error": round(median(absolute), 2),
            "p90_absolute_error": round(absolute[ceil(0.9 * len(absolute)) - 1], 2),
            "mean_signed_error": round(mean(errors), 2),
        }
    return {
        "rows": len(records),
        "facilities": len({row["facility_id"] for row in records}),
        "districts": len({row["district"] for row in records}),
        "metrics": metrics,
    }


def application_reason(row, observations, features):
    for field in features:
        values = [p[field] for p in observations]
        value = row[field]
        if numeric(values[0]):
            if not numeric(value):
                return f"missing_numeric_feature:{field}"
            if not min(values) <= value <= max(values):
                return f"outside_observed_range:{field}"
        else:
            districts = {p["district"] for p in observations if p[field] == value}
            if len(districts) < 2:
                return f"category_lacks_district_holdout_support:{field}"
    return None


def apply(cohort, observations, applications, metrics):
    best_baseline = min(
        (method for method in metrics if method != "xgboost"),
        key=lambda method: metrics[method]["mae"],
    )
    better = metrics["xgboost"]["mae"] < metrics[best_baseline]["mae"]
    error = metrics["xgboost"]["p90_absolute_error"]
    reasons = [application_reason(row, observations, cohort["features"]) for row in applications]
    eligible = [row for row, reason in zip(applications, reasons, strict=True) if not reason]
    fitted = iter(predictions(observations, eligible, cohort, prior_period="last_observed_or_peer" in metrics) if eligible else [])

    results = []
    for row, reason in zip(applications, reasons, strict=True):
        result = {
            **{k: v for k, v in row.items() if k != "total"},
            "service": cohort["service"], "unit": cohort["unit"],
            "source": cohort["sources"][row["source"]],
            "older_observations_in_cohort": latest_before(observations, row) is not None,
        }
        if link := cohort.get("facility_links", {}).get(row["facility_id"]):
            result["facility_link"] = link
        if reason:
            result.update(status="insufficient_feature_support", reason=reason, xgboost_typical_month=None)
        else:
            scored = next(fitted)
            point = scored["xgboost"]
            result.update(
                status="experimental_estimate",
                xgboost_typical_month=round(point, 2),
                comparator_typical_month=round(scored[best_baseline], 2),
                comparator=best_baseline,
                lower_mae_method="xgboost" if better else best_baseline,
                error_band=[round(max(0, point - error), 2), round(point + error, 2)],
            )
        results.append(result)
    return results


def evaluate(cohort):
    observations, applications = validate(cohort)
    district_records = heldout(observations, cohort)
    future_records = heldout(observations, cohort, future=True)
    known_site_records = forward_known_facilities(observations, cohort)
    district_summary = summarize(district_records)
    future_summary = summarize(future_records)
    known_site_summary = summarize(known_site_records)
    evaluations = {"unseen_district": district_summary, "later_period_unseen_district": future_summary,
                   "later_period_known_facility": known_site_summary}
    application_evaluation = "later_period_unseen_district" if future_summary else "unseen_district"
    if known_site_summary and applications and all(latest_before(observations, row) for row in applications):
        application_evaluation = "later_period_known_facility"
    application_metrics = evaluations[application_evaluation]["metrics"]
    imputed = apply(cohort, observations, applications, application_metrics)
    result = {
        "cohort": cohort["cohort"],
        "service": cohort["service"],
        "unit": cohort["unit"],
        "features": cohort["features"],
        "training_rows_by_period": dict(sorted(Counter(row["period"] for row in observations).items())),
        "sources": cohort["sources"],
        "evaluation": evaluations,
        "heldout_predictions": {
            "unseen_district": district_records,
            "later_period_unseen_district": future_records,
            "later_period_known_facility": known_site_records,
        },
        "application_evaluation": application_evaluation,
        "application_basis": cohort.get("application_basis"),
        "application_sources": [cohort["sources"][key] for key in cohort.get("application_sources", [])],
        "applications": imputed,
    }
    if extra := cohort.get("ablation_features"):
        matched = [row for row in observations if all(numeric(row[field]) for field in extra)]
        if len({row["district"] for row in matched}) >= 3:
            enriched = {**cohort, "features": [*cohort["features"], *extra]}
            result["feature_ablation"] = {
                "added_features": extra,
                "base": summarize(heldout(matched, cohort)),
                "enriched": summarize(heldout(matched, enriched)),
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path, help="Cohort manifests (defaults to all *_model_data.json)")
    parser.add_argument("--output", type=Path, help="Write results to this path instead of stdout")
    args = parser.parse_args()
    inputs = args.inputs or sorted(ROOT.glob("*_model_data.json"))
    if not inputs:
        parser.error("No cohort manifests found")
    results = {
        "xgboost_version": xgb.__version__,
        "parameters": PARAMETERS,
        "boost_rounds": BOOST_ROUNDS,
        "cohorts": [evaluate(json.loads(path.read_text(encoding="utf-8"))) for path in inputs],
    }
    rendered = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
