#!/usr/bin/env python3
"""Score one ranked list per query against a partial, versioned evidence panel."""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import sys
import unicodedata
from urllib.parse import urlsplit


MODES = {"driving", "walking", "cycling", "public_transport"}
OUTCOMES = {"recommendations", "abstain", "unsupported_query", "error"}
LABELS = {"supported", "unsupported", "unresolved"}
TRAVEL_BASES = {"route_distance", "route_time", "straight_line", "unavailable"}
RUN_FIELDS = ("id", "algorithm", "snapshot", "routing", "recorded_at")


def read_json(path):
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def indexed(rows, key, context):
    if not isinstance(rows, list):
        raise ValueError(f"{context} must be an array.")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"Every row in {context} needs a nonempty {key}.")
        if row[key] in result:
            raise ValueError(f"{context} contains duplicate {key} {row[key]!r}.")
        result[row[key]] = row
    return result


def present(value):
    return isinstance(value, str) and bool(value.strip())


def canonical_name(name):
    text = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(re.findall(r"[^\W_]+", text))


def citation_present(result):
    url = result.get("source_url")
    if not present(url) or not present(result.get("evidence_excerpt")):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def facility_keys(row):
    keys = []
    if row.get("facility_id"):
        keys.append(("id", row["facility_id"]))
    if row.get("pin"):
        name = canonical_name(row["name"])
        if name:
            keys.append(("name_pin", name, row["pin"]))
    return keys


def reference_index(reference, queries):
    by_query = indexed(reference, "query_id", "references")
    unknown = by_query.keys() - queries.keys()
    if unknown:
        raise ValueError(f"References contain unknown query IDs: {', '.join(sorted(unknown))}.")
    result = {}
    for query_id, review in by_query.items():
        judgments = review.get("judgments")
        if not isinstance(judgments, list):
            raise ValueError(f"{query_id}: judgments must be an array.")
        keys = {}
        for position, judgment in enumerate(judgments):
            if not isinstance(judgment, dict) or judgment.get("label") not in LABELS:
                raise ValueError(f"{query_id}: each judgment needs a valid label.")
            ids, names, pin = (judgment.get("facility_ids", []), judgment.get("names", []), judgment.get("pin", ""))
            if not all(isinstance(values, list) and all(map(present, values)) for values in (ids, names)):
                raise ValueError(f"{query_id}: judgment IDs and names must be arrays of nonempty strings.")
            if names and (not isinstance(pin, str) or not re.fullmatch(r"[1-9][0-9]{5}", pin)):
                raise ValueError(f"{query_id}: every judgment using a name needs a six-digit PIN.")
            match_keys = [("id", value) for value in ids] + [("name_pin", canonical_name(name), pin) for name in names]
            if not match_keys:
                raise ValueError(f"{query_id}: each judgment needs an ID or an exact name and PIN.")
            sources = judgment.get("sources")
            if (not present(judgment.get("reason")) or not isinstance(sources, list) or not sources
                    or not all(isinstance(source, dict) and citation_present(
                        {"source_url": source.get("url"), "evidence_excerpt": source.get("excerpt")})
                        for source in sources)):
                raise ValueError(f"{query_id}: each judgment needs a reason and at least one source.")
            for key in match_keys:
                if key in keys and keys[key] != position:
                    raise ValueError(f"{query_id}: {key!r} appears in multiple judgments.")
                keys[key] = position
        result[query_id] = (judgments, keys)
    return result


def validate_queries(queries):
    for query_id, query in queries.items():
        if query.get("stratum") not in {"urban", "rural"} or query.get("suite") not in {"regression", "evidence_panel"}:
            raise ValueError(f"{query_id}: a valid stratum and suite are required.")
        if not all(present(query.get(field)) for field in ("origin", "service", "purpose")) or query.get("transport_mode") not in MODES:
            raise ValueError(f"{query_id}: origin, service, purpose and a valid transport_mode are required.")


def load_predictions(payload, queries):
    meta = payload.get("run", {}) if isinstance(payload, dict) else {}
    if not isinstance(meta, dict):
        raise ValueError("Prediction run metadata must be an object.")
    predictions = indexed(payload.get("predictions") if isinstance(payload, dict) else payload,
                          "query_id", "predictions")
    unknown = predictions.keys() - queries.keys()
    if unknown:
        raise ValueError(f"Predictions contain unknown query IDs: {', '.join(sorted(unknown))}.")
    for query_id, row in predictions.items():
        outcome, results = row.get("outcome"), row.get("results", [])
        if outcome not in OUTCOMES or not isinstance(results, list) or bool(results) != (outcome == "recommendations"):
            raise ValueError(f"{query_id}: outcome and ranked results disagree or are invalid.")
        latency = row.get("latency_ms")
        if latency is not None and (isinstance(latency, bool) or not isinstance(latency, (int, float))
                                    or not math.isfinite(latency) or latency < 0):
            raise ValueError(f"{query_id}: latency_ms must be a finite nonnegative number.")
        for item in results:
            if not isinstance(item, dict) or not present(item.get("name")):
                raise ValueError(f"{query_id}: each result needs a facility name.")
            for key in ("facility_id", "pin"):
                if item.get(key) is not None and not isinstance(item[key], str):
                    raise ValueError(f"{query_id}: {key} must be text when provided.")
            if not facility_keys(item):
                raise ValueError(f"{query_id}: each result needs a facility ID or a name and PIN.")
            if item.get("pin") and not re.fullmatch(r"[1-9][0-9]{5}", item["pin"]):
                raise ValueError(f"{query_id}: a supplied PIN must be a six-digit string.")
            if item.get("travel_basis") is not None and item["travel_basis"] not in TRAVEL_BASES:
                raise ValueError(f"{query_id}: unknown travel_basis {item['travel_basis']!r}.")
            if item.get("travel_mode") is not None and item["travel_mode"] not in MODES:
                raise ValueError(f"{query_id}: unknown travel_mode {item['travel_mode']!r}.")
            if "car_only" in item and not isinstance(item["car_only"], bool):
                raise ValueError(f"{query_id}: car_only must be a boolean when provided.")
            value = item.get("travel_value")
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError(f"{query_id}: travel_value must be a finite nonnegative number.")
    return meta, predictions


def travel_details(query, results):
    if not results:
        return None
    top = results[0]
    basis, value = top.get("travel_basis"), top.get("travel_value")
    if basis in {None, "unavailable"} or value is None:
        return None
    mode = top.get("travel_mode")
    if basis != "straight_line" and mode is None:
        return None
    comparable = [item["travel_value"] for item in results if item.get("travel_basis") == basis
                  and item.get("travel_mode") == mode and item.get("travel_value") is not None]
    return {
        "basis": basis,
        "travel_mode": mode,
        "requested_mode": query["transport_mode"],
        "comparable_results": len(comparable),
        "top_result_extra_over_nearest_returned": round(value - min(comparable), 3),
    }


def assess(query, prediction, reference, k):
    judgments, keys = reference
    prediction = prediction or {"outcome": "missing_output"}
    outcome = prediction["outcome"]
    details, seen = [], set()
    results = prediction.get("results", [])[:k]
    for rank, item in enumerate(results, 1):
        identity = facility_keys(item)
        matches = {keys[key] for key in identity if key in keys}
        if len(matches) > 1:
            raise ValueError(f"{query['id']} rank {rank}: facility ID and name/PIN match different judgments.")
        index = next(iter(matches), None)
        judgment = judgments[index] if index is not None else None
        label = judgment["label"] if judgment else "unreviewed"
        reason = judgment["reason"] if judgment else None
        pin_conflict = bool(judgment and judgment.get("pin") and item.get("pin")
                            and item["pin"] != judgment["pin"])
        if pin_conflict and label == "supported":
            label = "unresolved"
            reason = f"Returned PIN differs from the documented branch. {reason}"
        if judgment is not None:
            identity.append(("judgment", index))
        duplicate = any(key in seen for key in identity)
        seen.update(identity)
        route_issue = None
        if item.get("car_only") and (query["transport_mode"] == "driving" or item.get("travel_mode") != "driving"
                                     or item.get("requested_mode_status") != "not_routed_within_limit"
                                     or item.get("travel_basis") not in {"route_distance", "route_time"}):
            route_issue = "Car-only label lacks a driving route or an explicit no-route status for the requested mode."
        details.append({
            "rank": rank,
            "facility": item,
            "judgment": label,
            "reference_reason": reason,
            "reference_sources": judgment["sources"] if judgment else [],
            "citation_present": citation_present(item),
            "duplicate_branch": duplicate,
            "pin_conflict": pin_conflict,
            "route_issue": route_issue,
        })
    has_anchor = any(judgment["label"] == "supported" for judgment in judgments)
    scored_for_anchor = has_anchor and outcome in {"recommendations", "abstain"}
    first_hit = next((item["rank"] for item in details if item["judgment"] == "supported"), None)
    return {
        "query": query,
        "outcome": outcome,
        "reason": prediction.get("reason"),
        "routing": prediction.get("routing"),
        "latency_ms": prediction.get("latency_ms"),
        "has_reference_anchor": has_anchor,
        "anchor_check": ("hit" if first_hit else "miss") if scored_for_anchor else "not_scored",
        "first_supported_rank": first_hit,
        "top_one": details[0]["judgment"] if details else None,
        "top_result_travel_comparison": travel_details(query, results),
        "ignored_beyond_k": max(0, len(prediction.get("results", [])) - k),
        "results": details,
    }


def summarize(cases):
    outcomes = Counter(case["outcome"] for case in cases)
    ranked = [row for case in cases for row in case["results"]]
    top = Counter(case["top_one"] for case in cases if case["top_one"])
    labels = Counter(row["judgment"] for row in ranked)
    anchors = Counter(case["anchor_check"] for case in cases)
    latency = [case["latency_ms"] for case in cases if case["latency_ms"] is not None]
    travel = Counter(row["facility"].get("travel_basis") or "unreported" for row in ranked)
    return {
        "queries": len(cases),
        "outcomes": {item: outcomes[item] for item in sorted(OUTCOMES | {"missing_output"})},
        "queries_with_reference_anchor": sum(case["has_reference_anchor"] for case in cases),
        "anchor_hits": anchors["hit"],
        "anchor_queries_scored": anchors["hit"] + anchors["miss"],
        "top_one": {item: top[item] for item in sorted(LABELS | {"unreviewed"})},
        "ranked_results": len(ranked),
        "result_judgments": {item: labels[item] for item in sorted(LABELS | {"unreviewed"})},
        "citations_present": sum(row["citation_present"] for row in ranked),
        "duplicate_results": sum(row["duplicate_branch"] for row in ranked),
        "pin_conflicts": sum(row["pin_conflict"] for row in ranked),
        "route_issues": sum(row["route_issue"] is not None for row in ranked),
        "travel_bases": dict(sorted(travel.items())),
        "latency_n": len(latency),
        "median_latency_ms": median(latency) if latency else None,
    }


def score(queries, reference, payload, k):
    metadata, predictions = load_predictions(payload, queries)
    cases = [assess(query, predictions.get(key), reference.get(key, ([], {})), k)
             for key, query in queries.items()]
    groups = {"all": summarize(cases)}
    for field, values in (("stratum", ("urban", "rural")), ("suite", ("regression", "evidence_panel"))):
        for value in values:
            groups[value] = summarize([case for case in cases if case["query"][field] == value])
    return {
        "run": metadata,
        "missing_run_metadata": [field for field in RUN_FIELDS if not present(metadata.get(field))],
        "groups": groups,
        "queries": cases,
    }


def compare(current, baseline):
    pairs = zip(current["queries"], baseline["queries"], strict=True)
    counts = Counter()
    for new, old in pairs:
        if "not_scored" not in (new["anchor_check"], old["anchor_check"]):
            counts[(old["anchor_check"], new["anchor_check"])] += 1
    return {
        "queries_scored_for_anchors_by_both": sum(counts.values()),
        "baseline_miss_to_current_hit": counts[("miss", "hit")],
        "baseline_hit_to_current_miss": counts[("hit", "miss")],
        "both_hit": counts[("hit", "hit")],
        "both_miss": counts[("miss", "miss")],
    }


def display(report):
    for name in ("current", "baseline"):
        if name not in report:
            continue
        run = report[name]
        print(f"{name.title()} run: {run['run'].get('id', '(unnamed)')}  |  Top {report['top_k']}")
        if run["missing_run_metadata"]:
            print("Missing run metadata:", ", ".join(run["missing_run_metadata"]))
        for group, stats in run["groups"].items():
            counts, top = stats["outcomes"], stats["top_one"]
            unknown = top["unreviewed"] + top["unresolved"]
            print(
                f"  {group:14} {stats['queries']:2} queries | {counts['recommendations']} answered, "
                f"{counts['abstain']} abstained, {counts['unsupported_query']} unsupported inputs, "
                f"{counts['error']} errors, {counts['missing_output']} missing | "
                f"anchor hits {stats['anchor_hits']}/{stats['anchor_queries_scored']} scored "
                f"({stats['queries_with_reference_anchor']} have anchors) | "
                f"top-1 supported/unsupported/unknown {top['supported']}/{top['unsupported']}/{unknown} | "
                f"citations present {stats['citations_present']}/{stats['ranked_results']}"
            )
    if "paired_anchor_changes" in report:
        print("Paired anchor checks:", json.dumps(report["paired_anchor_changes"], sort_keys=True))


def main():
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=folder / "queries.json")
    parser.add_argument("--references", type=Path, default=folder / "references.json")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="Optional ranked results from a comparison method.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, help="Write the full evaluation and input hashes as JSON.")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive.")
    try:
        queries, query_hash = read_json(args.queries)
        queries = indexed(queries, "id", "queries")
        validate_queries(queries)
        reference, reference_hash = read_json(args.references)
        reference = reference_index(reference, queries)
        predictions, predictions_hash = read_json(args.predictions)
        report = {
            "top_k": args.top_k,
            "sha256": {"queries": query_hash, "references": reference_hash, "predictions": predictions_hash},
            "current": score(queries, reference, predictions, args.top_k),
        }
        if args.baseline:
            baseline, baseline_hash = read_json(args.baseline)
            report["sha256"]["baseline"] = baseline_hash
            report["baseline"] = score(queries, reference, baseline, args.top_k)
            report["paired_anchor_changes"] = compare(report["current"], report["baseline"])
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, TypeError) as error:
        parser.error(str(error))
    display(report)


if __name__ == "__main__":
    sys.exit(main())
