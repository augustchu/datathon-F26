"""Build one historical planning estimate per reviewed release row and Care needed option."""

import argparse
from collections import Counter
import csv
import gzip
from hashlib import sha256
from io import TextIOWrapper
import json
from math import ceil, isfinite
from pathlib import Path
import re
from statistics import median
from tempfile import NamedTemporaryFile

from baseline_coverage import ROOT, read_json, release_sha256, unpack
from build import month_index
from expand_coverage import GRAINS
from network_model import load_profiles


COLUMNS = (
    "facility_id", "service", "care_needed", "unit", "scope", "period", "caseload", "capacity",
    "caseload_predicted", "capacity_predicted", "caseload_method", "capacity_method", "source_url",
    "capacity_source_url", "service_evidence", "routing_eligible", "location_state", "reference_start",
    "reference_end", "caseload_peer_group", "caseload_peer_count", "capacity_peer_group", "capacity_peer_count",
    "peer_min_caseload", "peer_max_caseload", "capacity_excludes_target_site", "capacity_reference_period",
    "release_sha256",
)
BRANCH_FIELDS = ("state", "pin", "routing_eligible", "documented_services", "services_evidence_status")
MIN_STATE_PEERS = 3
AS_OF_LABEL = "Historical typical month through"


def service_key(value):
    if value == "Trauma / emergency":
        return "trauma_care"
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def source_url(source):
    return source if isinstance(source, str) else source["url"]


def load_branches(release_path):
    """Keep the reviewed IDs and preserve unlinked source rows for scenario inventory."""
    branches = {}
    unlinked = []
    rows = 0
    with release_path.open(encoding="utf-8-sig", newline="") as stream:
        for index, raw in enumerate(csv.DictReader(stream), start=1):
            rows += 1
            row = {key: raw[key].strip() for key in BRANCH_FIELDS}
            ident = raw["facility_id"].strip()
            row["routing_eligible"] = bool(ident and row["routing_eligible"].casefold() == "true")
            if not ident:
                unlinked.append((f"release-row:{index}", row))
                continue
            prior = branches.setdefault(ident, row)
            if prior != row:
                raise ValueError(f"Duplicate branch disagrees on planning metadata: {ident}")
    return sorted(branches.items()) + unlinked, rows, len(unlinked)


def select_pool(benchmarks, state):
    regional = [row for row in benchmarks if state and row["region"].strip().casefold() == state.casefold()]
    if len(regional) >= MIN_STATE_PEERS:
        return regional, f"State peers: {state}"
    return benchmarks, "All comparable reporting sites"


def peer_stats(benchmarks):
    ordered = sorted(benchmarks, key=lambda row: (row["typical_month"], row["facility"]))
    values = [row["typical_month"] for row in ordered]
    if not values or any(type(value) not in (int, float) or not isfinite(value) or value < 0 for value in values):
        raise ValueError("Peer benchmarks must be finite nonnegative historical amounts")
    tail = ordered[ceil(0.9 * len(ordered)) - 1]
    # Keep positive throughput even when several verified historic counts were zero.
    positive = tail if tail["typical_month"] > 0 else ordered[-1]
    if positive["typical_month"] <= 0:
        raise ValueError("A throughput scenario needs at least one positive observed benchmark")
    middle = ordered[(len(ordered) - 1) // 2]
    return {
        "count": len(values), "median": median(values), "median_source_url": source_url(middle["source"]),
        "throughput": positive["typical_month"], "throughput_source_url": source_url(positive["source"]),
        "min": values[0], "max": values[-1],
    }


def dated_predictions(profile, replay_rows, service):
    """Use published observations or earlier audited same-site projections of this same metric."""
    choices = {}
    for row in profile["benchmark_rows"]:
        if row.get("facility_id"):
            value = {
                "caseload": row["typical_month"], "caseload_predicted": False,
                "caseload_method": "reported_site_typical_month", "source_url": source_url(row["source"]),
                "reference_start": row["period_start"], "reference_end": row["period_end"],
            }
            key = row["facility_id"]
            if key not in choices or value["reference_end"] > choices[key]["reference_end"]:
                choices[key] = value
    allowed = set(profile.get("replay_series_ids", ()))
    for row in replay_rows:
        ident = row["facility_id"]
        if not row["caseload_predicted"] or row["service"] != service or row["series_id"] not in allowed:
            continue
        current = choices.get(ident, {})
        if current.get("reference_end", "") >= row["period_end"]:
            continue
        choices[ident] = {
            "caseload": row["caseload"] / GRAINS[row["grain"]][0], "caseload_predicted": True,
            "caseload_method": f"backtested_same_site_{row['caseload_method']}",
            "source_url": source_url(row["caseload_history"][-1]["source"]),
            "reference_start": row["period_start"], "reference_end": row["period_end"],
        }
    return choices


def evidence_basis(branch, service, has_site_history):
    if has_site_history:
        return "reported_activity_in_scope"
    documented = {entry.strip().casefold() for entry in branch["documented_services"].split("|")}
    if service in documented:
        if branch["services_evidence_status"].casefold().startswith("verified_"):
            return "verified_service_broad_scope"
        return "service_listed_unverified"
    return "service_unknown_conditional_only"


def programme_capacity(evidence, maternity):
    """Recover resource-informed planning throughput where the exact programme link exists."""
    overrides = {}
    audit = {row["facility_id"]: row for row in evidence["dialysis_capacity_model"]["rows"] if row["facility_id"]}
    for row in evidence["pressure_inputs"]:
        if row["service"] != "dialysis" or row["capacity_predicted"] is not True:
            raise ValueError("Review the dialysis programme throughput source")
        overrides["dialysis", row["facility_id"]] = {
            "capacity": row["capacity"], "capacity_method": row["capacity_method"],
            "capacity_source_url": row["capacity_resource_source"]["url"], "capacity_reference_period": row["period"],
            "capacity_peer_group": "Himachal Pradesh PMNDP March 2024 functional-machine peers",
            "capacity_peer_count": audit[row["facility_id"]]["training_site_count"],
        }
    linked = evidence["historical_intensity"]["tn_cemonc"]["rows"]
    programme = {row["facility_id"]: row for row in unpack(maternity, "observations")}
    for item in linked:
        if not item["facility_id"]:
            continue
        site = programme[item["programme_site_id"]]
        peers = sorted(row["total"] / row["months"] / row["beds"] for row in programme.values()
                       if row["facility_id"] != site["facility_id"] and row["facility_type"] == site["facility_type"]
                       and row["period"] == site["period"])
        if len(peers) < MIN_STATE_PEERS:
            continue
        amount = site["beds"] * peers[ceil(0.9 * len(peers)) - 1]
        overrides["maternity_delivery", item["facility_id"]] = {
            "capacity": amount, "capacity_method": "same_type_leave_site_out_p90_deliveries_per_cemonc_bed",
            "capacity_source_url": maternity["sources"][site["source"]]["url"],
            "capacity_reference_period": site["period"],
            "capacity_peer_group": f"Tamil Nadu DMS CEmONC {site['facility_type']} peers",
            "capacity_peer_count": len(peers),
        }
    return overrides


def estimates(branches, profiles, replay, as_of, evidence_sources, capacity_overrides=None):
    benchmark_cache = {}
    capacity_overrides = capacity_overrides or {}
    for care, profile in profiles.items():
        key = service_key(care)
        observations = profile["benchmark_rows"]
        known = dated_predictions(profile, replay["caseloads"], care)
        if care == "Dialysis":
            for row in replay["dialysis_prior_replay"]["separate_caseload_estimates"]:
                ident = row["facility_id"]
                if known.get(ident, {}).get("reference_end", "") < row["period_end"]:
                    prior = row["prior_periods"][-1]
                    known[ident] = {
                        "caseload": row["typical_month"], "caseload_predicted": True,
                        "caseload_method": "backtested_same_site_prior_fiscal_year_average",
                        "source_url": source_url(evidence_sources[prior["source_id"]]), "reference_start": row["period_start"],
                        "reference_end": row["period_end"],
                    }
        for ident, branch in branches:
            history = known.get(ident)
            state = branch["state"]
            cache_key = (care, state.casefold(), ident if history else "")
            if cache_key not in benchmark_cache:
                peers = [row for row in observations if row.get("facility_id") != ident]
                excludes_target = bool(peers)
                pool, name = select_pool(peers or observations, state)
                benchmark_cache[cache_key] = (peer_stats(pool), name, excludes_target)
            stats, group, excludes_target = benchmark_cache[cache_key]
            workload = history or {
                "caseload": stats["median"], "caseload_predicted": True,
                "caseload_method": "conditional_service_peer_median", "source_url": stats["median_source_url"],
                "reference_start": "", "reference_end": "",
            }
            yield {
                **workload, "facility_id": ident, "service": key, "care_needed": care, "unit": profile["unit"],
                "scope": profile["scope"], "period": f"{AS_OF_LABEL} {as_of}", "capacity": stats["throughput"],
                "capacity_predicted": True, "capacity_method": "historical_peer_p90_throughput_scenario",
                "capacity_source_url": stats["throughput_source_url"], "location_state": state,
                "routing_eligible": branch["routing_eligible"], "service_evidence": evidence_basis(branch, key, bool(history)),
                "caseload_peer_group": group if not history else "", "caseload_peer_count": stats["count"] if not history else 0,
                "capacity_peer_group": group, "capacity_peer_count": stats["count"],
                "peer_min_caseload": stats["min"], "peer_max_caseload": stats["max"],
                "capacity_excludes_target_site": excludes_target, "capacity_reference_period": "",
                **capacity_overrides.get((key, ident), {}),
            }


def write_estimates(path, rows, release_digest):
    counts = {}
    total = 0
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            with gzip.GzipFile(filename="", mode="wb", fileobj=temp, mtime=0) as zipped:
                with TextIOWrapper(zipped, encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
                    writer.writeheader()
                    for row in rows:
                        for key in ("caseload", "capacity"):
                            value = row[key]
                            if not isfinite(value) or value < 0 or (key == "capacity" and value == 0):
                                raise ValueError(f"Invalid {key} for {row['facility_id']} {row['service']}")
                            row[key] = f"{value:.6f}"
                        writer.writerow({**row, "release_sha256": release_digest})
                        total += 1
                        tally = counts.setdefault(row["care_needed"], Counter())
                        tally["rows"] += 1
                        tally["same_site_predicted"] += row["caseload_method"].startswith("backtested_same_site")
                        tally["conditional_peer_predicted"] += row["caseload_method"] == "conditional_service_peer_median"
                        tally["reported_site"] += not row["caseload_predicted"]
                        tally[row["service_evidence"]] += 1
                        tally["predicted_capacity"] += row["capacity_predicted"]
                        tally["programme_resource_capacity"] += bool(row["capacity_reference_period"])
                        tally["routing_eligible_rows"] += row["routing_eligible"]
            temp.flush()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
    return total, counts


def build_network(release_path, output):
    audited = read_json("baseline_coverage.json")["dataset"]
    digest = release_sha256(release_path)
    if digest != audited["sha256"]:
        raise ValueError("Use the exact reviewed facilities_newest_capacity.csv from baseline_coverage.json")
    branches, raw_rows, unlinked = load_branches(release_path)
    profile = load_profiles(ROOT)
    taxonomy = read_json("observations.json")
    if set(profile) != set(taxonomy["targets"]) or len({service_key(key) for key in profile}) != len(profile):
        raise ValueError("Model profiles do not exactly match the Care needed options")
    month_index(taxonomy["as_of_month"])
    replay = read_json("care_options_replay.json")
    evidence = read_json("load_evidence.json")
    overrides = programme_capacity(evidence, read_json("tn_maternity_model_data.json"))
    total, counts = write_estimates(output, estimates(branches, profile, replay, taxonomy["as_of_month"],
                                                      evidence["sources"], overrides), digest)
    if total != len(branches) * len(profile):
        raise RuntimeError("The estimate matrix is incomplete")
    return {
        "scope": "Historical typical-month service planning scenarios",
        "release": audited, "as_of_month": taxonomy["as_of_month"],
        "raw_rows": raw_rows, "distinct_linked_facilities": len(branches) - unlinked,
        "unlinked_rows_with_scenario_only": unlinked,
        "routine_routing_eligible_facilities": sum(row["routing_eligible"] for _, row in branches),
        "care_options": len(profile), "estimate_rows": total, "live_availability_rows": 0,
        "forecast_file": output.name, "forecast_sha256": release_sha256(output),
        "usage_counts": counts, "profiles": profile,
        "inputs": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            "observations.json", "typical_caseload.json", "care_options_replay.json", "hp_dialysis_model_data.json",
            "tn_maternity_model_data.json", "baseline_coverage.json", "load_evidence.json", "care_replay_odisha_mri.json",
            *(str(path.relative_to(ROOT)) for path in sorted((ROOT / "expansion").glob("*.json"))),
        )},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Destination for the gzip-compressed CSV")
    parser.add_argument("--receipt", type=Path, required=True, help="Destination for coverage and model evidence JSON")
    args = parser.parse_args()
    if args.output.suffix != ".gz" or len({p.resolve() for p in (args.output, args.receipt, args.facilities)}) != 3:
        parser.error("Provide distinct output and receipt files, with .gz for the CSV.")
    receipt = build_network(args.facilities, args.output)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
