#!/usr/bin/env python3
"""Export a repeatable offline run of the existing notebook lookup."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter

from evaluate import indexed, read_json, validate_queries


def result_rows(rows, mode):
    ranked = rows.loc[rows["mode"].eq(mode) & rows["kind"].eq("recommendation")].sort_values("rank")
    fields = {
        "facility_id": "facility_id",
        "name": "name",
        "pin": "pin",
        "source_url": "source_url",
        "evidence_excerpt": "excerpt",
        "travel_value": "distance_km",
    }
    records = json.loads(ranked.reindex(columns=list(fields.values())).to_json(orient="records"))
    results = []
    for row in records:
        result = {target: row[column] for target, column in fields.items()}
        pin = re.fullmatch(r"[1-9][0-9]{5}(?:\.0)?", str(result["pin"]))
        result["pin"] = pin.group(0)[:6] if pin else None
        result["travel_basis"] = "straight_line"
        results.append(result)
    return results


def run_query(query, *, lookup, store, config, mode):
    started = perf_counter()
    query_id, service = query["id"], query["service"]
    if service not in config["service_aliases"]:
        return {"query_id": query_id, "outcome": "unsupported_query", "reason": "Service is not in this version's menu."}
    try:
        response = lookup(
            store, config, concern=service, location=query["origin"],
            travel_mode=query["transport_mode"], traveltime_enabled=False,
            facility_sort="closest", spread_demand=False,
        )
        results = result_rows(response["rows"], mode)
    except Exception as error:
        # Keep runtime or snapshot failures distinct from a completed search that abstained.
        return {"query_id": query_id, "outcome": "error", "reason": f"{type(error).__name__}: {error}"}
    return {
        "query_id": query_id,
        "outcome": "recommendations" if results else "abstain",
        "results": results,
        "reason": response["routing"]["message"],
        "routing": response["routing"],
        "latency_ms": round((perf_counter() - started) * 1000, 2),
    }


def main():
    folder = Path(__file__).resolve().parent
    repo = folder.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=folder / "queries.json")
    parser.add_argument("--checkpoint-root", type=Path, required=True, help="Directory containing the mode and date directories.")
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--checkpoint-mode", choices=("databricks", "export", "public", "demo"), default="export")
    parser.add_argument("--mode", choices=("confirmed", "ml", "regression"), default="confirmed")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(repo / "non-ml" / "src"))
    from facility_tool.lookup import lookup
    from facility_tool.workspace import Store, read_config

    try:
        queries, _ = read_json(args.queries)
        queries = indexed(queries, "id", "queries")
        validate_queries(queries)
        config = read_config(repo)
        store = Store(repo, args.checkpoint_mode, args.snapshot_date, output_dir=args.checkpoint_root)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if not (store.path / "origin_index.jsonl").is_file():
        parser.error(f"No prepared origin checkpoint in {store.path}. Run notebook 06b first.")

    report = {
        "run": {
            "id": args.run_id,
            "algorithm": f"Existing non-ml lookup ({args.mode}, closest within evidence tier)",
            "snapshot": f"{args.checkpoint_mode}/{args.snapshot_date}",
            "routing": "straight_line",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "load_balancing": False,
            "equity": False,
        },
        "predictions": [run_query(query, lookup=lookup, store=store, config=config, mode=args.mode)
                        for query in queries.values()],
    }
    output_fd = os.open(args.output, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(output_fd, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
