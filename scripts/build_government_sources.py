"""Combine the five government extracts without cross-source entity matching."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/government_sources"
SOURCES = ["bis_cghs", "esic", "cmchis", "rghs", "kerala_sha"]
FIELDS = ["facility_name", "phone_numbers", "email_addresses", "location",
          "services", "validity_dates", "source_links"]
LIST_FIELDS = set(FIELDS) - {"facility_name", "location"}


def read_source(source):
    path = OUT / f"{source}.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    with (OUT / f"{source}.csv").open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"{source}: unexpected CSV fields {reader.fieldnames}")
        csv_rows = []
        for row in reader:
            csv_rows.append({key: json.loads(value) if key in LIST_FIELDS else value
                             for key, value in row.items()})
    if records != csv_rows:
        raise ValueError(f"{source}: CSV and JSONL disagree")
    for number, record in enumerate(records, 1):
        if set(record) != set(FIELDS):
            raise ValueError(f"{source} row {number}: unexpected fields {list(record)}")
        for key in FIELDS:
            value = record[key]
            if key in LIST_FIELDS:
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise TypeError(f"{source} row {number}: {key} must be a list of strings")
            elif not isinstance(value, str):
                raise TypeError(f"{source} row {number}: {key} must be a string")
        if not record["facility_name"].strip() or not record["source_links"]:
            raise ValueError(f"{source} row {number}: missing facility name or provenance")
        if any(not url.startswith(("https://", "http://")) for url in record["source_links"]):
            raise ValueError(f"{source} row {number}: non-HTTP source URL")
    return records


def main():
    combined, counts, coverage, offsets = [], {}, {}, {}
    for source in SOURCES:
        records = read_source(source)
        offsets[source] = {"start_zero_based": len(combined), "row_count": len(records)}
        combined.extend(records)
        counts[source] = len(records)
        coverage[source] = {key: sum(bool(r[key]) for r in records) for key in FIELDS}
    (OUT / "government_facilities.jsonl").write_text(
        "".join(json.dumps({key: record[key] for key in FIELDS}, ensure_ascii=False) + "\n" for record in combined)
    )
    with (OUT / "government_facilities.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for record in combined:
            writer.writerow({key: json.dumps(record[key], ensure_ascii=False) if key in LIST_FIELDS else record[key]
                             for key in FIELDS})
    report = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(combined), "source_row_counts": counts,
        "source_row_offsets": offsets, "field_presence_counts": coverage,
        "checks": {"exact_seven_field_schema": True, "list_types_valid": True,
                   "per_source_csv_jsonl_agreement": True, "names_and_source_links_present": True},
        "notes": ["Source records are concatenated, not matched or deduplicated across sources.",
                  "Zero rows can mean access failure; consult each source report.",
                  "Presence of a validity date does not mean the agreement is currently valid.",
                  "Services retain published scope/specialty text, not verified operational availability."],
    }
    (OUT / "audit/combined_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"rows": len(combined), "source_row_counts": counts, "checks": report["checks"]}))


if __name__ == "__main__":
    main()
