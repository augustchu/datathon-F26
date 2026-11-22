"""Replay first-result changes across the exact service matches in the facility release."""

import argparse
from collections import Counter
import csv
import gzip
from itertools import combinations
import json
from pathlib import Path

import pandas as pd

from baseline_coverage import read_json, release_sha256
from build_network_caseload import service_key
from plan_network_load import (
    MAX_EXTRA_KM, MAX_EXTRA_MINUTES, MAX_RADIUS_KM, REPORTED_IN_SCOPE, TRAVEL_WEIGHT, read_sites,
)
from replay_load_planning import AS_OF
from facility_tool.network_load import read_network_pressure, replay_network_load
from facility_tool.recommend import (
    compare_prepared_modes, haversine_km, prepare_service_candidates, routine_load_allowed,
)


def reported_matches(path, digest, taxonomy):
    matched = {key: {} for key in taxonomy}
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"facility_id", "service", "care_needed", "service_evidence", "release_sha256"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("Use the generated network forecast CSV.gz.")
        for row in reader:
            service, ident = row["service"], row["facility_id"]
            if row["release_sha256"] != digest or taxonomy.get(service) != row["care_needed"]:
                raise ValueError("The forecast release or service taxonomy does not match the replay.")
            if row["service_evidence"] != REPORTED_IN_SCOPE:
                continue
            if not ident or ident in matched[service]:
                raise ValueError(f"Missing or repeated matched branch for {service}: {ident!r}.")
            matched[service][ident] = row
    return matched


def test_origins(sites, examples):
    """Use branch coordinates, near-midpoint probes and the earlier published examples."""
    origins = {}

    def add(origin):
        origin["lat"] = round(origin["lat"], 8)
        origin["lon"] = round(origin["lon"], 8)
        point = origin["lat"], origin["lon"]
        origins.setdefault(point, origin)

    for site in sites:
        add(dict(kind="facility_coordinates", name=site["name"], facility_id=site["facility_id"],
                 lat=site["lat"], lon=site["lon"]))
    for a, b in combinations(sites, 2):
        separation = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        if not 0 < separation <= 98:
            continue
        for fraction in (0.49, 0.51):
            lat = a["lat"] + fraction * (b["lat"] - a["lat"])
            lon = a["lon"] + fraction * (b["lon"] - a["lon"])
            if max(haversine_km(lat, lon, site["lat"], site["lon"]) for site in (a, b)) <= MAX_RADIUS_KM:
                add(dict(kind="hypothetical_pair_probe", name=f"{fraction:.0%} from {a['name']} to {b['name']}",
                         facility_ids=[a["facility_id"], b["facility_id"]], lat=lat, lon=lon))
    for example in examples:
        add(dict(kind="previous_hypothetical_example", name="Previously published scenario origin", **example))
    return list(origins.values())


def summarize_site(site, inputs):
    ident = site.facility_id
    row = inputs[ident]
    return {
        "facility_id": ident, "name": site.name, "distance_km": round(float(site.distance_km), 6),
        "baseline_rank": int(site.baseline_rank), "planning_rank": int(site.rank),
        "weighted_score": None if pd.isna(site.weighted_score) else float(site.weighted_score),
        **{key: row[key] for key in (
            "demand", "capacity", "unit", "caseload_predicted", "capacity_predicted", "caseload_method",
            "capacity_method", "reference_start", "reference_end", "capacity_reference_period",
            "source_url", "capacity_source_url",
        )},
    }


def replay_case(prepared, pressure, inputs, service, origin):
    routine = routine_load_allowed(service)
    result = compare_prepared_modes(
        prepared, origin["lat"], origin["lon"], service, as_of=AS_OF, route_sort="distance",
        max_radius_km=MAX_RADIUS_KM, max_extra_km=MAX_EXTRA_KM, max_extra_minutes=MAX_EXTRA_MINUTES,
        travel_weight=TRAVEL_WEIGHT, load_context="planning", spread_demand=routine, pressure_inputs=pressure,
    )
    eligible = result["trace"].query("mode == 'confirmed' and decision == 'eligible'").sort_values("baseline_rank")
    choices = [summarize_site(site, inputs) for site in eligible.itertuples()]
    before = choices[0] if choices else None
    compared = bool(eligible["weighted_score"].notna().any())
    after = min(choices, key=lambda row: row["planning_rank"]) if compared else None
    changed = bool(after and after["facility_id"] != before["facility_id"])
    first_suggestion = None
    if compared:
        replay = replay_network_load(eligible, pressure, requests=1, max_extra_km=MAX_EXTRA_KM,
                                     max_extra_minutes=MAX_EXTRA_MINUTES, travel_weight=TRAVEL_WEIGHT)
        first_suggestion = replay.loc[replay["balanced_suggestions"].eq(1), "facility_id"].item()
    if not routine:
        status = "time_critical"
    elif not choices:
        status = "no_matched_branch_within_radius"
    elif len(choices) == 1:
        status = "one_matched_branch_within_radius"
    else:
        status = result["summary"].iloc[0]["weighted_status"]
    return {
        "origin": origin, "status": status, "compared": compared, "top_changed": changed,
        "nearest_top": before, "planning_top": after, "first_incremental_suggestion_id": first_suggestion,
        "eligible_branches": choices,
    }


def run_service(service, care, matches, sites, forecasts, examples):
    routable_ids = sorted(matches.keys() & sites.keys())
    cases = []
    if routable_ids:
        entities = [
            {**sites[ident], "lat": float(sites[ident]["latitude"]), "lon": float(sites[ident]["longitude"]),
             "address": "", "district": "", "location_quality": "release_routing_eligible",
             "location_routing_eligible": True, "location_hold_reason": ""}
            for ident in routable_ids
        ]
        pressure = read_network_pressure(forecasts, service, eligible_ids=routable_ids)
        inputs = pressure.set_index("facility_id").to_dict("index")
        if inputs.keys() != set(routable_ids):
            raise ValueError(f"Invalid or ambiguous monthly pressure for matched {service} branches.")
        claims = pd.DataFrame([
            dict(facility_id=ident, record_id=f"historical:{ident}:{service}", service=service, stance="supports",
                 excerpt=matches[ident]["scope"], source_url=inputs[ident]["source_url"], source="Historical activity report",
                 scope=matches[ident]["scope"], validity="unknown", citation_quality="direct")
            for ident in routable_ids
        ])
        prepared = prepare_service_candidates(pd.DataFrame(entities), claims, service, as_of=AS_OF)
        cases = [replay_case(prepared, pressure, inputs, service, origin)
                 for origin in test_origins(entities, examples)]

    compared = sum(case["compared"] for case in cases)
    changes = sum(case["top_changed"] for case in cases)
    if not routine_load_allowed(service):
        status = "time_critical"
    elif not routable_ids:
        status = "no_matched_routable_branch"
    elif len(routable_ids) == 1:
        status = "one_matched_routable_branch"
    elif not compared:
        status = "no_comparable_local_alternative"
    else:
        status = "top_result_changed" if changes else "compared_without_change"
    return {
        "care_needed": care, "service": service, "status": status,
        "exact_matches": len(matches), "routable_matches": len(routable_ids),
        "origins": len(cases), "origins_compared": compared, "origins_with_top_change": changes,
        "origin_statuses": dict(sorted(Counter(case["status"] for case in cases).items())),
        "cases": cases,
    }


def run(facilities, forecasts):
    digest = release_sha256(facilities)
    if digest != read_json("baseline_coverage.json")["dataset"]["sha256"]:
        raise ValueError("Use the exact reviewed facility release recorded in baseline_coverage.json.")
    forecast_digest = release_sha256(forecasts)
    if forecast_digest != read_json("network_caseload_receipt.json")["forecast_sha256"]:
        raise ValueError("Use the verified forecast recorded in network_caseload_receipt.json.")
    taxonomy = {service_key(care): care for care in read_json("observations.json")["targets"]}
    matched = reported_matches(forecasts, digest, taxonomy)
    sites = read_sites(facilities)
    examples = read_json("network_planning_examples.json")["examples"]
    if any(example["forecast_source"]["sha256"] != forecast_digest for example in examples):
        raise ValueError("The previous scenario receipts identify a different forecast.")
    services = [
        run_service(service, care, matched[service], sites, forecasts,
                    [example["origin"] for example in examples if example["service"] == service])
        for service, care in taxonomy.items()
    ]
    summary = {key: sum(group[key] for group in services) for key in (
        "exact_matches", "routable_matches", "origins", "origins_compared", "origins_with_top_change",
    )}
    summary.update(care_options=len(services), care_options_compared=sum(bool(g["origins_compared"]) for g in services),
                   care_options_with_top_change=sum(bool(g["origins_with_top_change"]) for g in services))
    return {
        "purpose": "Historical typical-month planning replay of the first ranked result",
        "facility_release_sha256": digest, "forecast_sha256": forecast_digest, "evidence_as_of": AS_OF,
        "settings": {"service_evidence": REPORTED_IN_SCOPE, "assumed_facilities": False, "travel": "straight_line_km",
                     "max_radius_km": MAX_RADIUS_KM, "max_extra_km": MAX_EXTRA_KM, "travel_weight": TRAVEL_WEIGHT},
        "summary": summary, "services": services,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", type=Path, required=True)
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.facilities.resolve(), args.forecasts.resolve()}:
        parser.error("Use a result path separate from the input files.")
    receipt = run(args.facilities, args.forecasts)
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
