#!/usr/bin/env python3
"""Run the benchmark through the Databricks app's ranking code on a local snapshot."""

import argparse
from datetime import date, datetime, timezone
import gzip
import json
from math import isfinite
import os
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter

from evaluate import indexed, read_json, validate_queries


ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE = ROOT / "first-prototype"
SERVICE_MENU = {
    "dialysis": "Dialysis",
    "mri": "MRI",
    "nicu": "NICU",
    "cardiac_surgery": "Cardiac surgery",
    "emergency_c_section": "Emergency C-section",
}


def load_table(path, pd):
    open_file = gzip.open if path.suffix == ".gz" else open
    with open_file(path, "rt", encoding="utf-8") as stream:
        source = json.load(stream)
    return pd.DataFrame(source["rows"], columns=source["columns"], dtype=object)


def build_snapshot(args, pipeline, replay, pd):
    capacity = pd.read_csv(args.capacity, dtype={"unique_id": "string"}, low_memory=False)
    confidence = pd.read_csv(args.confidence)
    raw = replay.load_raw(args.raw_facilities)
    frame = pipeline.prepare_facilities(
        pipeline.attach_capacity_release(raw, capacity, confidence),
        reviewed_evidence=pipeline.load_reviewed_service_evidence(args.reviewed_evidence),
        audited_location_holds=pipeline.load_audited_location_holds(args.location_holds),
        as_of=args.as_of,
    )
    boundary = json.loads(args.boundaries.read_text(encoding="utf-8"))
    nfhs = load_table(args.nfhs, pd) if args.nfhs else pd.DataFrame()
    frame = pipeline.attach_regions(frame, boundary, nfhs)
    return pipeline.attach_population(frame, pd.read_csv(args.population))


def export_results(ranked, travel_basis):
    records = json.loads(ranked.to_json(orient="records"))
    output = []
    for row in records:
        reviewed = json.loads(row["service_evidence_sources_json"] or "[]")
        source_urls = json.loads(row["source_urls_json"] or "[]")
        source_url = reviewed[0]["url"] if reviewed else (source_urls[0] if source_urls else None)
        pin = re.fullmatch(r"[1-9][0-9]{5}(?:\.0)?", str(row.get("pin")))
        output.append({
            "facility_id": row["facility_id"],
            "name": row["facility_name"],
            "pin": pin.group(0)[:6] if pin else None,
            "city": row.get("city"),
            "state": row.get("state"),
            "evidence_excerpt": row["supporting_excerpt"],
            "source_url": source_url,
            "citation_scope": "reviewed_service" if reviewed else ("facility_record" if source_urls else None),
            "service_evidence_kind": row["service_evidence_kind"],
            "service_match_score": row["service_match_score"],
            "service_confidence_score": row["service_confidence_score"],
            "travel_basis": travel_basis,
            "travel_mode": "driving" if travel_basis == "route_time" else None,
            "travel_value": row["travel_time_minutes"] if travel_basis == "route_time" else row["distance_km"],
            "score": row["score"],
        })
    return output


def run_query(query, frame, pipeline, travel_time_matrix, use_traveltime):
    started = perf_counter()
    record = {"query_id": query["id"], "results": []}
    service = SERVICE_MENU.get(query["service"])
    if service not in pipeline.SERVICE_TERMS:
        record.update(outcome="unsupported_query", reason="The Databricks app has no exact menu entry for this service.")
    elif query["transport_mode"] != "driving":
        record.update(outcome="unsupported_query", reason="The Databricks app only supports driving as a routed transport mode.")
    else:
        candidates = pipeline.service_candidates(frame, service)
        if candidates.empty:
            record.update(outcome="abstain", reason="No candidate passed the app's service and location screens.")
        else:
            try:
                origin, origin_basis = pipeline.resolve_location(frame, query["origin"])
                travel_times = None
                nearest_only = False
                route_status = "offline"
                route_count = None
                if use_traveltime:
                    try:
                        travel_times = travel_time_matrix(origin, candidates[["facility_id", "latitude", "longitude"]])
                        minutes = (travel_times.get(str(key)) for key in candidates["facility_id"])
                        route_count = sum(value is not None and isfinite(value) and value >= 0 for value in minutes)
                        route_status = "live" if route_count else "no_routes_straight_line"
                        if not route_count:
                            travel_times, nearest_only = None, True
                    except Exception as error:
                        travel_times = None
                        route_status = f"provider_error_straight_line:{type(error).__name__}"
                severity = "emergency" if query["service"] == "emergency_c_section" else "routine"
                ranked = pipeline.rank_candidates(
                    frame, service, origin, severity=severity, population_share_pct=0.0, top_k=5,
                    travel_times=travel_times, nearest_only=nearest_only,
                )
                travel_basis = "route_time" if travel_times is not None else "straight_line"
                record.update(
                    outcome="abstain" if ranked.empty else "recommendations",
                    reason="No options remained after ranking." if ranked.empty else None,
                    results=export_results(ranked, travel_basis) if not ranked.empty else [],
                    routing={"status": route_status, "origin_basis": origin_basis, "candidates": len(candidates),
                             "provider_reachable": route_count, "severity": severity},
                )
            except Exception as error:
                record.update(outcome="error", reason=f"{type(error).__name__}: {error}")
    record["latency_ms"] = round((perf_counter() - started) * 1000, 2)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=Path(__file__).resolve().with_name("queries.json"))
    parser.add_argument("--raw-facilities", type=Path, required=True)
    parser.add_argument("--nfhs", type=Path)
    parser.add_argument("--capacity", type=Path, default=ROOT / "max-supplemented-data/capacity_and_confidence/facilities_newest_capacity.csv")
    parser.add_argument("--confidence", type=Path, default=ROOT / "max-supplemented-data/capacity_and_confidence/facility_confidence.csv")
    parser.add_argument("--reviewed-evidence", type=Path, default=PROTOTYPE / "service_confidence_evidence.json")
    parser.add_argument("--location-holds", type=Path, default=PROTOTYPE / "location_routing_holds.json")
    parser.add_argument("--boundaries", type=Path, default=ROOT / "load-balancing-pipeline/outputs/geoBoundaries-IND-ADM2.geojson")
    parser.add_argument("--population", type=Path, default=ROOT / "load-balancing-pipeline/outputs/nfhs_district_population_2020.csv")
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--use-traveltime", action="store_true", help="Allow live driving requests using configured credentials.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(PROTOTYPE))
    import pandas as pd
    import pipeline
    import replay_service_confidence as replay
    from traveltime_adapter import travel_time_matrix

    queries, _ = read_json(args.queries)
    queries = indexed(queries, "id", "queries")
    validate_queries(queries)
    frame = build_snapshot(args, pipeline, replay, pd)
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    sources = ("raw_facilities", "capacity", "confidence", "reviewed_evidence",
               "location_holds", "boundaries", "population", "nfhs")
    output = {
        "run": {
            "id": args.run_id,
            "algorithm": "Databricks first-prototype service and location screening, then rank_candidates",
            "code_commit": commit,
            "snapshot": f"Frozen Marketplace replay with {len(frame)} source rows",
            "evidence_as_of": args.as_of.isoformat(),
            "routing": "TravelTime driving, with the app's fallback" if args.use_traveltime else "straight_line",
            "execution": "local replay of the Databricks app algorithm",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "population_share_pct": 0,
            "input_sha256": {key: replay.sha256(getattr(args, key)) for key in sources if getattr(args, key)},
        },
        "predictions": [],
    }
    for query in queries.values():
        record = run_query(query, frame, pipeline, travel_time_matrix, args.use_traveltime)
        output["predictions"].append(record)
        print(f"{query['id']}  {record['outcome']}  {len(record['results'])} results", flush=True)
    descriptor = os.open(args.output, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
