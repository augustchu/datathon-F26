"""Summarize published service claims by branch, source independence and date."""

from collections import defaultdict
from datetime import date, datetime
import re
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from .data import CMCHIS_RECORD_ENDPOINT


VERIFICATION_COLUMNS = (
    "facility_id", "service", "verification_status", "verification_as_of",
    "supporting_publisher_count", "supporting_publishers", "supporting_record_ids", "supporting_urls",
    "contradicting_publishers", "contradicting_record_ids", "contradicting_urls",
    "unattributed_contradiction_record_ids",
    "inactive_record_ids", "future_record_ids", "expired_record_ids", "insufficient_record_ids",
    "evidence_scopes", "scope_caveat", "evidence",
)
REQUIRED_CLAIM_COLUMNS = {
    "facility_id", "service", "record_id", "stance", "excerpt", "source_url",
    "citation_quality", "scope", "validity",
}
PUBLISHED_VALIDITY = {"active", "unexpired", "undated"}
STATUS_BY_DATE = {"inactive", "future", "expired"}
INDIA_GOV_SUFFIXES = {"gov.in", "nic.in"}
MULTI_LABEL_SUFFIXES = INDIA_GOV_SUFFIXES | {
    "ac.in", "co.in", "edu.in", "ernet.in", "firm.in", "gen.in", "ind.in", "mil.in",
    "net.in", "org.in", "res.in", "co.uk", "org.uk", "com.au", "com.bd", "com.sg", "co.nz",
}
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
CMCHIS_ENDPOINT = urlsplit(CMCHIS_RECORD_ENDPOINT)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _date(value):
    if pd.api.types.is_scalar(value) and pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value))
    except ValueError:
        return None


def _validity(row, cutoff):
    supplied = _text(row.get("validity")).casefold()
    if supplied == "inactive":
        return supplied
    start_value, end_value = row.get("validity_start"), row.get("validity_end")
    start, end = _date(start_value), _date(end_value)
    if (_text(start_value) and start is None) or (_text(end_value) and end is None) or (start and end and end < start):
        return "unresolved_date"
    if start and cutoff < start:
        return "future"
    if end and cutoff > end:
        return "expired"
    if start and end:
        return "active"
    if supplied in STATUS_BY_DATE | {"active", "unexpired"}:
        return supplied
    if end:
        return "unexpired"
    notes = row.get("validity_text")
    notes = notes if isinstance(notes, (list, tuple)) else [notes]
    if start or any(_text(value) for value in notes):
        return "unresolved_date"
    return "undated" if supplied in {"", "unknown", "undated"} else "unresolved_date"


def _document(url):
    """Return the publication URL without its scheme or page fragment."""
    try:
        parsed = urlsplit(_text(url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.rstrip(".").removeprefix("www.")
        if not host or not all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
            return None
        return host, parsed.path or "/", parsed.query
    except ValueError:
        return None


def _publisher(host):
    labels = host.split(".")
    if len(labels) < 2 or labels[-1].isdigit():
        return ""
    suffix = ".".join(labels[-2:])
    if suffix in MULTI_LABEL_SUFFIXES:
        if len(labels) < 3:
            return ""
        # Consolidate the same agency or state slug under India's government
        # and NIC namespaces without merging separate agencies or states.
        family_suffix = "gov.in" if suffix in INDIA_GOV_SUFFIXES else suffix
        return f"{labels[-3]}.{family_suffix}"
    return suffix


def _direct_publisher(row):
    if _text(row.get("citation_quality")) != "direct":
        return ""
    document = _document(row.get("source_url"))
    if document is None:
        return ""
    source_urls = row.get("source_urls")
    if not isinstance(source_urls, (list, tuple)):
        source_urls = [_text(source_urls)]
    candidates = {_document(url) for url in source_urls if _text(url)}
    if candidates and document not in candidates:
        return ""
    if len(candidates) > 1:
        # The extractor retained a specific facility search URL for CMCHIS and
        # a generic directory link. Only the facility-level citation is counted.
        host, path, query = document
        if not (
            _text(row.get("source")) == "cmchis"
            and host == CMCHIS_ENDPOINT.hostname and path == CMCHIS_ENDPOINT.path
            and len(parse_qs(query).get("search", [])) == 1
        ):
            return ""
    return _publisher(document[0])


def _claim_evidence(row, cutoff):
    validity = _validity(row, cutoff)
    publisher = _direct_publisher(row)
    stance = _text(row.get("stance"))
    excerpt = row.get("excerpt") if isinstance(row.get("excerpt"), str) else ""
    record_id = _text(row.get("record_id"))
    urls = row.get("source_urls")
    urls = urls if isinstance(urls, (list, tuple)) else [_text(urls)]
    notes = row.get("validity_text")
    notes = notes if isinstance(notes, (list, tuple)) else [notes]
    start, end = _date(row.get("validity_start")), _date(row.get("validity_end"))
    role = "insufficient"
    if validity in STATUS_BY_DATE:
        role = validity
    elif (
        validity in PUBLISHED_VALIDITY and record_id and excerpt.strip()
        and _text(row.get("scope")) and _text(row.get("evidence_field")) != "equipment"
    ):
        if stance == "supports" and publisher:
            role = "supporting"
        elif stance == "contradicts":
            role = "contradicting" if publisher else "contradicting_unattributed"
    return {
        "record_id": record_id, "role": role, "stance": stance, "excerpt": excerpt,
        "source": _text(row.get("source")), "source_url": _text(row.get("source_url")),
        "candidate_source_urls": sorted({_text(url) for url in urls if _text(url)}),
        "publisher_family": publisher, "scope": _text(row.get("scope")),
        "validity": validity, "published_validity": _text(row.get("validity")),
        "validity_text": [note for note in notes if _text(note)],
        "validity_start": start.isoformat() if start else "", "validity_end": end.isoformat() if end else "",
        "citation_quality": _text(row.get("citation_quality")), "evidence_field": _text(row.get("evidence_field")),
        "match_reason": _text(row.get("match_reason")),
    }


def _distinct(rows, key):
    return sorted({row[key] for row in rows if row[key]})


def _service_status(facility_id, service, evidence, cutoff):
    publishers_by_record = defaultdict(set)
    for row in evidence:
        if row["role"] in {"supporting", "contradicting"}:
            publishers_by_record[row["record_id"]].add(row["publisher_family"])
    for row in evidence:
        if row["role"] in {"supporting", "contradicting"} and len(publishers_by_record[row["record_id"]]) > 1:
            row["role"] = "contradicting_unattributed" if row["role"] == "contradicting" else "insufficient"
            row["publisher_family"] = ""

    by_role = defaultdict(list)
    for row in evidence:
        by_role[row["role"]].append(row)
    supports, cited_disputes = by_role["supporting"], by_role["contradicting"]
    unattributed_disputes = by_role["contradicting_unattributed"]
    disputes = cited_disputes + unattributed_disputes
    publishers = _distinct(supports, "publisher_family")
    if disputes:
        status = "conflicting"
    elif len(publishers) > 1:
        status = "corroborated"
    else:
        status = "single_direct_source" if publishers else "insufficient"
    relevant = supports + disputes
    caveat = "Applies to the listed service and each publication's stated scope. Current availability requires confirmation."
    if any(row["validity"] == "undated" for row in relevant):
        caveat += " Some evidence gives no validity dates."
    if unattributed_disputes:
        caveat += " A conflicting excerpt could not be traced to a single publication and needs review."
    return {
        "facility_id": facility_id, "service": service, "verification_status": status,
        "verification_as_of": cutoff.isoformat(), "supporting_publisher_count": len(publishers),
        "supporting_publishers": publishers, "supporting_record_ids": _distinct(supports, "record_id"),
        "supporting_urls": _distinct(supports, "source_url"),
        "contradicting_publishers": _distinct(cited_disputes, "publisher_family"),
        "contradicting_record_ids": _distinct(disputes, "record_id"),
        "contradicting_urls": _distinct(cited_disputes, "source_url"),
        "unattributed_contradiction_record_ids": _distinct(unattributed_disputes, "record_id"),
        "inactive_record_ids": _distinct(by_role["inactive"], "record_id"),
        "future_record_ids": _distinct(by_role["future"], "record_id"),
        "expired_record_ids": _distinct(by_role["expired"], "record_id"),
        "insufficient_record_ids": _distinct(by_role["insufficient"], "record_id"),
        "evidence_scopes": _distinct(relevant, "scope"), "scope_caveat": caveat, "evidence": evidence,
    }


def verify_services(claims: pd.DataFrame, as_of) -> pd.DataFrame:
    """Verify the published evidence for each canonical branch and service.

    Explicit claims for the same canonical service can corroborate that shared
    meaning. Each publication retains its own terms, including scheme coverage.
    Separate publisher families use organization-level domains as a proxy.
    A current explicit contradiction holds the status in conflict, including
    when reviewers still need to resolve its attribution. The result documents
    sources and validity without inferring live availability.
    """
    cutoff = _date(as_of)
    if cutoff is None:
        raise ValueError("Service verification requires an ISO as-of date.")
    if claims.empty:
        return pd.DataFrame(columns=VERIFICATION_COLUMNS)
    missing = REQUIRED_CLAIM_COLUMNS - set(claims.columns)
    if missing:
        raise ValueError(f"Service claims are missing columns: {', '.join(sorted(missing))}.")
    grouped = defaultdict(list)
    for row in claims.to_dict(orient="records"):
        facility_id, service = _text(row["facility_id"]), _text(row["service"])
        if facility_id and service:
            grouped[facility_id, service].append(_claim_evidence(row, cutoff))
    results = [
        _service_status(facility_id, service, evidence, cutoff)
        for (facility_id, service), evidence in sorted(grouped.items())
    ]
    return pd.DataFrame(results, columns=VERIFICATION_COLUMNS)
