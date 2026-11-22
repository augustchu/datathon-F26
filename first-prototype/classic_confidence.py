"""Resolve confidence for the requested service at a particular facility branch."""

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import numpy as np

from pipeline import EVIDENCE_FIELDS, SERVICE_TERMS, _items, _service_key
from service_confidence import (
    RECORD_TEXT_KINDS, SERVICE_EVIDENCE_POINTS, branch_pin,
    load_reviewed_service_evidence, reviewed_service_status, service_confidence, service_match,
)

EVIDENCE_PATH = Path(__file__).with_name("service_confidence_evidence.json")
_FIELD_MARKER = re.compile(r"(?:^|\s*\|\s*)(specialties|specialty|capability|procedure|equipment|description):\s*")
_BRANCH_ID = re.compile(r"fdr:[^:\s]+")
REVIEWED_KINDS = SERVICE_EVIDENCE_POINTS.keys() - RECORD_TEXT_KINDS

# The confidence curve and record classifier follow the service-specific prototype.
SERVICE_LIST_FIELDS = {"specialties", "capability", "procedure"}
# These search aliases suggest related capabilities without stating the requested scope.
MENTION_ONLY_TERMS = {
    "Emergency C-section": {"cesarean section", "caesarean section"},
    "NICU": {"sncu"},
    "General care": {"general hospital", "multispecialty", "multi-specialty"},
}
CLAUSE_BOUNDARY = re.compile(r"[.!?;|\n]+")
NON_OFFER_BEFORE = re.compile(
    r"\b(?:no|does not|do not|doesn['’]t|without|not available|unavailable|"
    r"plans? to (?:offer|add|start)|refers? (?:patients? )?(?:elsewhere )?(?:to|for))\b", re.I,
)
NON_OFFER_AFTER = re.compile(
    r"^[\s:–—-]*(?:services?\s+)?(?:(?:is|are)\s+)?"
    r"(?:no\b|unavailable\b|no longer\b|not\s+(?:available|offered|provided)\b|"
    r"(?:isn['’]t|aren['’]t)\s+(?:available|offered|provided)\b|"
    r"referral only\b|planned\b|coming soon\b)", re.I,
)
OTHER_LOCATION = re.compile(r"\b(?:another|other|different)\s+(?:branch|hospital|location)\b", re.I)
OFFER_QUALIFIERS = r"(?:(?:an?|dedicated|on[- ]site|in[- ]house|24/7)\s+)*"
DIRECT_OFFER_BEFORE = re.compile(
    r"^\s*(?:(?:we|(?:this|our|the)\s+(?:branch|facility|hospital|clinic|centre|center))\s+)?"
    r"(?:also\s+)?(?:offers?|provides?|performs?|has|have|operates?|runs?|houses?)\s+"
    + OFFER_QUALIFIERS + r"$", re.I,
)
DIRECT_OFFER_AFTER = re.compile(r"^\s+(?:is|are)\s+(?:available|offered|provided|performed)\b", re.I)
SERVICE_LIST_HEADER = re.compile(r"^\s*(?:our\s+)?(?:services|procedures)(?:\s+offered)?\s*(?::|include[s]?\s)\s*(?:[^,:;]+,\s*)*$", re.I)
SERVICE_LIST_PREFIX = re.compile(r"^\s*" + OFFER_QUALIFIERS + r"$", re.I)
SERVICE_LIST_SUFFIX = re.compile(
    r"^\s*(?:(?:units?|clinics?|centres?|centers?|departments?|services?|treatments?)"
    r"(?:\s+(?:of|with)\s+\d+\s+(?:beds?|stations?|chairs?))?)?\s*$", re.I,
)
INDIRECT_SERVICE = re.compile(r"\b(?:tie[- ]ups?|referral|affiliated|partner hospital|planned)\b", re.I)
SERVICE_MODIFIER = re.compile(r"^\s+(?:machines?|equipment|catheters?|access|training|counseling|counselling|patients?)\b", re.I)
SERVICE_LOCATION = re.compile(
    r"\b(?:at|through|via)\s+([^,;()]{0,90}?\b(?:hospitals?|branches?|facility|clinics?|centres?|centers?|campus)\b)", re.I,
)
OWN_LOCATION = re.compile(r"^(?:this|our|the)\s+(?:branch|facility|hospital|clinic|centre|center|campus)$", re.I)
MATCH_PENALTY_MAX_KM = 5.0
EVIDENCE_PENALTY_MAX_MINUTES = 60.0
EVIDENCE_CONFIDENCE_MIDPOINT = 55.0
EVIDENCE_CONFIDENCE_WIDTH = 3.5
ROUTE_MINUTES_PER_KM_EQUIVALENT = 2.0


def evidence_penalty_minutes(confidence):
    """Map evidence confidence to a decreasing logistic penalty with exact endpoints."""
    def curve(score):
        return 1.0 / (1.0 + np.exp((score - EVIDENCE_CONFIDENCE_MIDPOINT) / EVIDENCE_CONFIDENCE_WIDTH))

    bottom, top = curve(0), curve(100)
    return EVIDENCE_PENALTY_MAX_MINUTES * ((curve(np.clip(confidence, 0, 100)) - top) / (bottom - top))


def _term_match(text, terms, *, structured, mention_only, facility_name):
    first_match = ("none", None, "")
    name_key = facility_name.casefold()
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I)
        for match in pattern.finditer(text):
            prefix = CLAUSE_BOUNDARY.split(text[max(0, match.start() - 90):match.start()])[-1]
            suffix = CLAUSE_BOUNDARY.split(text[match.end():match.end() + 140], maxsplit=1)[0]
            excerpt = prefix + match.group() + suffix
            if NON_OFFER_BEFORE.search(prefix) or NON_OFFER_AFTER.match(suffix) or OTHER_LOCATION.search(excerpt):
                continue
            excerpt = re.sub(r"\s+", " ", excerpt).strip()
            if first_match[0] == "none":
                first_match = "mention", term, excerpt
            if term in mention_only or INDIRECT_SERVICE.search(excerpt) or SERVICE_MODIFIER.match(suffix):
                continue
            if any(not OWN_LOCATION.fullmatch(place) and place.casefold() != name_key
                   for place in SERVICE_LOCATION.findall(excerpt)):
                continue
            standalone = SERVICE_LIST_PREFIX.fullmatch(prefix)
            listed = (structured and standalone and SERVICE_LIST_SUFFIX.fullmatch(suffix))
            offer_prefix = prefix.lstrip()
            if name_key and offer_prefix.casefold().startswith(name_key + " "):
                offer_prefix = offer_prefix[len(facility_name):]
            offered = DIRECT_OFFER_BEFORE.match(offer_prefix) or (standalone and DIRECT_OFFER_AFTER.match(suffix))
            if listed or offered or SERVICE_LIST_HEADER.search(prefix):
                return "record_listing", term, excerpt
    return first_match


def _record_service_match(fields, terms, mention_only, facility_name):
    best = ("none", None, "")
    for field, text in fields.items():
        if field == "equipment":
            continue
        kind, term, excerpt = _term_match(text, terms, structured=field in SERVICE_LIST_FIELDS,
                                          mention_only=mention_only, facility_name=facility_name)
        if kind == "record_listing":
            return kind, term, f"{field}: {excerpt}"
        if kind == "mention" and best[0] == "none":
            best = kind, term, f"{field}: {excerpt}"
    return best


@dataclass(frozen=True)
class ServiceAssessment:
    kind: str
    score: int
    tier: str
    match_score: int
    branch_points: int
    evidence_points: int
    term: str
    excerpt: str
    urls: tuple[str, ...]


def _text(value):
    text = "" if value is None else str(value).strip()
    return "" if text.casefold() in {"nan", "none", "null", "<na>", "nat"} else text


def _source_url(value):
    url = _text(value)
    if not url or re.search(r"[\s<>\x00-\x1f\x7f]", url):
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None:
            return url
    except ValueError:
        pass
    return ""


@lru_cache(maxsize=1)
def _reviewed_by_branch():
    by_branch = {}
    for (facility_id, pin, service), claims in load_reviewed_service_evidence(EVIDENCE_PATH).items():
        by_branch.setdefault(facility_id, {}).setdefault(pin, {})[service] = claims
    return by_branch


def _reviewed_branch_id(row):
    facility_id = _text(row.get("facility_id"))
    exact_id = _text(row.get("equity_facility_id")) if "equity_facility_id" in row else facility_id
    source_id = _text(row.get("source_id"))
    if source_id and (exact_id != f"fdr:{source_id}" or facility_id != exact_id):
        return ""
    return exact_id if _BRANCH_ID.fullmatch(exact_id) else ""


def _prepared_claims(row, key):
    candidates = row.get(f"service_evidence_sources_{key}")
    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except ValueError:
            return ()
    if not isinstance(candidates, (list, tuple)):
        return ()
    return tuple(
        (url, _text(claim["excerpt"]))
        for claim in candidates
        if isinstance(claim, dict) and (url := _source_url(claim.get("url"))) and _text(claim.get("excerpt"))
    )


def _original_fields(row):
    fields = {field: "; ".join(_items(row.get(field))) for field in EVIDENCE_FIELDS}
    text = _text(row.get("service_text"))
    markers = list(_FIELD_MARKER.finditer(text))
    for i, marker in enumerate(markers):
        field = "specialties" if marker[1] == "specialty" else marker[1]
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        if not fields[field]:
            fields[field] = text[marker.end():end].strip()
    if not markers and not any(fields.values()):
        fields["description"] = text
    return fields


def _branch_points(row, name, pin, confirmed):
    if (_text(row.get("branch_routing_hold")).casefold() in {"true", "1"}
            or _text(row.get("location_search_eligible")).casefold() in {"false", "0"}):
        return 0

    lat = row.get("lat", row.get("latitude"))
    lon = row.get("lon", row.get("longitude"))
    try:
        if not (6 <= float(lat) <= 38 and 68 <= float(lon) <= 98):
            return 0
    except (TypeError, ValueError):
        return 0

    if confirmed:
        return 20
    points = _text(row.get("branch_confidence_points"))
    if points in {"0", "10", "20", "0.0", "10.0", "20.0"}:
        return int(float(points))
    return 10 if pin and name and name.casefold() != "unnamed facility" else 0


def service_assessment_columns(service):
    """Identify the snapshot fields that can affect this service assessment."""
    key = _service_key(service)
    return {
        *EVIDENCE_FIELDS, "service_text", "name", "facility_name", "pin", "facility_id",
        "equity_facility_id", "source_id", "branch_routing_hold", "location_search_eligible",
        "lat", "lon", "latitude", "longitude", "branch_confidence_points",
        f"service_evidence_kind_{key}", f"service_evidence_sources_{key}", f"term_{key}", f"excerpt_{key}",
    }


def assess_service_confidence(row, service, aliases, *, as_of=None):
    """Rate only the selected service, using reviewed claims, then prepared or raw facility text."""
    key = _service_key(service)
    name = _text(row.get("name")) or _text(row.get("facility_name"))
    pin = branch_pin(_text(row.get("pin")))
    as_of = as_of or date.today()
    branch_id = _reviewed_branch_id(row)
    branch_reviews = _reviewed_by_branch().get(branch_id, {})
    reviewed = branch_reviews.get(pin, {})
    reviewed_kind, reviewed_claims = reviewed_service_status(reviewed.get(key, ()), as_of)
    prepared_kind = _text(row.get(f"service_evidence_kind_{key}")).casefold()
    valid_prepared = False
    if branch_id and pin and prepared_kind in REVIEWED_KINDS:
        prepared_claims = _prepared_claims(row, key)
        prepared_urls = {url for url, _ in prepared_claims}
        known_source_urls = {
            claim["source_url"]
            for services in branch_reviews.values()
            for claim in services.get(key, ())
        }
        minimum_sources = 2 if prepared_kind == "corroborated" else 1
        valid_prepared = len(prepared_urls) >= minimum_sources and prepared_urls.isdisjoint(known_source_urls)
    confirmed_branch = any(reviewed_service_status(claims, as_of)[1] for claims in reviewed.values())
    branch = _branch_points(row, name, pin, confirmed_branch or bool(valid_prepared))
    term = _text(row.get(f"term_{key}"))
    excerpt = _text(row.get(f"excerpt_{key}"))
    urls = ()
    if "conflict" in {prepared_kind, reviewed_kind}:
        kind, term, excerpt = "conflict", "", ""
    elif reviewed_claims:
        kind, term, excerpt = reviewed_kind, service, reviewed_claims[0]["excerpt"]
        urls = tuple(dict.fromkeys(claim["source_url"] for claim in reviewed_claims))
    elif valid_prepared:
        kind, term = prepared_kind, term or service
        excerpt = excerpt or prepared_claims[0][1]
        urls = tuple(dict.fromkeys(url for url, _ in prepared_claims))
    elif prepared_kind in RECORD_TEXT_KINDS and term and excerpt:
        kind = prepared_kind
    else:
        terms = tuple(dict.fromkeys((*aliases, *SERVICE_TERMS.get(service, ()))))
        kind, term, excerpt = _record_service_match(
            _original_fields(row), terms, MENTION_ONLY_TERMS.get(service, ()), name,
        )

    score, tier = service_confidence(kind, branch)
    return ServiceAssessment(
        kind=kind, score=score, tier=tier, match_score=service_match(kind), branch_points=branch,
        evidence_points=SERVICE_EVIDENCE_POINTS.get(kind, 0), term=term or "", excerpt=excerpt, urls=urls,
    )
