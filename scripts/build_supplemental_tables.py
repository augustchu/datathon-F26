#!/usr/bin/env python3
"""Build task-focused supplemental tables from the saved new-source extracts.

This is an offline projection, not a new scrape. It never rewrites raw exports.
Run: python3 scripts/build_supplemental_tables.py [--as-of 2026-11-21]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = ROOT / "data/external"
OUTPUT = ROOT / "data/supplemental"


def location(row):
    """Preserve plausible source coordinates without claiming verification."""
    values = [row.get("latitude"), row.get("longitude")]
    if any(value is None or value == "" for value in values):
        return {"latitude": None, "longitude": None,
                "coordinate_quality": "missing_or_ambiguous", "location_verified": False}
    try:
        latitude, longitude = [float(value) for value in values]
    except (ValueError, TypeError):
        quality = "invalid_format"
    else:
        if not all(math.isfinite(value) for value in (latitude, longitude)):
            quality = "invalid_format"
        elif latitude == 0 or longitude == 0:
            quality = "zero_placeholder"
        elif not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            quality = "invalid_range"
        else:
            return {"latitude": latitude, "longitude": longitude,
                    "coordinate_quality": "source_coordinates_unverified", "location_verified": False}
    return {"latitude": None, "longitude": None,
            "coordinate_quality": quality, "location_verified": False}


def nabh(row, as_of):
    # The full raw evidence_text includes phone numbers; the exact status field
    # is the relevant evidence excerpt for this table's certification claim.
    return {
        "source_record_id": row["source_record_id"],
        "facility_name": row["facility_name"],
        "address": row["address"],
        **location(row),
        "accreditation_number": row["accreditation_number"],
        "certification_status": row["status_text"],
        "evidence_text": row["status_text"],
        "source_url": row["source_url"],
        "retrieved_at_utc": row["retrieved_at_utc"],
    }


def nqas(row, as_of):
    validity_iso = row.get("validity_date_iso") or None
    valid_until = date.fromisoformat(validity_iso) if validity_iso else None
    return {
        "source_record_id": row["record_id"],
        "facility_name": row["facility_name"],
        "state": row["state"],
        "certification_status": row["certification_status"],
        "validity_date_text": row["validity_date_raw"],
        "validity_date_iso": validity_iso,
        "validity_expired": valid_until < as_of if valid_until else None,
        "evidence_text": row["certification_status"],
        "source_url": row["source_url"],
        "retrieved_at_utc": row["scraped_at_utc"],
    }


def eraktkosh(row, as_of):
    return {
        "source_record_id": str(row["hospital_code"]),
        "facility_name": row["facility_name"],
        "address": row["address"],
        "state": row["state_name"],
        **location(row),
        "facility_type": row["facility_type"],
        "evidence_text": row["facility_type"],
        "source_url": row["source_url"],
        "retrieved_at_utc": row["fetched_at_utc"],
    }


def write_table(source, rows):
    jsonl = OUTPUT / f"{source}.jsonl"
    csv_path = OUTPUT / f"{source}.csv"
    jsonl.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (jsonl, csv_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 11, 21))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    definitions = {
        "source_record_id": "Existing source/export record identifier. NQAS uses a hash of the source row; NABH uses a name/address/accreditation hash; eRaktKosh uses its hospital code. These are not cross-source facility IDs.",
        "facility_name": "Verbatim facility name from the saved source extract.",
        "address": "Verbatim source address; no city, district, state or PIN inferred from it.",
        "state": "State explicitly assigned by the source table caption or the source state-filter response. Not inferred from address text.",
        "latitude": "Source latitude in decimal degrees, or NULL when missing, zero-placeholder, nonnumeric or outside global coordinate bounds. Not geographically verified.",
        "longitude": "Source longitude in decimal degrees with the same NULL handling as latitude.",
        "coordinate_quality": "source_coordinates_unverified, missing_or_ambiguous, zero_placeholder, invalid_format or invalid_range. A plausible number does not verify the facility's location.",
        "location_verified": "Always false: no independent location verification has been performed.",
        "accreditation_number": "Verbatim NABH directory identifier. The source may describe accreditation, certification, empanelment or renewal.",
        "certification_status": "Exact status text asserted by NABH or NHSRC, preserved without treating all statuses as accreditation.",
        "validity_date_text": "Exact NHSRC validity-date text in DD/MM/YYYY format.",
        "validity_date_iso": "Parsed NHSRC date in YYYY-MM-DD format, or NULL if unavailable.",
        "validity_expired": f"True when the source's validity date is before {args.as_of.isoformat()}; false when on/after that date; NULL when unknown. False does not establish current certification or clinical availability.",
        "facility_type": "Exact eRaktKosh type field: Blood Bank or BSU (blood storage unit). This is a directory claim, not a clinical capability confirmation.",
        "evidence_text": "Exact relevant source-field excerpt: NABH status_text, NHSRC certification_status, or eRaktKosh facility_type. It is not a synthesized narrative. Name/address/date fields preserve their own source text separately.",
        "source_url": "Public directory citation. NHSRC pagination is retained in the URL where present; NABH and eRaktKosh records can be located by name or identifier in their lookup UI.",
        "retrieved_at_utc": "UTC retrieval timestamp from the original scrape, not the date of independent clinical verification.",
    }
    schema = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validity_assessed_as_of": args.as_of.isoformat(),
        "purpose": "Additional evidence for facility identity, location and explicitly recorded certification/blood-service type in the CMU datathon challenge.",
        "excluded_provided_sources": ["Virtue Foundation/FDR facility dataset", "India Post PIN directory", "NFHS-5", "geoBoundaries", "DataMeet"],
        "excluded_fields": ["emails", "phone numbers", "administrative codes other than source record IDs", "web implementation endpoints", "raw file paths", "map UI metadata", "unverified certificate-document contents"],
        "null_representation": {"jsonl": "JSON null", "csv": "empty cell"},
        "row_policy": "Source rows and order are retained; no entity deduplication, cross-source joins, geography imports or clinical-specialty inference.",
        "field_definitions": definitions,
        "sources": {},
    }
    limitations = {
        "nabh": [
            "Partial extract: 500 rows from 20 evenly spread pages of 21,609 advertised entries at collection time.",
            "One repeated name/address/accreditation identity is retained; rows are not guaranteed distinct facilities.",
            "The directory includes accreditation, certification, empanelment and renewal; migration can produce incomplete or inconsistent information.",
            "No clinical specialty is inferred from accreditation or facility name; certificate contents were not verified.",
            "State is not separately structured in this extract, so only the full source address is retained.",
            "Coordinates were associated by exact name and address in the source response, but their real-world accuracy remains unverified.",
        ],
        "nqas": [
            "22,015 displayed certification rows were collected, not a national census of facilities or a guarantee of distinct facility identities.",
            "Source is marked updated through 30 September 2026; includes expired and conditional entries.",
            "Validity is checked only against the fixed as-of date. Status and date do not establish current treatment availability or service scope.",
            "This table has no facility addresses, coordinates or specific-service evidence.",
        ],
        "eraktkosh": [
            "6,524 directory rows across all 36 state/UT options returned by the public lookup; this does not establish coverage of every real-world blood facility.",
            "Blood Bank and BSU are different source facility types and remain distinct.",
            "No blood inventory, appointment availability, or hospital transfusion capability is inferred.",
            "Zero coordinate placeholders are exported as NULL; original values remain in the external raw export.",
        ],
    }
    for source, project in [("nabh", nabh), ("nqas", nqas), ("eraktkosh", eraktkosh)]:
        input_path = EXTERNAL / source / "facilities.jsonl"
        data = input_path.read_bytes()
        original_rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
        rows = [project(row, args.as_of) for row in original_rows]
        if not rows:
            raise ValueError(f"No rows in {source}")
        expected_order = [row.get("source_record_id", row.get("record_id", str(row.get("hospital_code")))) for row in original_rows]
        assert [row["source_record_id"] for row in rows] == expected_order
        assert all(row["evidence_text"] in (original.get("status_text"), original.get("certification_status"), original.get("facility_type")) for row, original in zip(rows, original_rows))
        assert not any(field in rows[0] for field in ("email", "phone", "facility_phone", "raw_file", "raw_snapshot", "source_endpoint", "data_url", "district_code", "state_code"))
        hashes = write_table(source, rows)
        summary = {
            "row_count": len(rows), "unique_source_record_ids": len({row["source_record_id"] for row in rows}),
            "fields": list(rows[0]), "input_sha256": hashlib.sha256(data).hexdigest(),
            "output_sha256": hashes, "limitations": limitations[source],
        }
        if "coordinate_quality" in rows[0]:
            summary["coordinate_quality_counts"] = dict(Counter(row["coordinate_quality"] for row in rows))
        if source == "nqas":
            summary["validity_expired_rows"] = sum(row["validity_expired"] is True for row in rows)
            summary["validity_unknown_rows"] = sum(row["validity_expired"] is None for row in rows)
        if source == "eraktkosh":
            summary["facility_type_counts"] = dict(Counter(row["facility_type"] for row in rows))
        schema["sources"][source] = summary
        print(json.dumps({"source": source, **{key:value for key,value in summary.items() if key.endswith("count") or key.endswith("counts") or key.endswith("rows")}}, ensure_ascii=False))
    (OUTPUT / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
