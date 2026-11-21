"""Extract the seven requested fields from Kerala SHA's published table.

Default operation regenerates from the browser-observed table snapshot. Use
--refresh to try HTTP/BeautifulSoup collection. A failed refresh raises an error
and does not silently replace it with cached data. Browser fallback rows were
captured from rendered DOM cells, not inferred from search snippets.
"""
import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/government_sources"
RAW = OUT / "raw/kerala"
SOURCE = "https://sha.kerala.gov.in/?page_id=5749&lang=en"
FIELDS = ["facility_name", "phone_numbers", "email_addresses", "location",
          "services", "validity_dates", "source_links"]
HEADER = ["Hospital", "District", "Contact Number", "Email", "Address"]


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def parse_rows(rows):
    if [clean(x) for x in rows[0]] != HEADER:
        raise ValueError("Unexpected source table headers; review the source before extraction.")
    records = []
    for number, row in enumerate(rows[1:], 1):
        if len(row) != 5:
            raise ValueError(f"Source row {number} has {len(row)} cells, expected 5.")
        name, district, phone, email, address = map(clean, row)
        services = []
        # The listing explicitly names this service only for NISH. Programme
        # membership is not treated as proof of implantation surgery elsewhere.
        qualifier = " (Audio Verbal Therapy)"
        if name.endswith(qualifier):
            name = name[:-len(qualifier)]
            services = ["Audio Verbal Therapy"]
        records.append({
            "facility_name": name,
            # Preserve spelling, including published O495 typo; do not guess corrections.
            "phone_numbers": [x.strip() for x in phone.split(",") if x.strip()],
            "email_addresses": re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email),
            "location": f"{address}; District: {district}",
            "services": services,
            "validity_dates": [],
            "source_links": [SOURCE],
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    snapshot = RAW / "browser_table_rows.json"
    capture_method = "rendered_browser_DOM_table_cells"
    if args.refresh:
        response = requests.get(SOURCE, timeout=(15, 60))
        response.raise_for_status()
        (RAW / "hospital_list.html").write_bytes(response.content)
        soup = BeautifulSoup(response.content, "html.parser")
        candidates = []
        for table in soup.find_all("table"):
            rows = [[cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
                    for tr in table.find_all("tr")]
            if rows and [clean(x) for x in rows[0]] == HEADER:
                candidates.append(rows)
        if len(candidates) != 1:
            raise ValueError(f"Expected one matching hospital table, found {len(candidates)}.")
        snapshot = RAW / "http_table_rows.json"
        snapshot.write_text(json.dumps(candidates[0], ensure_ascii=False, indent=2) + "\n")
        capture_method = "HTTP_HTML_BeautifulSoup"
    rows = json.loads(snapshot.read_text())
    records = parse_rows(rows)
    (OUT / "kerala_sha.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
    with (OUT / "kerala_sha.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                             for key, value in row.items()})
    report = {
        "source": SOURCE, "row_count": len(records), "capture_method": capture_method,
        "browser_capture_date": "2026-11-21" if not args.refresh else None,
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_path": str(snapshot.relative_to(ROOT)),
        "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "http_access_attempt": "403 Forbidden on direct requests; normal public browser page succeeded" if not args.refresh else "200",
        "field_presence": {key: sum(bool(row[key]) for row in records) for key in FIELDS},
        "limitations": [
            "No validity dates are published in this table.",
            "Only NISH has an explicit facility-specific service label; the other ten services arrays are empty.",
            "Baby Memorial's second phone is published as O495-2777777 (letter O), preserved without guessing.",
            "Ascent Hospital has district Malappuram but an address in Kozhikode; both source claims are preserved.",
            "The three Government Medical College rows are different locations and remain separate.",
            "This is a small programme list, not a census of Kerala facilities.",
        ],
    }
    (OUT / "audit").mkdir(exist_ok=True)
    (OUT / "audit/kerala_sha_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"source": "kerala_sha", "rows": len(records), "capture_method": capture_method}))


if __name__ == "__main__":
    main()
