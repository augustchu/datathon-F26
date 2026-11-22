"""Compare two care-finder rankers on the same frozen candidates and travel inputs."""

import argparse
import csv
from datetime import date
import json
from pathlib import Path

import pandas as pd

import pipeline as current
from replay_service_confidence import CAPACITY_RELEASE, DISTRICT_BOUNDARIES, PROTOTYPE, load_pipeline, load_raw, sha256


SCORE_COMPONENTS = (
    "facility_name", "city", "state", "latitude", "longitude", "distance_km", "travel_time_minutes",
    "base_travel_cost", "score_unit", "service_evidence_kind", "service_match_score", "service_confidence_score",
    "load_penalty", "service_match_penalty", "evidence_confidence_penalty", "score",
)


def read_saved_routes(path):
    """Read reachable routes keyed by origin label and prepared facility ID."""
    if path is None:
        return {}
    with path.open(encoding="utf-8", newline="") as stream:
        rows = csv.DictReader(stream)
        if not {"origin", "facility_id", "travel_time_minutes"}.issubset(rows.fieldnames or ()):
            raise ValueError("Saved routes need origin, facility_id, and travel_time_minutes columns.")
        routes = {}
        for row in rows:
            origin = row["origin"].strip()
            facility_id = row["facility_id"].strip()
            minutes = float(row["travel_time_minutes"])
            if not origin or not facility_id or not 0 <= minutes < float("inf"):
                raise ValueError("Saved routes need nonempty IDs and finite, nonnegative minutes.")
            stops = routes.setdefault(origin, {})
            if facility_id in stops:
                raise ValueError(f"Duplicate saved route for {origin!r} and {facility_id!r}.")
            stops[facility_id] = minutes
    return routes


def replay_origins(frame, places, coordinates):
    if not places and not coordinates:
        places = ["Jaipur, Rajasthan"]
    origins = [(name, *current.resolve_location(frame, name)) for name in places]
    origins.extend((name, *current.resolve_location(frame, lat=latitude, lon=longitude))
                   for name, latitude, longitude in coordinates)
    if len({name for name, _, _ in origins}) != len(origins):
        raise ValueError("Use a unique label for each replay origin.")
    return origins


def rank_rows(ranker, frame, service, origin, severity, travel_times):
    ranked = ranker.rank_candidates(frame, service, origin, severity=severity,
                                    population_share_pct=0, top_k=len(frame), travel_times=travel_times)
    ranks = {}
    columns = ranked.columns.intersection(("facility_id", "confidence_penalty", *SCORE_COMPONENTS))
    for rank, row in enumerate(ranked[columns].to_dict(orient="records"), 1):
        facility_id = row["facility_id"]
        if facility_id in ranks:
            raise ValueError(f"Duplicate ranked facility {facility_id!r} in {service!r}.")
        ranks[facility_id] = {"rank": rank, **row}
    return ranks


def compare_service(baseline, frame, service, name, origin, origin_basis, severity, routes, top_k):
    before = rank_rows(baseline, frame, service, origin, severity, routes)
    after = rank_rows(current, frame, service, origin, severity, routes)
    if before.keys() != after.keys():
        raise ValueError(f"Ranking changed candidate eligibility for {service!r} at {name!r}.")
    before_top, after_top = list(before)[:top_k], list(after)[:top_k]
    context = {
        "origin": name,
        "origin_latitude": origin[0],
        "origin_longitude": origin[1],
        "origin_basis": origin_basis,
        "service": service,
        "severity": severity,
        "routing": "saved driving times" if routes is not None else "straight-line distance",
    }
    before_winner = before[before_top[0]] if before_top else {}
    after_winner = after[after_top[0]] if after_top else {}
    summary = {
        **context,
        "ranked_candidates": len(after),
        "top_k": top_k,
        "top_k_overlap": len(set(before_top) & set(after_top)),
        "winner_changed": before_top[:1] != after_top[:1],
        "before_winner": before_winner.get("facility_name"),
        "after_winner": after_winner.get("facility_name"),
        "before_winner_id": before_winner.get("facility_id"),
        "after_winner_id": after_winner.get("facility_id"),
        "before_winner_travel": before_winner.get("base_travel_cost"),
        "after_winner_travel": after_winner.get("base_travel_cost"),
        "travel_unit": after_winner.get("score_unit"),
        "before_winner_match": before_winner.get("service_match_score"),
        "after_winner_match": after_winner.get("service_match_score"),
        "before_winner_confidence": before_winner.get("service_confidence_score"),
        "after_winner_confidence": after_winner.get("service_confidence_score"),
        "before_top": " | ".join(before[key]["facility_name"] for key in before_top),
        "after_top": " | ".join(after[key]["facility_name"] for key in after_top),
    }
    changes = []
    for facility_id in dict.fromkeys(after_top + before_top):
        earlier, later = before[facility_id], after[facility_id]
        changes.append({
            **context, "facility_id": facility_id,
            "before_rank": earlier["rank"], "after_rank": later["rank"],
            "places_gained": earlier["rank"] - later["rank"],
            "before_release_data_penalty": earlier.get("confidence_penalty"),
            **{f"before_{key}": earlier.get(key) for key in SCORE_COMPONENTS},
            **{f"after_{key}": later.get(key) for key in SCORE_COMPONENTS},
        })
    return summary, changes


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-pipeline", type=Path, required=True)
    parser.add_argument("--raw-facilities", type=Path, required=True)
    parser.add_argument("--capacity-release", type=Path, default=CAPACITY_RELEASE / "facilities_newest_capacity.csv")
    parser.add_argument("--legacy-confidence", type=Path, default=CAPACITY_RELEASE / "facility_confidence.csv")
    parser.add_argument("--reviewed-evidence", type=Path, default=PROTOTYPE / "service_confidence_evidence.json")
    parser.add_argument("--location-holds", type=Path, default=PROTOTYPE / "location_routing_holds.json")
    parser.add_argument("--district-boundaries", type=Path, default=DISTRICT_BOUNDARIES)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--place", action="append", default=[], help="Prototype city, district, or PIN search.")
    parser.add_argument("--origin", nargs=3, metavar=("LABEL", "LATITUDE", "LONGITUDE"), action="append", default=[])
    parser.add_argument("--routes-csv", type=Path, help="Saved driving times for reachable destinations.")
    parser.add_argument("--severity", choices=("routine", "urgent", "emergency"), default="routine")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")

    baseline = load_pipeline(args.baseline_pipeline, "replay_before_ranking")
    capacity = pd.read_csv(args.capacity_release, dtype={"unique_id": "string"}, low_memory=False)
    confidence = pd.read_csv(args.legacy_confidence)
    raw = load_raw(args.raw_facilities)
    frame = current.prepare_facilities(
        current.attach_capacity_release(raw, capacity, confidence),
        reviewed_evidence=current.load_reviewed_service_evidence(args.reviewed_evidence),
        audited_location_holds=current.load_audited_location_holds(args.location_holds), as_of=args.as_of,
    )
    boundaries = json.loads(args.district_boundaries.read_text(encoding="utf-8"))
    frame = current.attach_regions(frame, boundaries, pd.DataFrame())
    origins = replay_origins(frame, args.place, args.origin)
    saved_routes = read_saved_routes(args.routes_csv)
    unknown_origins = set(saved_routes) - {name for name, _, _ in origins}
    if unknown_origins:
        raise ValueError(f"Saved routes refer to unrequested origins: {sorted(unknown_origins)!r}.")

    summaries, details = [], []
    for name, point, basis in origins:
        for service in current.SERVICE_TERMS:
            summary, changes = compare_service(baseline, frame, service, name, point, basis, args.severity,
                                               saved_routes.get(name), args.top_k)
            summaries.append(summary)
            details.extend(changes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "ranking_by_service.csv", summaries)
    write_csv(args.output_dir / "top_result_changes.csv", details)
    paths = {
        "baseline_pipeline": args.baseline_pipeline, "current_pipeline": PROTOTYPE / "pipeline.py",
        "scoring": PROTOTYPE / "service_confidence.py", "raw_facilities": args.raw_facilities,
        "capacity_release": args.capacity_release, "legacy_confidence": args.legacy_confidence,
        "reviewed_evidence": args.reviewed_evidence, "location_holds": args.location_holds,
        "district_boundaries": args.district_boundaries, "routes": args.routes_csv,
    }
    receipt = {
        "as_of": args.as_of.isoformat(), "source_rows": len(frame), "severity": args.severity,
        "population_share_pct": 0, "top_k": args.top_k,
        "sha256": {name: sha256(path) for name, path in paths.items() if path is not None},
        "rankings": summaries,
    }
    (args.output_dir / "ranking_replay.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Compared {len(summaries)} searches and saved results to {args.output_dir}")


if __name__ == "__main__":
    main()
