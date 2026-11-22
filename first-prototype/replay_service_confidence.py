"""Replay prototype service candidates against the same frozen facility data."""

import argparse
import csv
from datetime import date
from hashlib import file_digest
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pandas as pd


LEGACY_REF = "b649ce32f049ea366841c5f08f9ebd6f6cd369a1"
PROTOTYPE = Path(__file__).resolve().parent
REPOSITORY = PROTOTYPE.parent
CAPACITY_RELEASE = REPOSITORY / "max-supplemented-data" / "capacity_and_confidence"
DISTRICT_BOUNDARIES = REPOSITORY / "load-balancing-pipeline" / "outputs" / "geoBoundaries-IND-ADM2.geojson"


def load_pipeline(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def load_raw(path):
    with path.open(encoding="utf-8") as stream:
        records = (json.loads(line) for line in stream if line.strip())
        rows = [
            {"unique_id": row["unique_id"], "capacity": row.get("capacity")}
            for row in records if "unique_id" in row
        ]
    # The Marketplace snapshot has no stable Spark row order.
    return pd.DataFrame(rows).sample(frac=1, random_state=42).reset_index(drop=True)


def summarize(scores, tiers):
    available = scores.dropna()
    return {
        "mean": round(float(available.mean()), 2) if not available.empty else None,
        "median": float(available.median()) if not available.empty else None,
        "high": int(tiers.eq("High").sum()),
    }


def candidate_rows(pipeline, snapshot, service):
    candidates = pipeline.service_candidates(snapshot, service)
    if candidates.empty and "facility_id" not in candidates:
        candidates = snapshot.iloc[:0].copy()
    candidates = candidates.set_index("facility_id")
    if not candidates.index.is_unique:
        raise ValueError(f"Prepared IDs are not unique for {service}.")
    return candidates


def score_stats(rows, score_col, tier_col):
    if rows.empty:
        return {"mean": None, "median": None, "high": 0}
    return summarize(rows[score_col], rows[tier_col])


def compare_services(legacy, legacy_snapshot, current, current_snapshot):
    current_index = current_snapshot.set_index("facility_id")
    report = []
    high_examples = []
    eligible_locations = current_index.index.isin(current.eligible_facility_locations(current_index).index)
    for service in legacy.SERVICE_TERMS:
        before = candidate_rows(legacy, legacy_snapshot, service)
        after = candidate_rows(current, current_snapshot, service)
        shared = before.index.intersection(after.index)
        new = after.index.difference(before.index)
        removed = before.index.difference(after.index)
        key = current._service_key(service)
        service_conflict = current_index[f"service_evidence_kind_{key}"].eq("conflict")
        scored_kind = current_index[f"service_evidence_kind_{key}"].isin(current.SERVICE_EVIDENCE_POINTS)
        matched = scored_kind | current_index.index.isin(before.index)
        location_hold = matched & ~eligible_locations
        holds = service_conflict | location_hold
        held = current_index.index[holds]
        if not held.intersection(after.index).empty:
            raise ValueError(f"A held {service} claim remained in candidates.")
        old_stats = score_stats(before, "confidence_score", "confidence_tier")
        new_stats = score_stats(after, "service_confidence_score", "service_confidence_tier")
        shared_old = score_stats(before.loc[shared], "confidence_score", "confidence_tier")
        shared_new = score_stats(after.loc[shared], "service_confidence_score", "service_confidence_tier")
        match_scores = after["service_match_score"] if not after.empty else pd.Series(dtype=float)
        report.append({
            "service": service,
            "old_candidates": len(before),
            "new_candidates": len(after),
            "shared_candidates": len(shared),
            "added_candidates": len(new),
            "removed_candidates": len(removed),
            "held_candidates": len(held),
            "held_legacy_candidates": len(held.intersection(before.index)),
            "unconfirmed_candidates": int(after["service_evidence_kind"].eq("mention").sum()),
            "record_listing_candidates": int(after["service_evidence_kind"].eq("record_listing").sum()),
            "high_match_candidates": int(match_scores.eq(100).sum()),
            "service_match_mean": round(float(match_scores.mean()), 2) if not match_scores.empty else None,
            "service_match_median": float(match_scores.median()) if not match_scores.empty else None,
            **{f"old_{name}": value for name, value in old_stats.items()},
            **{f"new_{name}": value for name, value in new_stats.items()},
            **{f"shared_old_{name}": value for name, value in shared_old.items()},
            **{f"shared_new_{name}": value for name, value in shared_new.items()},
        })
        if not after.empty:
            high_rows = after.loc[after.service_confidence_tier.eq("High")]
            high_examples.extend({
                "service": service,
                "facility_id": row.Index,
                "facility_name": row.facility_name,
                "previous_score": float(row.confidence_score),
                "service_confidence_score": int(row.service_confidence_score),
                "source_kind": row.service_evidence_kind,
                "added_candidate": row.Index in new,
            } for row in high_rows.itertuples())
    return report, high_examples


def pinned_legacy_pipeline(repository):
    source = subprocess.check_output([
        "git", "-C", str(repository), "show", f"{LEGACY_REF}:first-prototype/pipeline.py",
    ])
    with TemporaryDirectory(prefix="prototype-legacy-confidence-") as temporary:
        path = Path(temporary) / "pipeline.py"
        path.write_bytes(source)
        return load_pipeline(path, "legacy_confidence_pipeline")


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-facilities", type=Path, required=True)
    parser.add_argument("--capacity-release", type=Path, default=CAPACITY_RELEASE / "facilities_newest_capacity.csv")
    parser.add_argument("--legacy-confidence", type=Path, default=CAPACITY_RELEASE / "facility_confidence.csv")
    parser.add_argument("--reviewed-evidence", type=Path, default=PROTOTYPE / "service_confidence_evidence.json")
    parser.add_argument("--location-holds", type=Path, default=PROTOTYPE / "location_routing_holds.json")
    parser.add_argument("--district-boundaries", type=Path, default=DISTRICT_BOUNDARIES)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    capacity = pd.read_csv(args.capacity_release, dtype={"unique_id": "string"}, low_memory=False)
    confidence = pd.read_csv(args.legacy_confidence)
    raw = load_raw(args.raw_facilities)
    legacy = pinned_legacy_pipeline(REPOSITORY)
    current = load_pipeline(PROTOTYPE / "pipeline.py", "current_confidence_pipeline")
    before = legacy.prepare_facilities(legacy.attach_capacity_release(raw, capacity, confidence))
    evidence = current.load_reviewed_service_evidence(args.reviewed_evidence)
    holds = current.load_audited_location_holds(args.location_holds)
    after = current.prepare_facilities(
        current.attach_capacity_release(raw, capacity, confidence), reviewed_evidence=evidence, as_of=args.as_of,
        audited_location_holds=holds,
    )
    # Apply the prototype's district geography to every source row. NFHS joins are
    # unnecessary for this replay of location eligibility and confidence.
    boundaries = json.loads(args.district_boundaries.read_text(encoding="utf-8"))
    after = current.attach_regions(after, boundaries, pd.DataFrame())
    if not before["facility_id"].equals(after["facility_id"]):
        raise ValueError("Prepared facility IDs differ between snapshots.")
    report, high_examples = compare_services(legacy, before, current, after)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(report)
    print(json.dumps({
        "as_of": args.as_of.isoformat(),
        "legacy_ref": LEGACY_REF,
        "source_rows": len(before),
        "boundary_check": "Applied the tracked ADM2 boundaries to all current snapshot rows.",
        "sha256": {
            name: sha256(path) for name, path in (
                ("raw_facilities", args.raw_facilities),
                ("capacity_release", args.capacity_release),
                ("legacy_confidence", args.legacy_confidence),
                ("reviewed_evidence", args.reviewed_evidence),
                ("location_holds", args.location_holds),
                ("district_boundaries", args.district_boundaries),
            )
        },
        "services": report,
        "high_examples": high_examples,
    }, indent=2))


if __name__ == "__main__":
    main()
