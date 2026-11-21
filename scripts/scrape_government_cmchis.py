#!/usr/bin/env python3
"""Collect the public CMCHIS hospital export used by its Download Excel button.

Only the requested seven fields enter the deliverable tables. Source snapshots,
their hashes and extraction checks remain separate under raw/ and audit/.
No credentials, authenticated APIs, or cross-source entity matches are used.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/government_sources"
RAW = OUT / "raw/cmchis"
REPORT = OUT / "audit/cmchis_report.json"
LISTING = "https://www.cmchistn.com/empanelment/hospital-list"
FIELDS = ["facility_name", "phone_numbers", "email_addresses", "location",
          "services", "validity_dates", "source_links"]
META_KEYS = {"s_no", "tpa", "entity_code", "hospital_name", "address", "district",
             "subentities", "single_multi", "pvt_govt", "directrate", "edc",
             "edc_date", "year", "grade"}


def now():
    return datetime.now(timezone.utc).isoformat()


def build_rows(source_rows, endpoint):
    rows = []
    for record in source_rows:
        # This is the same conversion used by the site's profile modal:
        # non-metadata fields whose value is exactly true become service labels.
        services = [key.replace("_", " ").title() for key, value in record.items()
                    if key not in META_KEYS and value is True]
        address = str(record.get("address") or "").strip()
        district = str(record.get("district") or "").strip()
        location = "; ".join(part for part in [address, f"District: {district}" if district else ""] if part)
        entity = str(record.get("entity_code") or "").strip()
        evidence_url = endpoint + "?" + urlencode({"search": entity}) if entity else endpoint + "?json=true"
        rows.append({
            "facility_name": str(record.get("hospital_name") or "").strip(),
            # Neither the directory response nor its hospital profile publishes
            # facility-level telephone/email. The site's footer contacts belong
            # to the programme office and must not be copied to every facility.
            "phone_numbers": [],
            "email_addresses": [],
            "location": location,
            "services": services,
            # edc_date is a committee/empanelment event, not a validity period.
            "validity_dates": [],
            "source_links": [LISTING, evidence_url],
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Rebuild from saved source responses without network calls")
    parser.add_argument("--refresh", action="store_true", help="Refresh public snapshots rather than reuse them")
    args = parser.parse_args()
    if args.offline and args.refresh:
        parser.error("--offline and --refresh are mutually exclusive")
    RAW.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "DatathonGovernmentDirectoryResearch/1.0"})
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.5,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])))
    downloads = []

    def fetch(url, filename):
        path = RAW / filename
        cache = path.exists() and not args.refresh
        if not cache:
            if args.offline:
                raise FileNotFoundError(f"Offline snapshot missing: {path}")
            response = session.get(url, timeout=(15, 60))
            response.raise_for_status()
            path.write_bytes(response.content)
        content = path.read_bytes()
        downloads.append({"url": url, "path": str(path.relative_to(ROOT)),
                          "sha256": hashlib.sha256(content).hexdigest(),
                          "bytes": len(content), "used_cache": cache,
                          "snapshot_file_mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
        return content

    html = fetch(LISTING, "hospital-list.html").decode("utf-8")
    soup = BeautifulSoup(html, "html.parser")
    assets = [urljoin(LISTING, script["src"]) for script in soup.select("script[src]")
              if "/assets/index-" in script["src"]]
    if len(assets) != 1:
        raise ValueError("Expected one CMCHIS application asset; review changed website.")
    javascript = fetch(assets[0], "application.js").decode("utf-8")
    match = re.search(r'https://[^"\s]+/api/v1/', javascript)
    if not match or "tpa-hospitals-v2/" not in javascript:
        raise ValueError("Public directory endpoint not found in the current application.")
    endpoint = urljoin(match.group(), "tpa-hospitals-v2/")
    first = json.loads(fetch(endpoint + "?page=1&page_size=10", "page1.json"))
    export = json.loads(fetch(endpoint + "?json=true", "export.json"))
    source_rows = export["results"]
    if len(source_rows) != export["count"] or export["count"] != first["count"]:
        raise ValueError("Export count and directory count disagree; do not claim a complete extract.")
    if source_rows[:len(first["results"])] != first["results"]:
        raise ValueError("Export does not agree with the directory's first page.")
    rows = build_rows(source_rows, endpoint)
    if not rows or any(not row["facility_name"] for row in rows):
        raise ValueError("Missing facility names in the public export.")
    for row in rows:
        assert list(row) == FIELDS
        assert all(isinstance(row[field], list) for field in ("phone_numbers", "email_addresses", "services", "validity_dates", "source_links"))
    for suffix in ("jsonl", "csv"):
        path = OUT / f"cmchis.{suffix}"
        if suffix == "jsonl":
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        else:
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k, v in row.items()})
    report = {
        "source": "Tamil Nadu CMCHIS public empanelled hospital list",
        "processed_at_utc": now(), "listing_url": LISTING, "public_export_url": endpoint + "?json=true",
        "method": "requests + BeautifulSoup asset discovery; official public JSON export used by website Download Excel button",
        "advertised_count": export["count"], "extracted_rows": len(rows), "complete_relative_to_current_export": True,
        "unique_source_entity_codes": len({r.get("entity_code") for r in source_rows}),
        "coverage": {field: sum(bool(row[field]) for row in rows) for field in FIELDS},
        "source_missing_street_addresses": sum(not str(r.get("address") or "").strip() for r in source_rows),
        "checks": {"export_matches_count": True, "first_page_matches_export": True,
                   "only_requested_fields": True, "services_follow_public_UI_boolean_mapping": True},
        "limitations": [
            "No facility telephone, email or validity period is published in this directory export/profile; these fields are empty.",
            "EDC committee dates and administrative year are not validity dates and are excluded.",
            "Source includes hospital and non-hospital participating units, including welfare offices; names are preserved without relabeling.",
            "Specialties are scheme-listed claims. Nephrology does not establish dialysis; empty service list does not prove absence of services.",
            "No independent corroboration, current-availability checks or statistical validity audit was performed.",
            "Per-record evidence URLs use the public entity-code search endpoint; human UI profiles have no separate permanent URL.",
        ],
        "downloads": downloads,
    }
    # Browser observations are maintained separately from reproducible extraction.
    observations = RAW / "browser_observations.json"
    if observations.exists():
        report["browser_spot_checks"] = json.loads(observations.read_text())
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(rows), "coverage": report["coverage"], "report": str(REPORT)}, indent=2))


if __name__ == "__main__":
    main()
