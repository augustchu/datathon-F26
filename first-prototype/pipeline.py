"""Small, auditable preparation and ranking layer for the Databricks prototype."""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
import re
import zipfile
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from service_confidence import (
    RECORD_TEXT_KINDS, SERVICE_EVIDENCE_LABELS, SERVICE_EVIDENCE_POINTS, branch_pin,
    load_reviewed_service_evidence, reviewed_service_status, service_confidence, service_match,
)

SERVICE_TERMS = {
    "Dialysis": ["dialysis", "haemodialysis", "hemodialysis", "peritoneal dialysis"],
    "Emergency C-section": ["emergency c-section", "emergency c section", "emergency caesarean", "emergency cesarean", "cesarean section", "caesarean section"],
    "NICU": ["nicu", "neonatal intensive care", "newborn intensive care", "sncu"],
    "Cardiac surgery": ["cardiac surgery", "cardiothoracic surgery", "cardiothoracic and vascular surgery", "cabg", "coronary artery bypass"],
    "MRI": ["mri", "magnetic resonance imaging"],
    "Burn care": ["burn unit", "burns unit", "burn care", "burn treatment"],
    "Trauma / emergency": ["trauma centre", "trauma center", "trauma care", "emergency medicine", "emergency department"],
    "Maternity / delivery": ["maternity", "obstetrics", "obstetric", "labour room", "labor room", "normal delivery"],
    "General care": ["general hospital", "general medicine", "primary care", "multispecialty", "multi-specialty", "community health centre", "community health center"],
}
FIELD_CANDIDATES = {
    "name": ["name", "facility_name", "facilityName", "hospital_name"],
    "city": ["city", "address_city", "addressCity"],
    "district": ["district", "address_district", "addressDistrict"],
    "state": ["state", "address_stateOrRegion", "state_name", "address_state"],
    "pin": ["pin", "address_zipOrPostcode", "postcode", "pincode", "postal_code"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon", "lng"],
    "capacity": ["capacity", "bed_capacity", "beds", "number_of_beds"],
    "phone": ["published_phone", "officialPhone", "phone", "telephone", "contact_phone"],
    "website": ["officialWebsite", "website", "websites", "url"],
    "id": ["unique_id", "facility_id", "id", "facilityId"],
    "url": ["source_urls", "sourceUrls", "source_url", "url", "website", "sources"],
}
EVIDENCE_FIELDS = ["specialties", "capability", "procedure", "equipment", "description"]
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
NFHS_HINTS = {
    "dialysis": ["blood_sugar", "diabetes"],
    "emergency_c_section": ["institutional_birth", "csection", "caesarean"],
    "nicu": ["children_prev_symptoms", "diarrhoea", "diarrhea", "stunted", "underweight"],
    "cardiac_surgery": ["blood_sugar", "hypertension"],
    "maternity_delivery": ["institutional_birth", "csection", "caesarean"],
    "general_care": ["children_prev_symptoms", "diarrhoea", "diarrhea", "blood_sugar"],
}
MATCH_PENALTY_MAX_KM = 5.0
EVIDENCE_PENALTY_MAX_MINUTES = 60.0
EVIDENCE_CONFIDENCE_MIDPOINT = 55.0
EVIDENCE_CONFIDENCE_WIDTH = 3.5
ROUTE_MINUTES_PER_KM_EQUIVALENT = 2.0


def _col(df, aliases):
    lower = {str(c).casefold(): c for c in df.columns}
    for alias in aliases:
        if alias.casefold() in lower:
            return lower[alias.casefold()]
    return None


def _items(value):
    if value is None or value is pd.NA:
        return []
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [x for v in value for x in _items(v)]
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null", "<na>"}:
        return []
    try:
        decoded = json.loads(text)
        if isinstance(decoded, list):
            return [x for v in decoded for x in _items(v)]
        if isinstance(decoded, str) and decoded != text:
            return _items(decoded)
    except (ValueError, TypeError):
        pass
    return [text]


def _urls(value):
    found = []
    for item in _items(value):
        found.extend(re.findall(r"https?://[^\s\"'<>]+", item))
    return list(dict.fromkeys(x.rstrip(",.;)]}") for x in found if urlparse(x).scheme in {"http", "https"}))


def _number(value):
    if value is None or pd.isna(value):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", str(value))
    return float(match.group(1)) if match else None


def _clean_id(value):
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _contact_text(value):
    items = _items(value)
    return " / ".join(dict.fromkeys(str(x).strip() for x in items if str(x).strip()))


def _website_urls(value):
    urls = []
    for item in _items(value):
        candidate = str(item).strip().rstrip(".,;)")
        if not candidate:
            continue
        if not re.match(r"^https?://", candidate, flags=re.I):
            candidate = "https://" + candidate.lstrip("/")
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc and "." in parsed.netloc:
            urls.append(candidate)
    return list(dict.fromkeys(urls))


def attach_capacity_release(raw: pd.DataFrame, capacity_release: pd.DataFrame,
                           confidence: pd.DataFrame) -> pd.DataFrame:
    """Use cleaned release rows as display data and cross-check them against Marketplace IDs/capacity."""
    raw_id_col = _col(raw, FIELD_CANDIDATES["id"])
    release_id_col = _col(capacity_release, ["unique_id", "facility_id", "facilityid", "id"])
    capacity_col = _col(capacity_release, ["capacity", "beds", "bed_capacity"])
    status_col = _col(capacity_release, ["capacity_status", "capacitystatus"])
    row_index_col = _col(capacity_release, ["row_index", "source_row_index"])
    confidence_col = _col(confidence, ["confidence_score"])
    if not all((raw_id_col, release_id_col, capacity_col, status_col, row_index_col, confidence_col)):
        raise ValueError("Capacity/confidence release is missing required facility ID, row_index, capacity, status, or confidence columns.")
    if len(raw) != len(capacity_release) or len(capacity_release) != len(confidence):
        raise ValueError(
            f"Row counts do not align: facilities={len(raw):,}, capacity={len(capacity_release):,}, "
            f"confidence={len(confidence):,}. No capacity or confidence values were applied."
        )
    positions = pd.to_numeric(capacity_release[row_index_col], errors="coerce")
    if positions.isna().any() or positions.astype(int).tolist() != list(range(len(capacity_release))):
        raise ValueError("Capacity CSV row_index is not an intact 0-based source order; refusing to pair confidence by position.")
    confidence_values = pd.to_numeric(confidence[confidence_col], errors="coerce")
    if confidence_values.isna().any() or not confidence_values.between(0, 100).all():
        raise ValueError("Confidence scores must be numeric and between 0 and 100.")

    release = capacity_release.copy()
    release["_facility_key"] = release[release_id_col].map(_clean_id)
    release["_capacity_value"] = pd.to_numeric(release[capacity_col], errors="coerce")
    release["_capacity_status"] = release[status_col].fillna("missing").astype(str).str.strip()
    # The release documentation says the companion confidence CSV has the same exact row order.
    release["_confidence_score"] = confidence_values.to_numpy()
    if release._facility_key.eq("").any():
        raise ValueError("Capacity release contains blank facility IDs; cannot align to the Marketplace table.")
    raw_keys = raw[raw_id_col].map(_clean_id)
    if raw_keys.eq("").any():
        raise ValueError("Marketplace facility table contains blank IDs; cannot safely apply release values.")

    # Duplicate IDs are present in the release. Require every field used by the demo to agree.
    check_fields = ["_capacity_value", "_capacity_status", "_confidence_score"]
    for source_name, aliases in {
        "_capacity_source": ["capacity_source", "capacity_model_id"],
        "_capacity_source_url": ["capacity_source_url"],
        "_phone_source_url": ["phone_source_url"],
        "_capacity_lower": ["capacity_prediction_lower"],
        "_capacity_upper": ["capacity_prediction_upper"],
        "_release_original_capacity": ["original_capacity"],
    }.items():
        col = _col(release, aliases)
        if col:
            release[source_name] = release[col]
            check_fields.append(source_name)
    phone_cols = [c for c in ("published_phone", "officialPhone", "phone", "phone_numbers") if c in release]
    website_cols = [c for c in ("officialWebsite", "website", "websites") if c in release]
    release["_published_phone"] = release.apply(
        lambda row: next((row[c] for c in phone_cols if _contact_text(row[c])), pd.NA), axis=1
    )
    release["_website"] = release.apply(
        lambda row: next((row[c] for c in website_cols if _contact_text(row[c])), pd.NA), axis=1
    )
    check_fields.extend(["_published_phone", "_website"])
    duplicated = release.groupby("_facility_key")[check_fields].nunique(dropna=False)
    if (duplicated > 1).any(axis=None):
        examples = duplicated[(duplicated > 1).any(axis=1)].index[:5].tolist()
        raise ValueError(f"Duplicate facility IDs have conflicting release values: {examples}")

    lookup = release.drop_duplicates("_facility_key").set_index("_facility_key")
    missing = sorted(set(raw_keys) - set(lookup.index))
    extra = sorted(set(lookup.index) - set(raw_keys))
    if missing or extra:
        raise ValueError(
            f"Facility IDs do not align with release ({len(missing):,} missing, {len(extra):,} extra; "
            f"examples missing={missing[:5]}, extra={extra[:5]}). Confirm the marketplace table uses "
            "the same facilities source snapshot."
        )

    # The release is the display-data source. Marketplace rows are retained only
    # to compare original capacities and prove that this is the same source snapshot.
    original_capacity_col = _col(raw, FIELD_CANDIDATES["capacity"])
    raw_original = raw[original_capacity_col] if original_capacity_col else pd.Series(np.nan, index=raw.index)
    raw_values = pd.to_numeric(raw_original, errors="coerce")
    raw_check = pd.DataFrame({"_facility_key": raw_keys, "_original_capacity": raw_values})
    duplicate_source_counts = raw_check.groupby("_facility_key")["_original_capacity"].nunique(dropna=False)
    if duplicate_source_counts.gt(1).any():
        examples = duplicate_source_counts[duplicate_source_counts.gt(1)].index[:5].tolist()
        raise ValueError(f"Marketplace rows with repeated IDs have inconsistent original capacity values: {examples}")
    original_lookup = raw_check.drop_duplicates("_facility_key").set_index("_facility_key")["_original_capacity"].to_dict()

    out = capacity_release.copy()
    out["capacity_release_value"] = release["_capacity_value"].to_numpy()
    out["capacity_release_status"] = release["_capacity_status"].to_numpy()
    out["confidence_score"] = release["_confidence_score"].to_numpy()
    out["capacity_release_source"] = release["_capacity_source"] if "_capacity_source" in release else ""
    out["capacity_release_source_url"] = release["_capacity_source_url"] if "_capacity_source_url" in release else ""
    out["phone_source_url"] = release["_phone_source_url"] if "_phone_source_url" in release else ""
    out["capacity_prediction_lower"] = release["_capacity_lower"] if "_capacity_lower" in release else pd.NA
    out["capacity_prediction_upper"] = release["_capacity_upper"] if "_capacity_upper" in release else pd.NA
    out["capacity_release_original"] = release["_release_original_capacity"] if "_release_original_capacity" in release else pd.NA
    out["release_published_phone"] = release["_published_phone"].to_numpy()
    out["release_website"] = release["_website"].to_numpy()
    out["capacity_original_dataset"] = release["_facility_key"].map(original_lookup).to_numpy()
    original_values = pd.to_numeric(out["capacity_original_dataset"], errors="coerce")
    release_original = pd.to_numeric(out["capacity_release_original"], errors="coerce")
    both_missing = original_values.isna() & release_original.isna()
    comparable = original_values.notna() & release_original.notna()
    original_alignment = np.select(
        [both_missing, comparable & np.isclose(original_values.fillna(0), release_original.fillna(0)), comparable],
        ["both blank", "match", "difference"], default="only one side populated"
    )
    out["capacity_source_alignment_status"] = original_alignment
    selected_values = pd.to_numeric(out["capacity_release_value"], errors="coerce")
    same_selected = selected_values.notna() & original_values.notna() & np.isclose(selected_values.fillna(0), original_values.fillna(0))
    selected_both_missing = selected_values.isna() & original_values.isna()
    out["capacity_crosscheck_status"] = np.select(
        [same_selected, selected_both_missing, selected_values.notna() & original_values.isna(), selected_values.isna() & original_values.notna()],
        ["match", "both blank", "release fills original blank", "release withholds original value"],
        default="released value differs from Marketplace"
    )
    return out


def confidence_adjustment(score):
    """Return a transparent 0/40/80 km-equivalent penalty for high/medium/low confidence."""
    if score is None or pd.isna(score):
        return "unknown", 80.0
    score = float(score)
    if score >= 50:
        return "High", 0.0
    if score >= 25:
        return "Medium", 40.0
    return "Low", 80.0


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


def load_audited_location_holds(path):
    """Index rejected coordinates by the release branch ID."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))["holds"]
    by_branch = {}
    for row in records:
        by_branch.setdefault(row["facility_id"], []).append((row["latitude"], row["longitude"]))
    return by_branch


def _within_india_bounds(latitude, longitude):
    return latitude is not None and longitude is not None and 6 <= latitude <= 38 and 68 <= longitude <= 98


def prepare_facilities(raw: pd.DataFrame, predicted_beds=None, model_label="not applied", *,
                       reviewed_evidence=None, audited_location_holds=None, as_of=None) -> pd.DataFrame:
    """Flatten source rows while retaining the source text and claim-level citations."""
    if raw.empty:
        raise ValueError("The facility source table returned no rows.")
    cols = {key: _col(raw, aliases) for key, aliases in FIELD_CANDIDATES.items()}
    if not cols["name"]:
        raise ValueError(f"Could not identify a facility name column. Available columns: {list(raw.columns)}")
    missing = [f for f in EVIDENCE_FIELDS if _col(raw, [f]) is None]
    evidence = reviewed_evidence or {}
    location_holds = audited_location_holds or {}
    as_of = as_of or date.today()
    services = [(_service_key(service), service, terms) for service, terms in SERVICE_TERMS.items()]
    id_seen = {}
    rows = []
    for pos, (_, source) in enumerate(raw.iterrows()):
        name_value = source[cols["name"]]
        name = "Unnamed facility" if pd.isna(name_value) else str(name_value)
        source_id = str(source[cols["id"]]) if cols["id"] and pd.notna(source[cols["id"]]) else str(pos)
        base_id = f"fdr:{source_id}"
        id_seen[base_id] = id_seen.get(base_id, 0) + 1
        fid = base_id if id_seen[base_id] == 1 else f"{base_id}:row{pos}"
        release_id = _clean_id(source.get("facility_id")) or base_id
        pin_value = source[cols["pin"]] if cols["pin"] else None
        pin = branch_pin(pin_value)
        latitude = _number(source[cols["lat"]]) if cols["lat"] else None
        longitude = _number(source[cols["lon"]]) if cols["lon"] else None
        reviewed_claims = {key: evidence.get((release_id, pin, key), ()) for key, _, _ in services}
        reviewed = {key: reviewed_service_status(claims, as_of) for key, claims in reviewed_claims.items()}
        routing_hold = any(
            str(source.get(field, "")).strip().casefold() in {"false", "0"}
            for field in ("routing_eligible", "location_routing_eligible")
        )
        valid_point = _within_india_bounds(latitude, longitude)
        if valid_point:
            routing_hold = routing_hold or any(
                math.isclose(latitude, rejected_lat, rel_tol=0, abs_tol=1e-6)
                and math.isclose(longitude, rejected_lon, rel_tol=0, abs_tol=1e-6)
                for rejected_lat, rejected_lon in location_holds.get(release_id, ())
            )
        location_search_eligible = valid_point and not routing_hold
        confirmed_branch = any(citations for _, citations in reviewed.values())
        plausible_branch = bool(pin and pd.notna(name_value) and str(name_value).strip())
        branch_points = 0 if not location_search_eligible else 20 if confirmed_branch else 10 if plausible_branch else 0
        field_texts = {}
        for field in EVIDENCE_FIELDS:
            col = _col(raw, [field])
            if col:
                values = _items(source[col])
                if values:
                    field_texts[field] = "; ".join(values)
        full_text = " | ".join(f"{field}: {text}" for field, text in field_texts.items())
        urls = _urls(source[cols["url"]]) if cols["url"] else []
        if "capacity_release_value" in raw.columns:
            # A withheld release value stays missing even if the older raw field has a number.
            beds = _number(source.get("capacity_release_value"))
            release_status = str(source.get("capacity_release_status", "missing")).strip()
            bed_source = f"release: {release_status}"
        else:
            beds = _number(source[cols["capacity"]]) if cols["capacity"] else None
            release_status = ""
            bed_source = "reported" if beds is not None else "missing"
        if (beds is None or beds <= 0) and predicted_beds is not None and fid in predicted_beds:
            beds = predicted_beds[fid]
            bed_source = model_label
        phone_value = source.get("release_published_phone")
        if phone_value is None or pd.isna(phone_value) or not str(phone_value).strip():
            phone_value = source[cols["phone"]] if cols["phone"] else None
        website_value = source.get("release_website")
        if website_value is None or pd.isna(website_value) or not _contact_text(website_value):
            website_value = source[cols["website"]] if cols["website"] else None
        confidence_score = _number(source.get("confidence_score"))
        confidence_tier, confidence_penalty = confidence_adjustment(confidence_score)
        row = {
            "facility_id": fid, "source_id": source_id, "facility_name": name,
            "city": str(source[cols["city"]]) if cols["city"] and pd.notna(source[cols["city"]]) else "",
            "district": str(source[cols["district"]]) if cols["district"] and pd.notna(source[cols["district"]]) else "",
            "state": str(source[cols["state"]]) if cols["state"] and pd.notna(source[cols["state"]]) else "",
            "pin": str(pin_value) if pd.notna(pin_value) else "",
            "latitude": latitude,
            "longitude": longitude,
            "beds_for_scenario": beds, "beds_provenance": bed_source,
            "capacity_release_status": release_status,
            "capacity_release_source": _contact_text(source.get("capacity_release_source")),
            "capacity_release_source_url": _contact_text(source.get("capacity_release_source_url")),
            "phone_source_url": _contact_text(source.get("_phone_source_url")),
            "capacity_prediction_lower": _number(source.get("capacity_prediction_lower")),
            "capacity_prediction_upper": _number(source.get("capacity_prediction_upper")),
            "capacity_original_dataset": _contact_text(source.get("capacity_original_dataset")),
            "capacity_release_original": _number(source.get("capacity_release_original")),
            "capacity_crosscheck_status": _contact_text(source.get("capacity_crosscheck_status")),
            "capacity_source_alignment_status": _contact_text(source.get("capacity_source_alignment_status")),
            "confidence_score": confidence_score,
            "confidence_tier": confidence_tier,
            "confidence_penalty": confidence_penalty,
            "branch_confidence_points": branch_points,
            "branch_routing_hold": routing_hold,
            "location_search_eligible": location_search_eligible,
            "phone": _contact_text(phone_value),
            "website_url": next(iter(_website_urls(website_value)), ""),
            "source_urls_json": json.dumps(urls), "service_text": full_text,
            "service_fields_missing": ",".join(missing),
        }
        for key, service, terms in services:
            evidence_kind, citations = reviewed[key]
            term, excerpt = None, ""
            if citations:
                term, excerpt = service, citations[0]["excerpt"]
            elif evidence_kind == "none":
                evidence_kind, term, excerpt = _record_service_match(
                    field_texts, terms, MENTION_ONLY_TERMS.get(service, ()), name.strip(),
                )
            row[f"term_{key}"] = term or ""
            row[f"excerpt_{key}"] = excerpt
            row[f"claim_citable_{key}"] = bool(citations)
            row[f"service_evidence_kind_{key}"] = evidence_kind
            row[f"service_evidence_sources_{key}"] = json.dumps([
                {"url": claim["source_url"], "excerpt": claim["excerpt"]} for claim in citations
            ])
        rows.append(row)
    return pd.DataFrame(rows)


def _service_key(service):
    return re.sub(r"[^a-z0-9]+", "_", service.casefold()).strip("_")


def read_geojson_archive(filename):
    """Read the GeoJSON member from the provided GeoBoundaries ZIP without GIS packages."""
    with zipfile.ZipFile(filename) as archive:
        names = [n for n in archive.namelist() if n.casefold().endswith(".geojson")]
        names = [n for n in names if "simplified" not in n.casefold()] or names
        if not names:
            raise ValueError(f"No GeoJSON member found in {filename}; ZIP members: {archive.namelist()[:20]}")
        # The archive contains a full ADM2 GeoJSON and a simplified copy; prefer the full version.
        member = next((n for n in names if "-adm2.geojson" in n.casefold()), names[0])
        return json.loads(archive.read(member))


def _point_in_ring(lon, lat, ring):
    inside = False
    if len(ring) < 3:
        return False
    x1, y1 = ring[-1][0], ring[-1][1]
    for point in ring:
        x2, y2 = point[0], point[1]
        if ((y1 > lat) != (y2 > lat)) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


def _coord_pairs(value):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            yield value[0], value[1]
        else:
            for child in value:
                yield from _coord_pairs(child)


def _feature_bounds(feature):
    points = list(_coord_pairs(feature.get("geometry", {}).get("coordinates", [])))
    if not points:
        return None
    return min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)


def _geometry_contains(lon, lat, geometry):
    if not geometry:
        return False
    kind, coordinates = geometry.get("type"), geometry.get("coordinates", [])
    polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
    for polygon in polygons:
        if polygon and _point_in_ring(lon, lat, polygon[0]) and not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:]):
            return True
    return False


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


def _region_norm(value):
    import unicodedata
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def attach_population(facilities, population):
    """Attach a modeled 2020 district population using normalized exact district/state keys."""
    district_col = _col(population, ["nfhs_district_name", "district_name", "district"])
    state_col = _col(population, ["nfhs_state", "state_ut", "state", "state_name"])
    population_col = _col(population, ["pop_2020", "population_2020", "population"])
    if not all((district_col, state_col, population_col)):
        raise ValueError("Population CSV needs district/state names and pop_2020 columns.")
    rows = population.copy()
    if "district_population_status" in rows:
        rows = rows[rows.district_population_status.astype(str).str.casefold().eq("available")]
    rows[population_col] = pd.to_numeric(rows[population_col], errors="coerce")
    rows = rows.dropna(subset=[population_col])
    rows["_key"] = rows.apply(lambda r: _region_norm(r[district_col]) + "::" + _region_norm(r[state_col]), axis=1)
    # Do not choose arbitrarily where the source contains duplicate region estimates.
    counts = rows.groupby("_key").size()
    lookup = rows.drop_duplicates("_key").set_index("_key")[population_col].to_dict()
    lookup = {key: value for key, value in lookup.items() if counts.get(key, 0) == 1}
    out = facilities.copy()
    district = out.get("nfhs_district", pd.Series("", index=out.index)).fillna("")
    state = out.get("nfhs_state", pd.Series("", index=out.index)).fillna("")
    keys = district.astype(str).map(_region_norm) + "::" + state.astype(str).map(_region_norm)
    out["regional_population"] = keys.map(lookup)
    out["population_status"] = out.regional_population.map(lambda x: "modeled_2020_population" if pd.notna(x) else "unavailable_or_ambiguous")
    out["population_source"] = "WorldPop 2020 constrained raster, state-scaled to MoHFW/NCP projection"
    return out


def eligible_facility_locations(frame):
    """Use the saved routing screen for search origins and destinations."""
    if "location_search_eligible" not in frame:
        return frame.iloc[:0]
    eligible = frame.location_search_eligible.eq(True)
    eligible &= pd.to_numeric(frame.latitude, errors="coerce").between(6, 38)
    eligible &= pd.to_numeric(frame.longitude, errors="coerce").between(68, 98)
    if "branch_routing_hold" in frame:
        eligible &= ~frame.branch_routing_hold.eq(True)
    if "region_match_status" in frame:
        eligible &= ~frame.region_match_status.isin({"invalid_or_missing_coordinates", "outside_or_missing"})
    return frame.loc[eligible]


def _has_reviewed_citation(value):
    try:
        claims = json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(claims, list) and any(
        isinstance(claim, dict) and str(claim.get("url", "")).startswith(("https://", "http://"))
        and str(claim.get("excerpt") or "").strip()
        for claim in claims
    )


def service_candidates(frame, service):
    """Score branches with service evidence and a usable location."""
    key = _service_key(service)
    cite_col = f"claim_citable_{key}"
    kind_col = f"service_evidence_kind_{key}"
    evidence_col = f"service_evidence_sources_{key}"
    term_col, excerpt_col = f"term_{key}", f"excerpt_{key}"
    if not {cite_col, kind_col, evidence_col, term_col, excerpt_col, "branch_confidence_points"}.issubset(frame.columns):
        return frame.iloc[:0].copy()
    kind = frame[kind_col]
    record_text = kind.isin(RECORD_TEXT_KINDS) & frame[term_col].fillna("").str.strip().ne("")
    record_text &= frame[excerpt_col].fillna("").str.strip().ne("")
    reviewed = kind.isin(SERVICE_EVIDENCE_POINTS.keys() - RECORD_TEXT_KINDS) & frame[cite_col].eq(True)
    work = eligible_facility_locations(frame.loc[record_text | reviewed])
    has_citation = work[evidence_col].map(_has_reviewed_citation).eq(True)
    work = work.loc[work[kind_col].isin(RECORD_TEXT_KINDS) | has_citation].copy()
    work["service_evidence_kind"] = work[kind_col]
    work["service_evidence_sources_json"] = work[evidence_col]
    work["service_evidence_label"] = work.service_evidence_kind.map(SERVICE_EVIDENCE_LABELS)
    work["service_match_score"] = work.service_evidence_kind.map(service_match)
    work["service_confidence_points"] = work.service_evidence_kind.map(SERVICE_EVIDENCE_POINTS)
    scored = [service_confidence(kind, branch) for kind, branch in
              zip(work.service_evidence_kind, work.branch_confidence_points, strict=True)]
    work["service_confidence_score"] = [score for score, _ in scored]
    work["service_confidence_tier"] = [tier for _, tier in scored]
    return work


def rank_candidates(frame, service, origin, severity="routine", population_share_pct=0.0, top_k=5,
                    travel_times=None, *, nearest_only=False):
    """Rank eligible matches, using distance alone for the no-reachable-routes fallback."""
    key = _service_key(service)
    term_col, excerpt_col, cite_col = f"term_{key}", f"excerpt_{key}", f"claim_citable_{key}"
    if cite_col not in frame.columns:
        return pd.DataFrame()
    work = service_candidates(frame, service).drop(columns="confidence_penalty", errors="ignore")
    if work.empty:
        return work
    work["distance_km"] = work.apply(lambda r: haversine_km(origin[0], origin[1], float(r.latitude), float(r.longitude)), axis=1)
    population_share_pct = min(2.0, max(0.0, float(population_share_pct)))
    work["population_share_pct"] = population_share_pct
    work["severity"] = severity
    work["beds_for_scenario"] = pd.to_numeric(work.beds_for_scenario, errors="coerce")
    region_cols = [c for c in ("nfhs_district", "nfhs_state") if c in work.columns]
    if len(region_cols) == 2:
        work["_region_key"] = work[region_cols].fillna("").astype(str).agg("::".join, axis=1)
    else:
        work["_region_key"] = ""
    work["regional_population"] = pd.to_numeric(work.get("regional_population", pd.Series(index=work.index, dtype=float)), errors="coerce")
    work["regional_facility_count"] = work.groupby("_region_key")["facility_id"].transform("count")
    work["regional_expected_cases"] = work.regional_population.fillna(0.0) * population_share_pct / 100.0
    work["simulated_load"] = work.regional_expected_cases / work.regional_facility_count.clip(lower=1)
    work["capacity_basis_beds"] = work.beds_for_scenario.where(work.beds_for_scenario > 0, 50.0)
    work["load_ratio"] = work.simulated_load / work.capacity_basis_beds
    routed = travel_times is not None and not nearest_only
    work["travel_time_minutes"] = np.nan
    if routed:
        work["travel_time_minutes"] = work.facility_id.map(lambda fid: travel_times.get(str(fid)))
        work["travel_time_minutes"] = pd.to_numeric(work.travel_time_minutes, errors="coerce")
        valid_minutes = np.isfinite(work.travel_time_minutes) & work.travel_time_minutes.ge(0)
        work = work.loc[valid_minutes].copy()
    work["routing_basis"] = "TravelTime driving matrix" if routed else "straight-line"
    work["score_unit"] = "minutes" if routed else "km"
    work["base_travel_cost"] = work.travel_time_minutes if routed else work.distance_km
    if severity == "emergency" or nearest_only:
        work["load_penalty_km"] = 0.0
        work["load_penalty_basis"] = "nearest-distance fallback" if nearest_only else "ignored for emergency"
    else:
        # Each district's share of population demand is split evenly among every
        # evidence-backed facility candidate for this service in that district.
        # This is a scenario allocation, not a forecast of real patients or routing.
        burden_col = f"nfhs_burden_{key}"
        if burden_col in work.columns:
            work["regional_demand_factor"] = pd.to_numeric(work[burden_col], errors="coerce").fillna(1.0).clip(0.5, 2.0)
        else:
            work["regional_demand_factor"] = 1.0
        work["load_penalty_km"] = 12.0 * (1.0 - np.exp(-work.load_ratio * work.regional_demand_factor))
        work["load_penalty_basis"] = "heuristic km-equivalent adjustment, converted to minutes for TravelTime"
    work["service_match_penalty_km"] = MATCH_PENALTY_MAX_KM * (1.0 - work.service_match_score / 100.0)
    evidence_minutes = evidence_penalty_minutes(work.service_confidence_score)
    work["evidence_confidence_penalty_km"] = evidence_minutes / ROUTE_MINUTES_PER_KM_EQUIVALENT
    multiplier = ROUTE_MINUTES_PER_KM_EQUIVALENT if routed else 1.0
    if nearest_only:
        multiplier = 0.0
    work["load_penalty"] = work.load_penalty_km * multiplier
    work["service_match_penalty"] = work.service_match_penalty_km * multiplier
    work["evidence_confidence_penalty"] = work.evidence_confidence_penalty_km * multiplier
    work["score"] = work.base_travel_cost + work.load_penalty + work.service_match_penalty + work.evidence_confidence_penalty
    work["matched_term"] = work[term_col]
    work["supporting_excerpt"] = work[excerpt_col]
    work["source_urls"] = work.source_urls_json.map(lambda x: json.loads(x) if isinstance(x, str) else [])
    sort_cols = ["score", "travel_time_minutes", "distance_km"] if routed else ["score", "distance_km"]
    return work.sort_values(sort_cols, ascending=True, na_position="last").head(int(top_k)).reset_index(drop=True)


def resolve_location(frame, location_text="", lat=None, lon=None):
    if lat is not None and lon is not None:
        lat, lon = float(lat), float(lon)
        if not _within_india_bounds(lat, lon):
            raise ValueError("Coordinates must fall within the India bounds used by this prototype.")
        return (lat, lon), "User-provided coordinates"
    q = re.sub(r"\s+", " ", str(location_text or "")).strip().casefold()
    if not q:
        raise ValueError("Enter a city, district, PIN code, or coordinates.")
    frame = eligible_facility_locations(frame)
    pin = re.search(r"(?<!\d)([1-9]\d{5})(?!\d)", q)
    if pin:
        hit = frame[frame.pin.fillna("").astype(str).str.extract(r"(\d{6})", expand=False).eq(pin.group(1))]
        precision = "PIN matched facility-coordinate centroid (approximate)"
    else:
        tokens = [x.strip() for x in q.split(",") if x.strip()]
        hit = pd.DataFrame()
        for col in ("city", "district"):
            values = frame[col].fillna("").astype(str).str.casefold().str.strip()
            mask = values.eq(tokens[0])
            if len(tokens) > 1:
                mask &= frame.state.fillna("").astype(str).str.casefold().str.contains(re.escape(tokens[-1]), na=False)
            candidate = frame[mask]
            if not candidate.empty:
                hit = candidate
                break
        precision = "Facility-coordinate centroid (approximate)"
    if hit.empty:
        raise ValueError("No facility coordinates matched that place. Try a different spelling or enter coordinates.")
    return (float(hit.latitude.mean()), float(hit.longitude.mean())), precision


def attach_regions(facilities, boundaries, nfhs):
    """Point-in-polygon ADM2 match followed by conservative exact district/state join."""
    import unicodedata

    def norm(value):
        value = unicodedata.normalize("NFKD", str(value or "")).casefold()
        return re.sub(r"[^a-z0-9]+", "", value)

    original_columns = facilities.columns.tolist()
    # This archive's GeoJSON coordinates are WGS84. It contains shapeName but only
    # country-level shapeGroup, so the facility's state is used for the NFHS join.
    features = boundaries.get("features", []) if isinstance(boundaries, dict) else boundaries
    polygons = []
    for feature in features:
        props = feature.get("properties", {})
        district = next((props[k] for k in ("shapeName", "ADM2_EN", "district", "name") if k in props), "")
        if district:
            polygons.append({"district": str(district), "geometry": feature.get("geometry"), "bounds": _feature_bounds(feature)})
    if not polygons:
        raise ValueError("No district features with polygon geometry were found in the GeoJSON archive.")

    joined = facilities.copy()
    region_names, region_status = [], []
    for row in joined.itertuples():
        try:
            lon, lat = float(row.longitude), float(row.latitude)
            valid = math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90
        except (TypeError, ValueError):
            valid = False
        if not valid:
            region_names.append("")
            region_status.append("invalid_or_missing_coordinates")
            continue
        hits = []
        for polygon in polygons:
            bounds = polygon["bounds"]
            if bounds and bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3]:
                if _geometry_contains(lon, lat, polygon["geometry"]):
                    hits.append(polygon["district"])
        region_names.append(hits[0] if len(hits) == 1 else "")
        region_status.append("matched_adm2" if len(hits) == 1 else ("ambiguous_boundary" if len(hits) > 1 else "outside_or_missing"))
    joined["adm2_district"] = region_names
    joined["adm2_state"] = joined["state"].fillna("").astype(str) if "state" in joined else ""
    joined["region_match_status"] = region_status

    # NFHS names differ from boundary vintages. Join only normalized exact district/state names.
    nfhs_district = _col(nfhs, ["district_name", "district", "districtname"])
    nfhs_state = _col(nfhs, ["state_ut", "state", "state_name"])
    if nfhs_district and nfhs_state:
        lookup = {}
        for _, row in nfhs.iterrows():
            lookup[(norm(row[nfhs_district]), norm(row[nfhs_state]))] = (str(row[nfhs_district]), str(row[nfhs_state]))
        pairs = [lookup.get((norm(row.adm2_district), norm(row.adm2_state)), ("", ""))
                 for row in joined.itertuples()]
        joined["nfhs_district"] = [p[0] for p in pairs]
        joined["nfhs_state"] = [p[1] for p in pairs]
        joined.loc[joined.nfhs_district.eq(""), "region_match_status"] = joined.loc[joined.nfhs_district.eq(""), "region_match_status"].replace("matched_adm2", "nfhs_name_unmatched")
    else:
        joined["nfhs_district"], joined["nfhs_state"] = "", ""
    # Attach a clipped relative NFHS proxy to facilities only when the matched indicator exists.
    # These survey percentages are not incidence or patient counts.
    for service_key, hints in NFHS_HINTS.items():
        indicator_cols = [c for c in nfhs.columns if any(hint in str(c).casefold() for hint in hints)]
        burden_col = f"nfhs_burden_{service_key}"
        basis_col = f"nfhs_burden_basis_{service_key}"
        if indicator_cols and nfhs_district and nfhs_state:
            values = {}
            pooled = []
            for _, nr in nfhs.iterrows():
                parsed = []
                for col in indicator_cols:
                    raw_value = str(nr[col]).strip()
                    low_conf = raw_value.startswith("(") and raw_value.endswith(")")
                    raw_value = raw_value.strip("()")
                    if raw_value == "*":
                        continue
                    try:
                        numeric = float(raw_value)
                    except (TypeError, ValueError):
                        continue
                    parsed.append(numeric * (0.5 if low_conf else 1.0))
                if parsed:
                    values[(norm(nr[nfhs_district]), norm(nr[nfhs_state]))] = sum(parsed) / len(parsed)
                    pooled.extend(parsed)
            national_mean = sum(pooled) / len(pooled) if pooled else 0.0
            def relative_burden(row):
                raw_value = values.get((norm(row.nfhs_district), norm(row.nfhs_state)))
                if raw_value is None or national_mean <= 0:
                    return 1.0
                return min(2.0, max(0.5, raw_value / national_mean))
            joined[burden_col] = [relative_burden(row) for row in joined.itertuples()]
            joined[basis_col] = ",".join(str(c) for c in indicator_cols)
        else:
            joined[burden_col] = 1.0
            joined[basis_col] = "no mapped NFHS indicator; neutral factor"
    for service_key in NFHS_HINTS:
        burden_col, basis_col = f"nfhs_burden_{service_key}", f"nfhs_burden_basis_{service_key}"
        if burden_col not in joined:
            joined[burden_col] = 1.0
            joined[basis_col] = "region unavailable; neutral factor"
    region_cols = ["adm2_district", "adm2_state", "nfhs_district", "nfhs_state", "region_match_status"]
    burden_cols = [c for service_key in NFHS_HINTS for c in (f"nfhs_burden_{service_key}", f"nfhs_burden_basis_{service_key}")]
    return joined.reindex(columns=list(dict.fromkeys(original_columns + region_cols + burden_cols)))
