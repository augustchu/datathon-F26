#!/usr/bin/env python3
"""Retrieve the public RGHS hospital list and retain the requested seven fields.

Current run status is in data/government_sources/audit/rghs_report.json. The
source host timed out during collection; an empty extract is not a hospital
census. PDF parsing is deliberately header-driven and fails closed when its
columns cannot be identified. Use --pdf with an official downloaded PDF to
retry without network access. No login or access-control bypass is used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/government_sources"
HOME = "https://rghs.rajasthan.gov.in/RGHS/home/"
CURRENT = "https://rghs.rajasthan.gov.in/RGHS/downloadHomeDocument?filename=%2Fappdata26_02%2FRghsHome%2F2026%2F2026-11-18%2FList_of_hospitals_empaneled_under_rghs_nov_26.pdf"
LEGACY = "https://rghs.rajasthan.gov.in/List_of_empaneled_hospitals_under_RGHS.pdf"
FIELDS = ["facility_name", "phone_numbers", "email_addresses", "location", "services", "validity_dates", "source_links"]
ARRAYS = set(FIELDS) - {"facility_name", "location"}


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def header_map(row):
    mapping = {}
    for i, value in enumerate(row):
        value = clean(value).lower()
        if "hospital" in value and "name" in value:
            mapping["name"] = i
        elif "address" in value:
            mapping["address"] = i
        elif "district" in value:
            mapping["district"] = i
        elif value == "city":
            mapping["city"] = i
        elif "special" in value:
            mapping["services"] = i
        elif "valid" in value:
            mapping["validity"] = i
        elif "contact" in value or "mobile" in value or "phone" in value:
            mapping["phone"] = i
        elif "mail" in value:
            mapping["email"] = i
    return mapping if {"name", "address", "services"} <= set(mapping) else None


def parse_pdf(path, source_url):
    import pdfplumber
    records, issues, pages = [], [], []
    previous_map, previous_width = None, None
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            page_count = 0
            for table in tables:
                mapping, width = previous_map, previous_width
                for raw in table:
                    row = [clean(cell) for cell in raw]
                    found = header_map(row)
                    if found:
                        mapping, width = found, len(row)
                        previous_map, previous_width = found, len(row)
                        continue
                    if mapping is None or len(row) != width:
                        continue
                    # RGHS tables have a leading serial number. Ambiguous fragments
                    # are reported, never merged into a neighboring hospital.
                    if not re.fullmatch(r"\d+[.)]?", row[0]):
                        if any(row):
                            issues.append({"page": page_number, "reason": "Unparsed non-numbered table row or continuation", "row": row})
                        continue
                    get = lambda key: row[mapping[key]] if key in mapping else ""
                    if not get("name"):
                        issues.append({"page": page_number, "reason": "Missing facility name", "row": row})
                        continue
                    contact = " ".join([get("phone"), get("email")])
                    emails = list(dict.fromkeys(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", contact)))
                    phones = list(dict.fromkeys(clean(x) for x in re.findall(r"(?<!\d)(?:\+91[ -]?)?(?:0\d{2,4}[ -]?)?\d[\d -]{7,}\d(?!\d)", get("phone"))
                                                if 8 <= len(re.sub(r"\D", "", x)) <= 15))
                    location = "; ".join(dict.fromkeys(x for x in [get("address"), get("city"), get("district")] if x))
                    services = [clean(x) for x in re.split(r"\s*\|\|\s*", get("services")) if clean(x)]
                    records.append({"facility_name": get("name"), "phone_numbers": phones,
                                    "email_addresses": emails, "location": location,
                                    "services": services,
                                    "validity_dates": [get("validity")] if get("validity") else [],
                                    "source_links": [source_url + f"#page={page_number}"]})
                    page_count += 1
            pages.append({"page": page_number, "rows": page_count, "tables": len(tables)})
    if not records:
        raise ValueError("No hospital rows with recognized headers were extracted; inspect the PDF before changing parsing rules.")
    return records, {"page_counts": pages, "unparsed_table_rows": issues,
                     "parser_status": "partial_review_required" if issues else "parsed_headers"}


def write_records(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "rghs.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (OUT / "rghs.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if k in ARRAYS else v for k, v in row.items()})


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--pdf", type=Path, help="Official PDF already downloaded locally")
    args.add_argument("--source-url", default=CURRENT)
    args.add_argument("--record-existing-failures", action="store_true", help="Build an honest zero-row report from saved retrieval attempts")
    options = args.parse_args()
    raw = OUT / "raw/rghs"
    audit = OUT / "audit"
    raw.mkdir(parents=True, exist_ok=True)
    audit.mkdir(parents=True, exist_ok=True)
    report = {"source": "Rajasthan Government Health Scheme", "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
              "status": "access_failed", "rows": 0, "attempts": [], "limitations": []}
    rows = []
    pdf_path, source_url = options.pdf, options.source_url
    if options.record_existing_failures:
        report["attempts"] = json.loads((raw / "retrieval_attempts.json").read_text())
        report["browser_check"] = "Normal Chrome navigation also timed out; the tab remained blank."
    elif pdf_path is None:
        urls = [CURRENT, LEGACY]
        try:
            response = requests.get(HOME, timeout=(12, 35))
            report["attempts"].append({"url": HOME, "status": response.status_code})
            response.raise_for_status()
            (raw / "homepage.html").write_bytes(response.content)
            soup = BeautifulSoup(response.text, "html.parser")
            discovered = [urljoin(HOME, a["href"]) for a in soup.select("a[href]")
                          if "empanelled hospitals list" in a.get_text(" ", strip=True).lower()]
            urls = list(dict.fromkeys(discovered + urls))
        except requests.RequestException as exc:
            report["attempts"].append({"url": HOME, "error": str(exc)})
        for url in urls:
            try:
                response = requests.get(url, timeout=(12, 35))
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("Response is not a PDF; not parsed as hospital data.")
                pdf_path, source_url = raw / "hospital_list.pdf", url
                pdf_path.write_bytes(response.content)
                report["attempts"].append({"url": url, "status": response.status_code, "bytes": len(response.content)})
                break
            except (requests.RequestException, ValueError) as exc:
                report["attempts"].append({"url": url, "error": str(exc)})
    if pdf_path is not None:
        try:
            rows, parsed = parse_pdf(pdf_path, source_url)
            report.update(parsed)
            report.update(status="partial_review_required" if parsed["unparsed_table_rows"] else "extracted", rows=len(rows),
                          source_url=source_url, sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest())
            if source_url == LEGACY:
                report["limitations"].append("Used the legacy published PDF; do not label it the current November 2026 list.")
        except Exception as exc:
            report.update(status="parse_failed", error=str(exc))
    if not rows:
        report["limitations"].append("No RGHS facility records collected. Empty output means source inaccessible or parsing unsuccessful, not that no facilities exist.")
        report["limitations"].append("The header-driven PDF parser could not be verified against a downloaded source PDF in this run.")
    write_records(rows)
    (audit / "rghs_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": report["status"], "rows": len(rows)}))


if __name__ == "__main__":
    main()
