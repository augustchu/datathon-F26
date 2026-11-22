"""Align sourced caseloads and replay comparable historical planning rankings."""

import argparse
from collections import Counter, defaultdict
import csv
from itertools import combinations
import json
from math import isfinite
from pathlib import Path

import pandas as pd

from baseline_coverage import ROOT, read_json, release_sha256
from build import month_index
from expand_coverage import GRAINS, assess_target, check_link, read_series
from replay_load_planning import AS_OF, MAX_EXTRA_KM, MAX_RADIUS_KM, TRAVEL_WEIGHT, planning_origins
from facility_tool.recommend import compare_modes, haversine_km, routine_load_allowed


# Only group series with the same activity definition. Grain and dates must still match.
COHORTS = {
    "EHA hospital deliveries": (
        "eha_chhatarpur_deliveries", "eha_herbertpur_deliveries", "eha_kachhwa_deliveries",
    ),
    "Tamil Nadu DMS taluk CEmONC deliveries": ("govt_lawley_deliveries", "pattukkottai_deliveries"),
    "Tamil Nadu DME hospital deliveries": ("raja_mirasdar_deliveries", "chengalpattu_medical_college_deliveries"),
    "Hospital radiology MRI examinations": (
        "whitefield_mri_annual", "unmehta_mri_monthly", "aiims_bhubaneswar_mri_annual", "sgrd_mri",
        "zydus_dahod_mri", "himalayan_expanded_mri", "scb_mri_annual",
    ),
    "Hospital radiology CT examinations with unspecified angiography split": (
        "whitefield_ct_annual", "unmehta_ct_monthly", "sgrd_ct", "zydus_dahod_ct", "himalayan_expanded_ct",
    ),
    "General paediatric outpatient visits": ("sgrd_pediatric", "himalayan_expanded_pediatric", "lok_nayak_pediatrics"),
    "Major orthopedic operations": ("sgrd_ortho", "himalayan_expanded_ortho"),
    "All major and minor oral and maxillofacial operations": ("its_muradnagar_oral", "vishnu_dental_oral_surgery"),
    "All-cause hospital casualty attendance": (
        "kem_mumbai_casualty", "sanjay_gandhi_memorial_casualty", "lok_nayak_casualty",
    ),
}
COHORT_BY_SERIES = {series_id: name for name, members in COHORTS.items() for series_id in members}
# These source columns have different labels for the same counted event.
COHORT_UNITS = {
    "All major and minor oral and maxillofacial operations": "operations",
    "All-cause hospital casualty attendance": "visits",
}


def read_release(path):
    release = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            release[row["facility_id"]].append(row)
    return release


def caseload_record(target, cohort, aligned):
    site = target["site"]
    data = {
        "facility_id": site["facility_id"], "facility": site["facility"], "site": site,
        "service": target["service"], "series_id": target["series_id"],
        "cohort": cohort, "scope": target["scope"], "grain": target["grain"],
        "period_start": target["period_start"], "period_end": target["period_end"],
        "unit": COHORT_UNITS.get(cohort, target["unit"]), "source_unit": target["unit"],
        "aligned_for_replay": aligned, "status": target["status"],
        "caseload": None, "caseload_predicted": None, "capacity": None, "capacity_predicted": None,
    }
    if target["status"] == "reported":
        data.update(caseload=target["reported_count"], caseload_predicted=False, caseload_source=target["source"])
    elif target["status"] == "baseline_estimate":
        backtest = target["backtest"]
        data.update(
            caseload=round(target["typical_month"] * GRAINS[target["grain"]][0], 2),
            caseload_predicted=True, typical_month=target["typical_month"],
            caseload_method=target["method"], forecast_horizon_periods=target["forecast_horizon_periods"],
            gap_kind=target["gap_kind"], gap_source=target.get("gap_source"),
            caseload_history=target["prior_periods"],
            backtest={key: backtest[key] for key in ("scored_periods", "method_mae")},
        )
    else:
        data["reason"] = target["reason"]
    return data


def aligned_caseloads(manifests, release, as_of):
    indexed, periods = [], defaultdict(set)
    for manifest in manifests:
        for series in manifest["series"]:
            cohort = COHORT_BY_SERIES.get(series["id"], series["id"])
            group = (series["service"], cohort, series["grain"])
            targets = {target["period"]: target for target in series["targets"]}
            periods[group].update(targets)
            indexed.append((series, manifest, group, targets))

    rows = []
    for series, manifest, group, targets in indexed:
        sources = manifest["sources"]
        observed = read_series(series, sources)
        site = manifest["sites"][series["site"]]
        for period in sorted(periods[group]):
            target = targets.get(period, {"period": period, "basis": "Aligned to a comparable series' reviewed target"})
            assessment = assess_target(series, site, observed, sources, target, month_index(as_of), release)
            rows.append(caseload_record(assessment, group[1], aligned=period not in targets))

        # Twelve site-reported calendar months are the same grain as the Delhi CY casualty tables.
        if series["id"] == "lok_nayak_casualty":
            start = month_index("2025-01")
            months = [observed.get(start + month) for month in range(12)]
            if all(months) and not check_link(site, release):
                if len({item[1] for item in months}) != 1:
                    raise ValueError("Review the scope before aggregating revised casualty sources")
                total = sum(item[0] for item in months)
                annual = dict(series_id=series["id"], site=site, service=series["service"], scope=series["scope"],
                              unit="visits", grain="calendar_year", period_start="2025-01", period_end="2025-12",
                              status="reported", reported_count=total, source=sources[months[0][1]])
                rows.append(caseload_record(annual, group[1], aligned=True))
    return rows


def attach_maternity_inventory(rows, report):
    index = {(row["facility_id"], row["period_start"], row["period_end"]): row for row in rows
             if row["cohort"] == "Tamil Nadu DMS taluk CEmONC deliveries"}
    for record in report["rows"]:
        row = index[record["facility_id"], record["period_start"], record["period_end"]]
        if (row["caseload_predicted"] is not False or row["caseload"] != record["reported_deliveries"]
                or row["site"]["release_pin"] != record["release_pin"] or row["site"]["state"] != record["state"]):
            raise ValueError(f"Review the CEmONC capacity match for {record['facility']}")
        if record["beds"] <= 0 or record["occupancy_pct"] < 0:
            raise ValueError(f"Invalid reported CEmONC measurement for {record['facility']}")
        row.update(
            capacity=record["beds"], capacity_predicted=False, capacity_unit="reported CEmONC beds",
            occupancy_pct=record["occupancy_pct"], capacity_source={**report["source"], "row_locator": record["locator"]},
        )


def attach_throughput_capacity(rows, capacities):
    """Join a reviewed capacity export in the exact units of the caseload."""
    fields = ("facility_id", "series_id", "period_start", "period_end")
    index = {tuple(row[key] for key in fields): row for row in rows if row["caseload"] is not None}
    for record in capacities:
        key = tuple(record[field] for field in fields)
        row = index.get(key)
        value = record["capacity"]
        predicted = record["capacity_predicted"]
        source = record["capacity_source"]
        if (row is None or row["capacity"] is not None or record["unit"] != row["unit"]
                or type(value) not in (int, float) or not isfinite(value) or value <= 0
                or type(predicted) is not bool or not all(source.get(k) for k in ("title", "url", "locator"))
                or (predicted and not record.get("capacity_method"))):
            raise ValueError(f"Capacity row must match an unused site, series, period and unit with valid provenance: {key}")
        row.update(capacity=value, capacity_predicted=predicted, capacity_unit=record["unit"],
                   capacity_source=source, capacity_method=record.get("capacity_method"))


def replay_sites(rows, release):
    sites = []
    seen = set()
    for row in rows:
        ident = row["facility_id"]
        records = release[ident]
        routable = [r for r in records if r["routing_eligible"].strip().casefold() == "true"]
        if ident in seen or not routable:
            raise ValueError(f"Duplicate or unsupported branch in replay {ident}")
        seen.add(ident)
        points = {(float(r["latitude"]), float(r["longitude"])) for r in routable}
        if len(points) != 1:
            raise ValueError(f"Review contradictory release coordinates for {ident}")
        lat, lon = points.pop()
        source_row = routable[0]
        sites.append({
            **{field: source_row[field] for field in ("facility_id", "address", "city", "district", "state", "pin")},
            "name": row["facility"], "lat": lat, "lon": lon, "location_quality": "release_routing_eligible",
            "location_routing_eligible": True, "location_hold_reason": "",
            "location_source_urls": [row["site"]["identity_url"]],
        })
    return sorted(sites, key=lambda site: site["facility_id"])


def closest_pair(sites):
    return min(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in combinations(sites, 2))


def run_order(rows, sites, origin, service, cohort):
    by_id = {row["facility_id"]: row for row in rows}
    claims, pressure = [], []
    for row in rows:
        ident = row["facility_id"]
        source = row.get("caseload_source") or row["caseload_history"][-1]["source"]
        claims.append({
            "facility_id": ident, "record_id": row["series_id"], "service": service, "stance": "supports",
            "excerpt": row["scope"], "source_url": source["url"], "source": source["title"],
            "scope": cohort, "validity": "unknown", "citation_quality": "direct",
        })
        if row["capacity"] is not None:
            occupancy = row.get("occupancy_pct")
            pressure.append({
                "facility_id": ident, "service": service, "period": f"{row['period_start']}/{row['period_end']}",
                "unit": "CEmONC bed equivalents" if occupancy is not None else row["capacity_unit"],
                "demand": row["capacity"] * occupancy / 100 if occupancy is not None else row["caseload"],
                "capacity": row["capacity"], "capacity_predicted": row["capacity_predicted"],
                "basis": "Reported average CEmONC occupancy and bed inventory" if occupancy is not None else "Same-period service throughput",
                "source_url": row["capacity_source"]["url"],
            })

    result = compare_modes(
        pd.DataFrame(sites), pd.DataFrame(claims), origin["lat"], origin["lon"], service,
        as_of=AS_OF, max_radius_km=MAX_RADIUS_KM, max_extra_km=MAX_EXTRA_KM, travel_weight=TRAVEL_WEIGHT,
        spread_demand=True, load_context="planning", pressure_inputs=pd.DataFrame(pressure),
    )
    eligible = result["trace"].query("mode == 'confirmed' and decision == 'eligible'")
    before = eligible.sort_values("baseline_rank")
    after = eligible.sort_values("rank")

    def order(frame):
        return [
            {"facility_id": cell.facility_id, "name": cell.name, "distance_km": cell.distance_km,
             **{key: by_id[cell.facility_id][key] for key in ("caseload", "caseload_predicted", "capacity", "capacity_predicted")}}
            for cell in frame.itertuples()
        ]

    compared = bool(eligible["weighted_score"].notna().any())
    changes = [{"facility_id": cell.facility_id, "name": cell.name, "before": int(cell.baseline_rank),
                "after": int(cell.rank)} for cell in after.itertuples() if cell.baseline_rank != cell.rank]
    return {
        "origin": origin, "status": result["summary"].iloc[0]["load_status"], "compared": compared,
        "nearest_order": order(before), "planning_order": order(after) if compared else None,
        "rank_changes": changes if compared else None,
        "scored_facilities": [
            {"facility_id": cell.facility_id, "name": cell.name, "pressure_ratio": cell.pressure_ratio,
             "planning_score": cell.weighted_score,
             "caseload_predicted": by_id[cell.facility_id]["caseload_predicted"],
             "capacity_predicted": by_id[cell.facility_id]["capacity_predicted"]}
            for cell in after.itertuples() if pd.notna(cell.weighted_score)
        ],
    }


def service_key(value):
    return value.casefold().replace(" / ", "_").replace("-", "_").replace(" ", "_")


def replay_groups(rows, release):
    grouped = defaultdict(list)
    for row in rows:
        if row["caseload"] is not None:
            grouped[row["service"], row["cohort"], row["period_start"], row["period_end"], row["unit"]].append(row)
    groups = []
    for (care, cohort, start, end, unit), members in sorted(grouped.items()):
        if len(members) < 2:
            continue
        sites = replay_sites(members, release)
        pair_km = closest_pair(sites)
        group = {"care_needed": care, "cohort": cohort, "period_start": start, "period_end": end,
                 "unit": unit, "facilities": len(members), "closest_pair_km": round(pair_km, 3),
                 "facilities_with_capacity": sum(row["capacity"] is not None for row in members)}
        cases = []
        code = service_key(care)
        if not routine_load_allowed(code):
            group["status"] = "time_critical"
        else:
            origins = planning_origins(sites)
            if pair_km > 2 * MAX_RADIUS_KM:
                origins = [origin for origin in origins if origin["kind"] == "facility_coordinates"]
            cases = [run_order(members, sites, origin, code, cohort) for origin in origins]
            if any(case["compared"] for case in cases):
                group["status"] = "compared"
            elif pair_km > 2 * MAX_RADIUS_KM:
                group["status"] = "no_local_pair"
            elif group["facilities_with_capacity"] < 2:
                group["status"] = "no_comparable_capacity"
            else:
                group["status"] = "no_comparable_capacity_or_detour"
        groups.append({**group, "cases": cases})
    return groups


def replay_report(release_path, capacities=()):
    evidence = read_json("load_evidence.json")
    original = read_json("load_planning_replay.json")
    expected_release = read_json("baseline_coverage.json")["dataset"]
    if release_sha256(release_path) != expected_release["sha256"]:
        raise ValueError("Use the exact reviewed facility release from baseline_coverage.json")
    release = read_release(release_path)
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "expansion").glob("*.json"))]
    manifests.append(read_json("care_replay_odisha_mri.json"))
    rows = aligned_caseloads(manifests, release, evidence["as_of_month"])
    attach_maternity_inventory(rows, read_json("care_replay_maternity.json"))
    attach_throughput_capacity(rows, capacities)
    groups = replay_groups(rows, release)
    forecasts = [r for r in rows if r["caseload_predicted"] is True]
    cases = [case for group in groups for case in group["cases"]]
    coverage = []
    for service in read_json("observations.json")["targets"]:
        selected = [row for row in rows if row["service"] == service]
        selected_groups = [group for group in groups if group["care_needed"] == service]
        group_cases = [case for group in selected_groups for case in group["cases"]]
        coverage.append({
            "care_needed": service,
            "reported_rows": sum(r["caseload_predicted"] is False for r in selected),
            "predicted_rows": sum(r["caseload_predicted"] is True for r in selected),
            "new_aligned_predictions": sum(r["caseload_predicted"] is True and r["aligned_for_replay"] for r in selected),
            "linked_facilities": len({r["facility_id"] for r in selected if r["caseload"] is not None}),
            "cohort_statuses": dict(Counter(g["status"] for g in selected_groups)),
            "tested_origins": len(group_cases), "compared_origins": sum(case["compared"] for case in group_cases),
            "changed_origins": sum(bool(case["rank_changes"]) for case in group_cases),
        })
    return {
        "purpose": "Historical planning replay with independently flagged caseload and capacity provenance",
        "release": expected_release,
        "settings": {"distance_metric": "straight_line_km", "max_radius_km": MAX_RADIUS_KM,
                     "max_extra_km": MAX_EXTRA_KM, "travel_weight": TRAVEL_WEIGHT},
        "summary": {"reported_caseload_rows": sum(r["caseload_predicted"] is False for r in rows),
                    "predicted_caseload_rows": len(forecasts),
                    "new_aligned_predictions": sum(r["aligned_for_replay"] for r in forecasts),
                    "care_options_with_matched_caseload": sum(c["linked_facilities"] > 0 for c in coverage),
                    "facilities_with_caseload": len({r["facility_id"] for r in rows if r["caseload"] is not None}),
                    "withheld_caseload_rows": sum(r["caseload"] is None for r in rows),
                    "reported_capacity_rows": sum(r["capacity_predicted"] is False for r in rows),
                    "new_predicted_capacity_rows": sum(r["capacity_predicted"] is True for r in rows),
                    "tested_origins": len(cases), "compared_origins": sum(case["compared"] for case in cases),
                    "origins_with_rank_changes": sum(bool(case["rank_changes"]) for case in cases)},
        "coverage": coverage,
        "dialysis_prior_replay": {"source_file": "load_planning_replay.json", "summary": original["summary"],
                                  "inputs": [dict(row, caseload_predicted=False) for row in evidence["pressure_inputs"]],
                                  "separate_caseload_estimates": [dict(row, caseload_predicted=True, capacity_predicted=None)
                                                                  for row in evidence["estimated_caseload"]
                                                                  if row["service"] == "Dialysis"]},
        "caseloads": rows,
        "cohorts": groups,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", required=True, type=Path)
    parser.add_argument("--capacity", type=Path, help="Optional reviewed JSON list of same-period throughput rows")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    capacities = json.loads(args.capacity.read_text(encoding="utf-8")) if args.capacity else []
    content = json.dumps(replay_report(args.facilities, capacities), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
