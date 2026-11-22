"""Replay synthetic demand at one origin using historical network forecasts."""

import argparse
import csv
import gzip
import json
from math import isfinite
from pathlib import Path
import sys

import pandas as pd

from baseline_coverage import read_json, release_sha256


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from facility_tool.network_load import read_network_pressure, replay_network_load  # noqa: E402
from facility_tool.recommend import haversine_km  # noqa: E402
from facility_tool.travel import DISTANCE_MODES  # noqa: E402


MAX_RADIUS_KM = 50
MAX_EXTRA_KM = 10
MAX_EXTRA_MINUTES = 15
TRAVEL_WEIGHT = 0.5
REPORTED_IN_SCOPE = "reported_activity_in_scope"
CONDITIONAL_LEADS = {"verified_service_broad_scope", "service_listed_unverified"}
FACILITY_FIELDS = ("facility_id", "name", "city", "state", "pin", "latitude", "longitude")
REPLAY_FIELDS = ("facility_id", "service", "distance_km", "evidence_tier", "scope", "eligible")


def read_sites(path):
    sites = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {*FACILITY_FIELDS, "routing_eligible"} <= set(reader.fieldnames or ()):
            raise ValueError("The facility release lacks routing and identity fields.")
        for row in reader:
            ident = (row["facility_id"] or "").strip()
            if not ident or row["routing_eligible"].strip().casefold() != "true":
                continue
            site = {field: row[field] for field in FACILITY_FIELDS}
            if ident in sites and site != sites[ident]:
                raise ValueError(f"Conflicting routable facility rows for {ident}.")
            sites[ident] = site
    return sites


def read_forecast_evidence(path, service, release_digest):
    rows = {}
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"facility_id", "service", "service_evidence", "scope", "release_sha256"} <= set(reader.fieldnames or ()):
            raise ValueError("Use the generated network forecast CSV.gz.")
        for row in reader:
            if row["service"] != service:
                continue
            if row["release_sha256"] != release_digest:
                raise ValueError("The forecast identifies a different facility release.")
            ident = (row["facility_id"] or "").strip()
            if not ident or ident in rows:
                raise ValueError(f"Missing or repeated forecast ID for {service}: {ident!r}.")
            rows[ident] = {field: row[field] for field in ("scope", "service_evidence")}
    if not rows:
        raise ValueError(f"No forecast rows for {service!r}. Use a service code from the forecast.")
    return rows


def route_number(value):
    try:
        if type(value) in (int, float) and isfinite(value) and value >= 0:
            return value
    except OverflowError:
        pass
    raise ValueError("Route distances and travel times must be finite nonnegative numbers.")


def read_routes(path, lat, lon, candidate_ids):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Supply a route file with origin, source, mode and routes.")
    origin = data.get("origin")
    if not isinstance(origin, dict) or (origin.get("lat"), origin.get("lon")) != (lat, lon):
        raise ValueError("The route file origin must match --lat and --lon.")
    source, mode = data.get("source"), data.get("mode")
    if not isinstance(source, str) or not source.strip() or mode not in DISTANCE_MODES:
        raise ValueError("The route file needs a provider source and a driving, walking or cycling mode.")
    if not isinstance(data.get("routes"), list):
        raise ValueError("The route file needs a routes list.")
    routes = {}
    for row in data["routes"]:
        ident = row.get("facility_id") if isinstance(row, dict) else None
        if not isinstance(ident, str) or not ident.strip() or ident in routes:
            raise ValueError("Provide one route per facility ID in the route file.")
        routes[ident] = {
            "route_distance_km": route_number(row.get("route_distance_km")),
            "travel_minutes": route_number(row.get("travel_minutes")),
            "travel_mode": mode,
            "travel_source": source,
            "travel_status": "estimated",
        }
    missing = candidate_ids - routes.keys()
    if missing:
        raise ValueError("Supply both route distance and route time for every scoped candidate within 50 km. "
                         f"Missing route for {', '.join(sorted(missing)[:5])} ({len(missing)} total).")
    return routes, {
        "metric": "route_minutes", "source": source, "mode": mode, "file": path.name, "sha256": release_sha256(path),
    }


def build_candidates(sites, forecast, lat, lon, assumed_ids, include_conditional):
    haversine_km(lat, lon, lat, lon)
    invalid = sorted(assumed_ids - (sites.keys() & forecast.keys()))
    if invalid:
        raise ValueError(f"Assumed branches must be routable and have a service forecast row: {', '.join(invalid)}.")

    candidates = []
    counts = {
        "historical_scope_matched_routable": 0,
        "conditional_service_leads_routable": 0,
        "service_unknown_routable": 0,
        "selected_outside_radius": 0,
    }
    for ident in sites.keys() & forecast.keys():
        evidence = forecast[ident]
        confirmed = evidence["service_evidence"] == REPORTED_IN_SCOPE
        lead = evidence["service_evidence"] in CONDITIONAL_LEADS
        if confirmed:
            counts["historical_scope_matched_routable"] += 1
        elif lead:
            counts["conditional_service_leads_routable"] += 1
        else:
            counts["service_unknown_routable"] += 1
        if not (confirmed or (include_conditional and lead) or ident in assumed_ids):
            continue
        site = sites[ident]
        latitude, longitude = float(site["latitude"]), float(site["longitude"])
        distance = haversine_km(lat, lon, latitude, longitude)
        if distance > MAX_RADIUS_KM:
            counts["selected_outside_radius"] += 1
            continue
        candidates.append({
            **site, **evidence, "latitude": latitude, "longitude": longitude, "distance_km": distance,
            "service_assumed": not confirmed,
            "evidence_tier": 0, "eligible": True,
        })
    return candidates, counts


def plan(*, facilities, forecasts, service, lat, lon, requests, assume_facility_ids=(),
         include_conditional=False, routes=None):
    digest = release_sha256(facilities)
    if digest != read_json("baseline_coverage.json")["dataset"]["sha256"]:
        raise ValueError("Use the exact reviewed facility release recorded in baseline_coverage.json.")
    assumed = set(assume_facility_ids)
    sites = read_sites(facilities)
    evidence = read_forecast_evidence(forecasts, service, digest)
    candidates, counts = build_candidates(sites, evidence, lat, lon, assumed, include_conditional)
    candidate_ids = {row["facility_id"] for row in candidates}
    if routes:
        route_rows, metric = read_routes(routes, lat, lon, candidate_ids)
        for row in candidates:
            row.update(route_rows[row["facility_id"]])
        candidates.sort(key=lambda row: (row["travel_minutes"], row["distance_km"], row["facility_id"]))
    else:
        metric = {"metric": "straight_line_km", "source": "Haversine great-circle distance"}
        candidates.sort(key=lambda row: (row["distance_km"], row["facility_id"]))
    for rank, row in enumerate(candidates, start=1):
        row["rank"] = rank
        row["service"] = service
        row["ranking_travel_metric"] = metric["metric"]

    pressure = read_network_pressure(forecasts, service, eligible_ids=candidate_ids)
    replay = replay_network_load(
        pd.DataFrame(candidates) if candidates else pd.DataFrame(columns=REPLAY_FIELDS),
        pressure, requests, max_extra_km=MAX_EXTRA_KM,
        max_extra_minutes=MAX_EXTRA_MINUTES, travel_weight=TRAVEL_WEIGHT,
    )
    records = json.loads(replay.to_json(orient="records", double_precision=15))
    balanced_total = sum(row["balanced_suggestions"] for row in records)
    baseline_total = sum(row["baseline_suggestions"] for row in records)
    return {
        "purpose": "Historical planning scenario with synthetic requests",
        "operational_use": False,
        "booking_performed": False,
        "origin": {"lat": lat, "lon": lon},
        "service": service,
        "synthetic_requests": requests,
        "facility_source": {"file": facilities.name, "sha256": digest},
        "forecast_source": {"file": forecasts.name, "sha256": release_sha256(forecasts)},
        "settings": {
            "travel": metric, "max_straight_line_radius_km": MAX_RADIUS_KM,
            "max_extra_km": MAX_EXTRA_KM, "max_extra_minutes": MAX_EXTRA_MINUTES if routes else None,
            "travel_weight": TRAVEL_WEIGHT,
            "include_conditional": include_conditional, "assume_facility_ids": sorted(assumed),
        },
        "eligibility": {
            **counts, "candidates_within_radius": len(candidates),
            "assumed_candidates_within_radius": sum(row["service_assumed"] for row in candidates),
            "service_evidence_rule": "Scope-matched historical activity or an explicit scenario assumption",
        },
        "nearest_eligible_facility_id": candidates[0]["facility_id"] if candidates else None,
        "summary": {
            "status": records[0]["status"] if records else "no_eligible_candidate",
            "comparable_candidates": sum(row["comparable"] for row in records),
            "baseline_suggestions": baseline_total,
            "balanced_suggestions": balanced_total,
            "requests_redirected_from_baseline": sum(
                max(row["balanced_suggestions"] - row["baseline_suggestions"], 0) for row in records
            ),
            "unassigned_synthetic_requests": requests - balanced_total,
        },
        "candidates": candidates,
        "replay": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facilities", required=True, type=Path, help="Pinned facilities_newest_capacity.csv")
    parser.add_argument("--forecasts", required=True, type=Path, help="Generated network forecast CSV.gz")
    parser.add_argument("--service", required=True, help="Care needed code in snake_case")
    parser.add_argument("--lat", required=True, type=float, help="Origin latitude in decimal degrees")
    parser.add_argument("--lon", required=True, type=float, help="Origin longitude in decimal degrees")
    parser.add_argument("--requests", required=True, type=int, help="Number of synthetic service-unit requests")
    parser.add_argument("--assume-facility-ids", nargs="+", default=(), help="Admit these unverified branches in the scenario")
    parser.add_argument("--include-conditional", action="store_true",
                        help="Admit branches with broadly verified or unverified service listings as scenario assumptions")
    parser.add_argument("--routes", type=Path, help="Optional route JSON for the same origin and all selected branches within 50 km")
    parser.add_argument("--output", type=Path, help="Write the scenario receipt to a JSON file")
    args = parser.parse_args()
    try:
        result = plan(
            facilities=args.facilities, forecasts=args.forecasts, service=args.service,
            lat=args.lat, lon=args.lon, requests=args.requests, assume_facility_ids=args.assume_facility_ids,
            include_conditional=args.include_conditional, routes=args.routes,
        )
        content = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            inputs = {path.resolve() for path in (args.facilities, args.forecasts, args.routes) if path}
            if args.output.resolve() in inputs:
                raise ValueError("The scenario output path must differ from its inputs.")
            args.output.write_text(content, encoding="utf-8")
        else:
            print(content, end="")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
