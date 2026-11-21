"""Choose reproducible, uniform row samples from downloaded supplemental data.

This selects rows for browser review; it does not itself validate any website.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data/external"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+", choices=["nabh", "nqas", "eraktkosh"])
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    (ROOT / "verification").mkdir(exist_ok=True)
    for source in args.sources:
        path = ROOT / source / "facilities.jsonl"
        payload = path.read_bytes()
        rows = [json.loads(line) for line in payload.decode().splitlines() if line.strip()]
        if len(rows) < args.count:
            raise ValueError(f"{source}: only {len(rows)} rows available")
        indexes = random.Random(args.seed).sample(range(len(rows)), args.count)
        sample = {"source": source, "seed": args.seed, "population_size": len(rows),
                  "sample_size": args.count, "sampling": "uniform rows without replacement from this saved extract",
                  "universe_sha256": hashlib.sha256(payload).hexdigest(), "zero_based_row_indexes": indexes,
                  "rows": [rows[index] for index in indexes],
                  "scope_note": "A spot check of extraction fidelity; not proof of clinical accuracy, source independence, or all-row correctness."}
        dest = ROOT / "verification" / f"{source}_sample.json"
        dest.write_text(json.dumps(sample, ensure_ascii=False, indent=2))
        print(json.dumps({"source": source, "population_size": len(rows), "sample_file": str(dest),
                          "names": [row.get("facility_name") for row in sample["rows"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
