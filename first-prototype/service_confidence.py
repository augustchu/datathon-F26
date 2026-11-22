"""Score documentary support for a requested service at one facility branch."""

from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlparse


SERVICE_EVIDENCE_POINTS = {
    "corroborated": 80,
    "hospital_site": 75,
    "directory": 70,
    "record_listing": 50,
    "mention": 15,
}
RECORD_TEXT_KINDS = {"record_listing", "mention"}
SERVICE_EVIDENCE_LABELS = {
    "corroborated": "Hospital source and independent corroboration",
    "hospital_site": "Listed by hospital",
    "directory": "Listed in a reviewed directory",
    "record_listing": "Listed in facility record",
    "mention": "Mentioned in facility record",
    "none": "Not expected to offer",
    "conflict": "Conflicting service evidence",
}
PIN_PATTERN = re.compile(r"([1-9][0-9]{5})(?:\.0)?")


def branch_pin(value):
    match = PIN_PATTERN.fullmatch(str(value).strip())
    return match[1] if match else ""


def load_reviewed_service_evidence(path):
    """Load reviewed claims keyed to the canonical release branch ID, PIN and service."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("Reviewed service evidence must be a JSON list.")
    claims = defaultdict(list)
    for row in records:
        required = ("facility_id", "pin", "service", "publisher_key", "source_url", "excerpt", "source_kind")
        if not isinstance(row, dict) or any(not str(row.get(key) or "").strip() for key in required):
            raise ValueError("Reviewed service evidence has a missing claim or branch field.")
        if row["source_kind"] not in {"hospital_site", "directory"} or row.get("stance", "supports") not in {"supports", "contradicts"}:
            raise ValueError("Reviewed service evidence has an invalid source kind or stance.")
        pin = branch_pin(row["pin"])
        url = urlparse(row["source_url"])
        if not pin or url.scheme not in {"https", "http"} or not url.hostname:
            raise ValueError("Reviewed service evidence requires a valid branch PIN and source URL.")
        start, end = (date.fromisoformat(row[field]) if row.get(field) else None
                      for field in ("valid_from", "valid_through"))
        if start and end and start > end:
            raise ValueError("Reviewed service evidence has reversed validity dates.")
        key = (row["facility_id"].strip(), pin, row["service"].strip())
        claims[key].append({**row, "valid_from": start, "valid_through": end})
    return dict(claims)


def reviewed_service_status(claims, as_of):
    """Return the best active support and its exact citations, with conflicts taking priority."""
    active = [claim for claim in claims
              if (claim["valid_from"] is None or claim["valid_from"] <= as_of)
              and (claim["valid_through"] is None or as_of <= claim["valid_through"])]
    if any(claim.get("stance") == "contradicts" for claim in active):
        return "conflict", []
    official = [claim for claim in active if claim["source_kind"] == "hospital_site"]
    if official:
        publisher = official[0]["publisher_key"]
        independent = next((claim for claim in active if claim["publisher_key"] != publisher), None)
        return ("corroborated", [official[0], independent]) if independent else ("hospital_site", official[:1])
    return ("directory", active[:1]) if active else ("none", [])


def service_confidence(kind, branch_points):
    """No service evidence earns zero overall, including for an exact branch match."""
    if kind not in SERVICE_EVIDENCE_POINTS:
        return 0, SERVICE_EVIDENCE_LABELS[kind]
    score = SERVICE_EVIDENCE_POINTS[kind] + branch_points
    return score, "High" if score >= 90 else "Moderate" if kind == "record_listing" else "Limited"


def service_match(kind):
    """Rate how directly the record matches the service, independent of source verification."""
    if kind not in SERVICE_EVIDENCE_POINTS:
        return 0
    return 40 if kind == "mention" else 100
