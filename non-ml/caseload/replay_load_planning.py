"""Replay the planning selector with reviewed Himachal Pradesh dialysis evidence."""

import argparse
import csv
from itertools import combinations
import json
from pathlib import Path
import sys

import pandas as pd

from baseline_coverage import read_json, release_sha256


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from facility_tool.recommend import compare_modes, haversine_km  # noqa: E402


SERVICE = "dialysis"
AS_OF = "2026-11-22"
MAX_RADIUS_KM = 50
MAX_EXTRA_KM = 10
TRAVEL_WEIGHT = 0.5


def read_sites(path, pressure, links):
    links_by_id = {link["fdr_id"]: (programme_id, link) for programme_id, link in links.items()}
    pressure_by_id = {row["facility_id"]: row for row in pressure}
    if not pressure_by_id or len(pressure_by_id) != len(pressure) or set(pressure_by_id) - links_by_id.keys():
        raise ValueError("Use one planning row per reviewed PMNDP facility.")

    sites = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            facility_id = row["facility_id"]
            if facility_id not in pressure_by_id:
                continue
            programme_id, link = links_by_id[facility_id]
            if (facility_id in sites or row["pin"] != link["pin"] or row["state"] != "Himachal Pradesh"
                    or row["routing_eligible"].casefold() != "true"):
                raise ValueError(f"Ambiguous or ineligible release branch for {facility_id}.")
            sites[facility_id] = {
                "facility_id": facility_id, "name": row["name"], "address": row["address"],
                "city": row["city"], "district": row["district"], "state": row["state"], "pin": row["pin"],
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "location_quality": "release_routing_eligible", "location_routing_eligible": True,
                "location_hold_reason": "", "location_source_urls": [link["source_url"]],
                "programme_id": programme_id,
            }
    if sites.keys() != pressure_by_id.keys():
        raise ValueError("Some planning sites are missing from the reviewed facility release.")
    return [sites[ident] for ident in sorted(sites)]


def planning_origins(sites):
    origins = [
        {"kind": "facility_coordinates", "name": site["name"], "facility_id": site["facility_id"],
         "lat": site["lat"], "lon": site["lon"]}
        for site in sites
    ]
    if len(sites) > 1:
        first, second = min(combinations(sites, 2), key=lambda pair: haversine_km(
            pair[0]["lat"], pair[0]["lon"], pair[1]["lat"], pair[1]["lon"],
        ))
        origins.append({
            "kind": "hypothetical_midpoint", "name": "Coordinate midpoint of closest facility pair",
            "facility_ids": [first["facility_id"], second["facility_id"]],
            "lat": (first["lat"] + second["lat"]) / 2,
            "lon": (first["lon"] + second["lon"]) / 2,
        })
    return origins


def describe_branch(row):
    return {"facility_id": row["facility_id"], "name": row["name"],
            "straight_line_km": round(float(row["distance_km"]), 6)}


def replay(sites, pressure):
    if len({(row["service"], row["period"], row["unit"], row["basis"]) for row in pressure}) != 1:
        raise ValueError("Use a single comparable service, period, unit and basis for this replay.")
    if any(row["service"] != SERVICE for row in pressure):
        raise ValueError("This replay uses the Himachal Pradesh PMNDP dialysis cohort.")

    entities = pd.DataFrame(sites)
    pressure_inputs = pd.DataFrame(pressure)
    sites_by_id = {site["facility_id"]: site for site in sites}
    claims = pd.DataFrame([
        {
            "facility_id": row["facility_id"], "service": SERVICE, "stance": "supports",
            "record_id": sites_by_id[row["facility_id"]]["programme_id"] + ":" + row["period"],
            "excerpt": f"{row['demand']} completed programme haemodialysis sessions in {row['period']}.",
            "source_url": row["source_url"], "source": "Himachal Pradesh State Health Society",
            "scope": "PMNDP haemodialysis", "validity": "unknown", "citation_quality": "direct",
        }
        for row in pressure
    ])

    cases = []
    for origin in planning_origins(sites):
        settings = dict(as_of=AS_OF, max_radius_km=MAX_RADIUS_KM,
                        max_extra_km=MAX_EXTRA_KM, travel_weight=TRAVEL_WEIGHT)
        args = entities, claims, origin["lat"], origin["lon"], SERVICE
        nearest = compare_modes(*args, **settings)
        planning = compare_modes(*args, **settings, spread_demand=True, load_context="planning",
                                 pressure_inputs=pressure_inputs)
        nearest_branch = nearest["rows"].query("mode == 'confirmed' and kind == 'recommendation' and rank == 1").iloc[0]
        planning_branch = planning["rows"].query("mode == 'confirmed' and kind == 'recommendation' and rank == 1").iloc[0]
        scored = planning["trace"].loc[planning["trace"]["mode"].eq("confirmed")]
        comparisons = [
            {
                **describe_branch(row), "completed_sessions": float(row["pressure_demand"]),
                "predicted_throughput": float(row["pressure_capacity"]),
                "capacity_predicted": bool(row["pressure_capacity_predicted"]),
                "throughput_ratio": round(float(row["pressure_ratio"]), 6),
                "planning_score": round(float(row["weighted_score"]), 6),
            }
            for row in scored.loc[scored["weighted_score"].notna()].to_dict("records")
        ]
        cases.append({
            "origin": origin,
            "nearest_eligible": describe_branch(nearest_branch),
            "planning_selection": describe_branch(planning_branch),
            "added_straight_line_km": round(float(planning_branch["distance_km"] - nearest_branch["distance_km"]), 6),
            "load_status": planning_branch["load_status"],
            "comparable_planning_sites": comparisons,
        })
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", type=Path, required=True, help="Reviewed facilities_newest_capacity.csv")
    parser.add_argument("--output", type=Path, help="Write the receipt to this path instead of stdout")
    args = parser.parse_args()
    digest = release_sha256(args.facilities)
    if digest != read_json("baseline_coverage.json")["dataset"]["sha256"]:
        parser.error("Use the exact reviewed facility release recorded in baseline_coverage.json.")

    pressure = read_json("load_evidence.json")["pressure_inputs"]
    sites = read_sites(args.facilities, pressure, read_json("hp_dialysis_model_data.json")["facility_links"])
    cases = replay(sites, pressure)
    result = {
        "purpose": "Offline planning replay with observed historical activity and predicted throughput",
        "facility_source": {"file": args.facilities.name, "sha256": digest},
        "pressure_source": "load_evidence.json",
        "pressure_period": pressure[0]["period"], "pressure_basis": pressure[0]["basis"],
        "scope": "Himachal Pradesh PMNDP haemodialysis",
        "settings": {"as_of": AS_OF, "load_context": "planning", "distance_metric": "straight_line_km",
                     "max_radius_km": MAX_RADIUS_KM, "max_extra_km": MAX_EXTRA_KM, "travel_weight": TRAVEL_WEIGHT},
        "summary": {
            "reviewed_branches": len(sites), "cases": len(cases),
            "cases_with_comparable_planning_sites": sum(bool(case["comparable_planning_sites"]) for case in cases),
            "cases_with_changed_first_facility": sum(
                case["nearest_eligible"]["facility_id"] != case["planning_selection"]["facility_id"] for case in cases
            ),
        },
        "cases": cases,
    }
    content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
