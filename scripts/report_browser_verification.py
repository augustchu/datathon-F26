"""Compare manually recorded live-browser observations to fixed random samples."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data/external"
KEYS = {"nabh": "source_record_id", "nqas": "source_serial", "eraktkosh": "hospital_code"}


def main():
    audit = json.loads((ROOT / "verification/browser_observations.json").read_text())
    samples = {}
    for source in KEYS:
        sample = json.loads((ROOT / "verification" / f"{source}_sample.json").read_text())
        actual_hash = hashlib.sha256((ROOT / source / "facilities.jsonl").read_bytes()).hexdigest()
        if actual_hash != sample["universe_sha256"]:
            raise ValueError(f"{source}: source extract changed after sampling")
        samples[source] = {str(row[KEYS[source]]): row for row in sample["rows"]}
    checks = []
    for observation in audit["observations"]:
        row = samples[observation["source"]][observation["key"]]
        comparisons = {field: {"expected": row.get(field), "observed": value,
                              "match": str(row.get(field, "")).strip() == str(value).strip()}
                       for field, value in observation["observed_values"].items()}
        status = observation["status"]
        if comparisons:
            status = "match" if all(c["match"] for c in comparisons.values()) else "mismatch"
        checks.append({**observation, "facility_name": row["facility_name"],
                       "status": status, "field_comparisons": comparisons})
    if len(checks) != sum(len(s) for s in samples.values()):
        raise ValueError("Every sampled record must have a reported outcome")
    report = {"seed": 20261121, "sample_size_per_source": 3,
              "sampling": "Uniform row sample without replacement within each saved source extract; no substitution for unavailable lookups",
              "matched_records": sum(c["status"] == "match" for c in checks),
              "mismatched_records": sum(c["status"] == "mismatch" for c in checks),
              "unverified_records": sum(c["status"] not in ("match", "mismatch") for c in checks),
              "matched_field_comparisons": sum(v["match"] for c in checks for v in c["field_comparisons"].values()),
              "scope": audit["scope"], "checks": checks}
    (ROOT / "verification/report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    text = ["Browser verification of supplemental healthcare directories", "", audit["scope"], "",
            f"Seed: 20261121. Three random records per saved source extract.",
            f"Matched: {report['matched_records']}; mismatched: {report['mismatched_records']}; unverified: {report['unverified_records']}.", ""]
    for c in checks:
        text.append(f"{c['source']} | {c['facility_name'].strip()} | {c['status']}")
        if c.get("note"):
            text.append("  " + c["note"])
    (ROOT / "verification/report.txt").write_text("\n".join(text) + "\n")
    print(json.dumps({k:v for k,v in report.items() if k != "checks"}, indent=2))


if __name__ == "__main__":
    main()
