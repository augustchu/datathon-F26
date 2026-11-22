"""Audit and export the tested fiscal-year caseload baselines for the project release."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET_PERIOD = "FY2025–26"
PRIOR_PERIOD = "FY2024–25"
VERIFIED = {"verified_unchanged", "verified_corrected", "verified_filled"}
SERVICE_CODE_OVERRIDES = {"trauma_care": "Trauma / emergency", "nicu_level_3": "NICU"}


def service_code(label):
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


def index_release(rows, services):
    code_to_option = {service_code(service): service for service in services}
    code_to_option.update(SERVICE_CODE_OVERRIDES)
    facilities = defaultdict(list)
    documented = defaultdict(set)
    verified = defaultdict(set)
    unlinked = 0
    for row in rows:
        facility_id = row["facility_id"].strip()
        if not facility_id:
            unlinked += 1
            continue
        routable = row["routing_eligible"].strip().casefold() == "true"
        facilities[facility_id].append({
            "name": row["name"], "state": row["state"], "pin": row["pin"].strip(),
            "routing_eligible": routable,
        })
        if routable:
            for code in row["documented_services"].split("|"):
                service = code_to_option.get(code.strip().casefold())
                if service in services:
                    documented[service].add(facility_id)
                    if row["services_evidence_status"] in VERIFIED:
                        verified[service].add(facility_id)
    return facilities, documented, verified, unlinked


def unpack(cohort, field):
    return [dict(zip(cohort["columns"], row, strict=True)) for row in cohort[field]]


def match_release_site(facilities, link):
    if not link:
        return None, "no_reviewed_project_facility_link"
    records = facilities.get(link["fdr_id"], [])
    exact = [
        row for row in records
        if row["pin"] == link["pin"] and row["state"].strip().casefold() == "himachal pradesh"
    ]
    if not exact or len(exact) != len(records):
        return None, "project_identity_or_pin_mismatch"
    routed = [row for row in exact if row["routing_eligible"]]
    if not routed:
        return None, "project_location_hold"
    return routed[0], None


def forecast_dialysis(hp, facilities, test_metrics):
    observed = {}
    apps = set()
    for row in unpack(hp, "observations"):
        key = row["facility_id"], row["period"]
        if key in observed:
            raise ValueError(f"Repeated observed programme facility-period: {key}")
        observed[key] = row

    estimates, withheld = [], []
    joined_ids = set()
    for app in unpack(hp, "applications"):
        key = app["facility_id"], app["period"]
        if app["period"] != TARGET_PERIOD:
            continue
        if key in apps:
            raise ValueError(f"Repeated programme application: {key}")
        apps.add(key)
        link = hp["facility_links"].get(app["facility_id"])
        site, reason = match_release_site(facilities, link)
        prior = observed.get((app["facility_id"], PRIOR_PERIOD))
        if key in observed or app["total"] is not None:
            reason = "target_already_observed"
        elif not prior or prior["months"] != 12 or type(prior["total"]) is not int or prior["total"] < 0:
            reason = "missing_comparable_prior_year_total"

        if reason:
            withheld.append({"programme_site_id": app["facility_id"], "facility": app["facility"], "reason": reason})
            continue

        facility_id = link["fdr_id"]
        if facility_id in joined_ids:
            raise ValueError(f"Multiple programme rows resolve to {facility_id}")
        joined_ids.add(facility_id)
        point = prior["total"] / prior["months"]
        error = test_metrics["p90_absolute_error"]
        estimates.append({
            "facility_id": facility_id,
            "facility": site["name"],
            "programme_site_id": app["facility_id"],
            "programme_facility": app["facility"],
            "district": app["district"],
            "service": hp["service"],
            "unit": hp["unit"],
            "target_period": TARGET_PERIOD,
            "status": "baseline_estimate",
            "method": "prior_fiscal_year_monthly_average",
            "estimated_typical_month": round(point, 2),
            "historical_error_band": [round(max(0, point - error), 2), round(point + error, 2)],
            "prior_period": PRIOR_PERIOD,
            "prior_reported_total": prior["total"],
            "prior_reported_months": prior["months"],
            "prior_source": hp["sources"][prior["source"]],
            "facility_link": link,
        })
    return estimates, withheld


def run_audit(rows, targets, hp, tn, comparisons):
    facilities, documented, verified, unlinked = index_release(rows, targets)
    dialysis, maternity = hp["service"], tn["service"]
    tests = {cohort["service"]: cohort["evaluation"] for cohort in comparisons["cohorts"]}
    hp_test = tests[dialysis]["later_period_known_facility"]
    tn_test = tests[maternity]["unseen_district"]
    hp_baseline = hp_test["metrics"]["last_observed_or_peer"]
    tn_baseline = tn_test["metrics"]["capacity_scaled_median"]
    if hp_baseline["mae"] >= hp_test["metrics"]["xgboost"]["mae"] or (
        tn_baseline["mae"] >= tn_test["metrics"]["xgboost"]["mae"]
    ):
        raise ValueError("The input comparison no longer supports the recorded baseline choices")

    estimates, withheld = forecast_dialysis(hp, facilities, hp_baseline)
    maternity_observed = [
        row for row in unpack(tn, "observations")
        if row["period"] == TARGET_PERIOD and row["total"] is not None
    ]
    if any(row["period"] == TARGET_PERIOD for row in unpack(tn, "applications")):
        raise ValueError("New maternity applications require branch and CEmONC feature review")

    counts = Counter(row["service"] for row in estimates)
    return {
        "target_period": TARGET_PERIOD,
        "dataset": {
            "source_rows": len(rows),
            "unique_facility_ids": len(facilities),
            "rows_without_facility_id": unlinked,
            "additional_rows_with_repeated_id": len(rows) - unlinked - len(facilities),
            "routing_eligible_unique_facility_ids": sum(
                any(row["routing_eligible"] for row in records) for records in facilities.values()
            ),
        },
        "summary": [
            {
                "care_needed": service,
                "release_documented_sites": len(documented[service]),
                "release_verified_service_sites": len(verified[service]),
                "baseline_fills": counts[service],
                "basis": {
                    dialysis: "Prior programme-year average at the same reviewed branch",
                    maternity: "All sites in the tested CEmONC cohort have observed target totals",
                }.get(service, "No evaluated fiscal-year baseline with supported application rows"),
            }
            for service in targets
        ],
        "validation": {
            dialysis: {
                "test": "Later fiscal year at previously observed programme sites",
                "sites": hp_test["facilities"],
                "method": "last_observed_or_peer",
                **hp_baseline,
                "xgboost_mae": hp_test["metrics"]["xgboost"]["mae"],
            },
            maternity: {
                "test": "District holdout in the Tamil Nadu DMS CEmONC cohort",
                "sites": tn_test["facilities"],
                "method": "capacity_scaled_median",
                **tn_baseline,
                "xgboost_mae": tn_test["metrics"]["xgboost"]["mae"],
                "programme_sites_with_observed_target": len(maternity_observed),
            },
        },
        "programme_dialysis_candidates": len(estimates) + len(withheld),
        "programme_dialysis_candidates_withheld": withheld,
        "unique_project_facilities_with_fills": len({row["facility_id"] for row in estimates}),
        "estimates": estimates,
    }


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def release_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", type=Path, required=True, help="Final facilities_newest_capacity.csv")
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout")
    args = parser.parse_args()
    with args.facilities.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"facility_id", "name", "state", "pin", "routing_eligible",
                    "documented_services", "services_evidence_status"}
        if not required <= set(reader.fieldnames or []):
            parser.error("Use the final capacity export with the documented service evidence columns")
        rows = list(reader)
    result = run_audit(
        rows, read_json("observations.json")["targets"],
        read_json("hp_dialysis_model_data.json"), read_json("tn_maternity_model_data.json"),
        read_json("xgboost_results.json"),
    )
    result["dataset"]["source_file"] = args.facilities.name
    result["dataset"]["sha256"] = release_sha256(args.facilities)
    content = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
