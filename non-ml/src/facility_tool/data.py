"""Read saved facility records, link branches, and extract scoped claims.

Raw values never get overwritten. Inputs are the committed per-source JSONL
files and, optionally, an in-memory snapshot of the original facilities table.
No network or Databricks access occurs here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from hashlib import sha256
import json
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qs, urldefrag, urlsplit

import pandas as pd

from .geography import india_point
from .profiles import plausible_phone


GOVERNMENT_SCOPES = {
    "bis_cghs": "BIS cashless empanelment list (Delhi/NCR)",
    "esic": "ESIC empanelment circular",
    "cmchis": "Tamil Nadu CMCHIS scheme directory",
    "kerala_sha": "Kerala SHA Sruthitharangam programme",
    "rghs": "Rajasthan RGHS scheme directory",
}
SUPPLEMENTAL_SCOPES = {
    "nqas": "NHSRC certification listing",
    "nabh": "NABH directory listing",
    "eraktkosh": "eRaktKosh directory type; no inventory or availability",
}
FDR_SERVICE_FIELDS = ("specialties", "procedure", "equipment", "capability", "description")
CMCHIS_RECORD_ENDPOINT = "https://chtn.cmchistn.com/api/v1/tpa-hospitals-v2/"
GOVERNMENT_PDF_SOURCES = {"bis_cghs", "esic", "rghs"}
SAVED_PHONE_FIELDS = {"eraktkosh": ("hospital_code", "phone"), "nabh": ("source_record_id", "facility_phone")}
RECORD_COLUMNS = (
    "record_id", "source", "source_record_id", "input_position", "name", "address", "city", "district", "state",
    "pin", "lat", "lon", "location_quality", "location_conflict", "coordinate_note", "phones", "emails",
    "source_urls", "service_texts", "scope", "validity_text", "raw",
)
ENTITY_COLUMNS = (
    "facility_id", "name", "address", "city", "district", "state", "pin", "lat", "lon", "location_quality",
    "location_conflict", "base_record_id", "location_record_id", "base_source",
)
LINK_COLUMNS = ("record_id", "facility_id", "link_status", "link_reason", "candidate_facility_ids")
CLAIM_COLUMNS = (
    "facility_id", "record_id", "service", "stance", "excerpt", "source_url", "source_urls", "source", "scope",
    "validity", "validity_text", "validity_start", "validity_end", "evidence_field", "citation_quality", "match_reason",
)
DISTRICT_RE = re.compile(r";\s*District\s*:\s*(.*?)\s*$", re.IGNORECASE)
DATE_TOKEN = r"(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2})"
VALIDITY_RANGE = re.compile(rf"({DATE_TOKEN})\s*(?:to|through|until|[–—])\s*({DATE_TOKEN})", re.IGNORECASE)
INACTIVE = re.compile(r"\b(?:exit(?:ed)?|suspend(?:ed)?|withdrawn|cancelled|inactive|not interested|non[- ]renewing)\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:no|not(?!\s+(?:only|just)\b)|doesn t|don t|without|exclude[sd]?|excluding|unavailable|unoffered|discontinued)\b")
WEAK_CONTEXT = re.compile(r"\b(?:refer(?:red|s|ral|rals)?|outsourc(?:e|ed|es|ing)|planned|proposed|upcoming|under construction|potential|history of|never|training|conference|awareness)\b")
OFFER_CONTEXT = re.compile(r"\b(?:offer(?:s|ed)?|provid(?:es?|ed|ing)|operat(?:es?|ed|ing)|perform(?:s|ed)?|availab(?:le|ility)|services?|treatment|unit|centre|center|department|including|conduct(?:s|ed)?)\b")
EXPLICIT_OFFER = re.compile(r"\b(?:offers?|provides?|operates?|performs?|available|conducts?)\b")
EQUIPMENT_CONTEXT = re.compile(r"\b(?:machines?|equipment|devices?|scanners?|apparatus)\b")
SPACE = re.compile(r"\s+")


def _text(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and not isfinite(value):
        return ""
    return str(value)


def _array(value):
    """Expose exact source strings, including members of JSON-encoded arrays."""
    if isinstance(value, (tuple, list)):
        return [item for member in value for item in _array(member)]
    if hasattr(value, "tolist") and not isinstance(value, str):
        return _array(value.tolist())
    raw = _text(value)
    if not raw.strip() or raw.strip().casefold() in {"null", "none", "nan", "n/a", "<na>"}:
        return []
    if raw.strip().startswith("["):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return [raw]
        if isinstance(parsed, list):
            return _array(parsed)
    return [raw]


def _key(value):
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return SPACE.sub(" ", "".join(ch if ch.isalnum() else " " for ch in normalized)).strip()


def _fingerprint(raw):
    material = json.dumps(raw, default=str, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()[:20]


def _jsonl(path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON in {path.name}, line {line_number}") from error
            if not isinstance(row, dict):
                raise ValueError(f"Expected a JSON object in {path.name}, line {line_number}")
            yield row


def _postal_code(value, address):
    posted = re.fullmatch(r"([1-9]\d{5})(?:\.0+)?", _text(value).strip())
    if posted:
        return posted.group(1)
    explicit = re.findall(r"\bPIN(?:\s*CODE)?\s*[-:]?\s*([1-9]\d{2})\s*(\d{3})(?!\d)", address, flags=re.IGNORECASE)
    found = {part1 + part2 for part1, part2 in explicit}
    found.update(re.findall(r"(?<!\d)[1-9]\d{5}(?!\d)", address))
    return next(iter(found)) if len(found) == 1 else ""


def _point(raw):
    latitude = _text(raw.get("latitude")).strip()
    longitude = _text(raw.get("longitude")).strip()
    if not latitude or not longitude:
        return None, None, "missing"
    try:
        lat, lon = float(latitude), float(longitude)
    except ValueError:
        return None, None, "invalid"
    # A deliberately broad bounding box rejects obvious wrong-country points.
    # A point inside it still does not establish a geocoded facility address.
    if not india_point(lat, lon):
        return None, None, "invalid"
    return lat, lon, "source_coordinates_unverified"


def _urls(value):
    found = [url.rstrip(".;)") for item in _array(value) for url in re.findall(r"https?://[^\s'\"\[\]{}<>,]+", item)]
    return list(dict.fromkeys(found))


def _government_geography(raw):
    address = _text(raw.get("location"))
    district_match = DISTRICT_RE.search(address)
    district = district_match.group(1) if district_match else ""
    return address, district


def _saved_phone_rows(repo, source, id_field):
    path = repo / "data" / "external" / source / "facilities.jsonl"
    indexed = {}
    if path.is_file():
        for row in _jsonl(path):
            identity = _text(row.get(id_field)).strip()
            if identity:
                indexed[identity] = None if identity in indexed else row
    return indexed


def _matching_public_phones(record, saved, phone_field):
    if saved is None:
        return []
    name, address = _key(record["name"]), _key(record["address"])
    urls = _urls(saved.get("source_url"))
    if (
        not name or not address or not urls or urls != record["source_urls"]
        or name != _key(saved.get("facility_name")) or address != _key(saved.get("address"))
        or not all(urlsplit(url).hostname for url in urls)
    ):
        return []
    return [value for value in _array(saved.get(phone_field)) if plausible_phone(value)]


def _make_record(source, raw, position):
    if source == "fdr":
        name = _text(raw.get("name"))
        address = ", ".join(part for key in ("address_line1", "address_line2", "address_line3", "address_city", "address_stateOrRegion", "address_zipOrPostcode") if (part := _text(raw.get(key)).strip()))
        city, district, state = _text(raw.get("address_city")), "", _text(raw.get("address_stateOrRegion"))
        urls = _urls(raw.get("source_urls"))
        fields = [(column, text) for column in FDR_SERVICE_FIELDS for text in _array(raw.get(column))]
        phones = _array(raw.get("phone_numbers")) + _array(raw.get("officialPhone"))
        emails = _array(raw.get("email"))
        scope = "FDR-recorded claim"
    elif source in GOVERNMENT_SCOPES:
        name = _text(raw.get("facility_name"))
        address, district = _government_geography(raw)
        city, state = "", ""
        urls = _urls(raw.get("source_links"))
        fields = [("services", text) for text in _array(raw.get("services"))]
        phones = _array(raw.get("phone_numbers"))
        emails = _array(raw.get("email_addresses"))
        scope = GOVERNMENT_SCOPES[source]
    else:
        name = _text(raw.get("facility_name"))
        address, state = _text(raw.get("address")), _text(raw.get("state"))
        city, district = "", ""
        urls = _urls(raw.get("source_url"))
        fields = [("facility_type", text) for text in _array(raw.get("facility_type"))] if source == "eraktkosh" else []
        phones, emails = [], []
        scope = SUPPLEMENTAL_SCOPES[source]

    lat, lon, quality = _point(raw)
    # This specific contradiction was retained and documented in the SHA extract.
    conflict = source == "kerala_sha" and "ascent" in _key(name) and "malappuram" in _key(district) and "kozhikode" in _key(address)
    return {
        "source": source,
        "source_record_id": _text(raw.get("unique_id" if source == "fdr" else "source_record_id")),
        "input_position": position if source == "fdr" else None,
        "name": name,
        "address": address,
        "city": city,
        "district": district,
        "state": state,
        "pin": _postal_code(raw.get("address_zipOrPostcode"), address),
        "lat": lat,
        "lon": lon,
        "location_quality": "conflict" if conflict else quality,
        "location_conflict": conflict,
        "coordinate_note": _text(raw.get("coordinate_quality")),
        "phones": list(dict.fromkeys(phones)),
        "emails": list(dict.fromkeys(emails)),
        "source_urls": urls,
        "service_texts": fields,
        "scope": scope,
        "validity_text": _array(raw.get("validity_dates")) or _array(raw.get("validity_date_text")),
        "raw": raw,
    }


def load_records(repo_root, fdr: pd.DataFrame | None = None, *, include_public=True) -> pd.DataFrame:
    """Read the per-source exports once; never read their CSV or combined mirrors.

    Empty snapshots (e.g. RGHS) are recorded in `attrs['source_counts']`. A
    missing snapshot raises a file error rather than silently hiding a source.
    Saved raw eRaktKosh and NABH rows may supply a cited facility phone by ID.
    Set `include_public=False` to keep a synthetic FDR fixture fully isolated.
    """
    repo = Path(repo_root)
    snapshots = [(source, repo / "data" / "government_sources" / f"{source}.jsonl") for source in GOVERNMENT_SCOPES]
    snapshots += [(source, repo / "data" / "supplemental" / f"{source}.jsonl") for source in SUPPLEMENTAL_SCOPES]
    sources = [(source, _jsonl(path)) for source, path in snapshots] if include_public else []
    if fdr is not None:
        if "name" not in fdr.columns:
            raise ValueError("The FDR snapshot needs the original 'name' column.")
        sources.insert(0, ("fdr", fdr.to_dict(orient="records")))

    records = []
    coverage = {source: 0 for source, _ in sources}
    for source, rows in sources:
        phone_rows, phone_field, id_counts = {}, "", {}
        if source in SAVED_PHONE_FIELDS:
            id_field, phone_field = SAVED_PHONE_FIELDS[source]
            phone_rows = _saved_phone_rows(repo, source, id_field)
            if phone_rows:
                rows = list(rows)
                id_counts = Counter(_text(row.get("source_record_id")).strip() for row in rows)
        for position, raw in enumerate(rows):
            record = _make_record(source, raw, position)
            source_id = record["source_record_id"].strip()
            if id_counts.get(source_id) == 1:
                record["phones"] = list(dict.fromkeys(_matching_public_phones(record, phone_rows.get(source_id), phone_field)))
            identity = record["source_record_id"] or _fingerprint(raw)
            record["record_id"] = f"{source}:{identity}"
            records.append(record)
            coverage[source] += 1

    repetitions = Counter(row["record_id"] for row in records)
    occurrences = Counter()
    for row in records:
        key = row["record_id"]
        if repetitions[key] > 1:
            key = f"{key}:{_fingerprint(row['raw'])}"
            occurrences[key] += 1
            row["record_id"] = key if occurrences[key] == 1 else f"{key}:{occurrences[key]}"
    result = pd.DataFrame(records, columns=RECORD_COLUMNS)
    result.attrs["source_counts"] = coverage
    return result


def _has_point(record):
    return record["location_quality"] == "source_coordinates_unverified" and pd.notna(record["lat"]) and pd.notna(record["lon"])


def _distance_meters(a, b):
    lat1, lat2 = radians(a["lat"]), radians(b["lat"])
    delta_lat, delta_lon = lat1 - lat2, radians(a["lon"] - b["lon"])
    half_chord = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 12_742_000 * asin(sqrt(min(1.0, half_chord)))


def _conflicting_fields(a, b):
    if any(a[key] and b[key] and _key(a[key]) != _key(b[key]) for key in ("pin", "city", "district")):
        return True
    state_a, state_b = (_key(row["state"]).removeprefix("new ") for row in (a, b))
    return bool(state_a and state_b and state_a != state_b)


def _match_reason(a, b):
    if a["location_conflict"] or b["location_conflict"] or _conflicting_fields(a, b):
        return ""
    distance = _distance_meters(a, b) if _has_point(a) and _has_point(b) else None
    # Even verbatim addresses should not transfer evidence across contradictory points.
    if distance is not None and distance > 250:
        return ""
    address_a = _key(DISTRICT_RE.sub("", a["address"]))
    address_b = _key(DISTRICT_RE.sub("", b["address"]))
    if address_a == address_b and len(address_a.split()) >= 4:
        return "exact normalized name and full address"
    if a["pin"] and a["pin"] == b["pin"]:
        place_words = set(_key(" ".join((a["city"], a["state"], a["district"], b["city"], b["state"], b["district"], a["pin"]))).split())
        place_words.update({"pin", "code", "india", "district"})
        for source_record, other_address in ((a, address_b), (b, address_a)):
            street = _key(" ".join(_text(source_record["raw"].get(f"address_line{i}")) for i in (1, 2, 3)))
            exact_prefix = other_address == street or other_address.startswith(street + " ")
            suffix = other_address[len(street):].split()
            if exact_prefix and len(street.split()) >= 3 and re.search(r"\d", street) and set(suffix) <= place_words:
                return "exact normalized name, street address and PIN"
    if distance is not None and distance <= 100:
        return "exact normalized name and source coordinates within 100 m"
    return ""


def _could_be_same_branch(record, anchor):
    """Only plausible same-area pairs go into review; names across towns are normal."""
    same_pin = bool(record["pin"] and record["pin"] == anchor["pin"])
    same_address = bool(record["address"] and _key(record["address"]) == _key(anchor["address"]))
    return same_pin or same_address


def link_entities(records: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach only uniquely matching exact-name branches to an anchor.

    A full address match or consistent nearby source coordinates are required.
    A same-name, same-PIN pair without branch evidence is reviewable rather than
    pooled. Comparisons use original anchors, so a chain of weak matches cannot
    move a claim to an unrelated branch.
    """
    if records["record_id"].duplicated().any():
        raise ValueError("Record identifiers must be unique before linkage.")
    rows = sorted(records.to_dict(orient="records"), key=lambda row: (row["source"] != "fdr", row["record_id"]))
    entities = {}
    anchors_by_name = defaultdict(list)
    links = []
    for record in rows:
        name = _key(record["name"])
        possible = anchors_by_name.get(name, ()) if name else ()
        matches = [(anchor, reason) for anchor in possible if (reason := _match_reason(record, anchor))]
        if len(matches) == 1:
            anchor, reason = matches[0]
            facility_id = anchor["record_id"]
            existing = entities[facility_id]
            if existing["location_quality"] != "conflict" and not _has_point(existing) and _has_point(record):
                for field in ("lat", "lon", "location_quality"):
                    existing[field] = record[field]
                existing["location_record_id"] = record["record_id"]
            elif _has_point(existing) and _has_point(record) and _distance_meters(existing, record) > 250:
                existing["location_quality"] = "conflict"
                existing["location_conflict"] = True
            links.append((record["record_id"], facility_id, "linked", reason, [facility_id]))
            continue

        candidates = [anchor["record_id"] for anchor, _ in matches]
        if not candidates:
            candidates = [anchor["record_id"] for anchor in possible if _could_be_same_branch(record, anchor)]
        if candidates:
            reason = "multiple branch matches" if matches else "same-name location evidence is insufficient or contradictory"
            links.append((record["record_id"], "", "ambiguous", reason, candidates))
            continue

        record_id = record["record_id"]
        entity = {key: record[key] for key in ("name", "address", "city", "district", "state", "pin", "lat", "lon", "location_quality", "location_conflict")}
        entity.update(facility_id=record_id, base_record_id=record_id, location_record_id=record_id if _has_point(record) else "", base_source=record["source"])
        entities[record_id] = entity
        if name:
            anchors_by_name[name].append(record)
        status = "anchor" if record["source"] == "fdr" else "source_only"
        links.append((record_id, record_id, status, "no uniquely supported cross-record match", []))

    return pd.DataFrame(entities.values(), columns=ENTITY_COLUMNS), pd.DataFrame(links, columns=LINK_COLUMNS)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _parse_date(token):
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            return date.fromisoformat(token)
        day, month, year = (int(part) for part in re.split(r"[./-]", token))
        return date(year, month, day)
    except ValueError:
        return None


def _validity(record, as_of):
    dates = record["validity_text"]
    if any(INACTIVE.search(text) for text in dates):
        return "inactive", "", ""
    ranges = {(_parse_date(start), _parse_date(end)) for text in dates for start, end in VALIDITY_RANGE.findall(text)}
    if len(ranges) != 1:
        return "unknown", "", ""
    start, end = next(iter(ranges))
    if not start or not end or end < start:
        return "unknown", "", ""
    state = "future" if as_of < start else "expired" if as_of > end else "active"
    return state, start.isoformat(), end.isoformat()


def _compile_aliases(aliases):
    patterns = {}
    for service, values in aliases.items():
        if isinstance(values, str):
            raise TypeError(f"Aliases for {service!r} must be a list, not a string.")
        tokens = {_key(value) for value in values} | {_key(service)}
        tokens.discard("")
        if not tokens:
            raise ValueError("Service and alias names cannot all be empty.")
        patterns[service] = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in sorted(tokens, key=lambda word: (-len(word), word))) + r")\b")
    return patterns


def _interpret(clause, match, field, source):
    left = clause[max(0, match.start() - 52):match.start()]
    right = clause[match.end():match.end() + 42]
    if NEGATION.search(left) or re.match(r"\s*(?:(?:is|are|currently)\s+)*(?:not(?!\s+(?:only|just)\b)|unavailable|excluded|discontinued)\b", right):
        return "contradicts", "explicit negation or exclusion in the source clause"
    if WEAK_CONTEXT.search(left + " " + right):
        return "weak", "referral, planned treatment, or other qualified mention"
    if field == "equipment" or EQUIPMENT_CONTEXT.search(right[:20]):
        return "weak", "equipment wording alone does not document a service"
    if field == "description" and not OFFER_CONTEXT.search(left + " " + right):
        return "weak", "mention in a description without an explicit provision signal"
    if source in GOVERNMENT_SCOPES:
        return "supports", "service appears within the scheme's listed scope"
    if field == "facility_type":
        return "supports", "exact listed eRaktKosh directory type"
    return "supports", "explicit matching wording in an original FDR field"


def _clauses(excerpt):
    """Carry possible negation across comma-separated items, not sentences."""
    for sentence in re.split(r"[;.!?\n]+", excerpt):
        previous_negation = False
        for part in sentence.split(","):
            clause = _key(part)
            if not clause:
                continue
            negated = bool(NEGATION.search(clause))
            if EXPLICIT_OFFER.search(clause) and not negated:
                previous_negation = False
            yield clause, previous_negation
            previous_negation |= negated


def _service_citation(source, urls):
    if not urls:
        return ""
    if source == "cmchis":
        # The public listings page is shared by every hospital. The search URL
        # locates the record whose service flags produced the saved excerpt.
        entries = [
            url for url in urls if url.startswith(CMCHIS_RECORD_ENDPOINT + "?")
            and len(parse_qs(urlsplit(url).query).get("search", [])) == 1
        ]
        return entries[0] if len(entries) == 1 else ""
    if len(urls) == 1:
        return urls[0]
    if source in GOVERNMENT_PDF_SOURCES:
        # A wrapped listing can span pages. Cite its document when the saved
        # extract cannot tie one exact page to this specific service excerpt.
        documents = {urldefrag(url).url for url in urls}
        return next(iter(documents)) if len(documents) == 1 else ""
    return "" if source == "fdr" else urls[0]


def extract_claims(records: pd.DataFrame, links: pd.DataFrame, aliases, as_of) -> pd.DataFrame:
    """Extract exact, scoped service evidence; missing text is never a negative.

    `aliases` maps canonical service labels to explicit equivalent phrases.
    Specialty-related phrases should only be supplied when they mean the same
    service, not when they merely suggest it may be available.
    """
    cutoff = _as_date(as_of)
    patterns = _compile_aliases(aliases)
    attached = links.loc[links["facility_id"].fillna("").ne(""), ["record_id", "facility_id"]]
    trace = records.merge(attached, on="record_id", how="inner", validate="one_to_one")
    claims = []
    for record in trace.to_dict(orient="records"):
        if not record["service_texts"]:
            continue
        validity, start, end = _validity(record, cutoff)
        urls = record["source_urls"]
        source_url = _service_citation(record["source"], urls)
        citation_quality = "direct" if source_url else "candidate_links" if urls else "missing"
        for field, excerpt in record["service_texts"]:
            clauses = list(_clauses(excerpt))
            for service, pattern in patterns.items():
                interpretations = {}
                for clause, nearby_exclusion in clauses:
                    if field == "facility_type":
                        if clause == "bsu":
                            clause = "blood storage unit"
                        match = pattern.fullmatch(clause)
                        matches = (match,) if match else ()
                    else:
                        matches = pattern.finditer(clause)
                    for match in matches:
                        stance, reason = _interpret(clause, match, field, record["source"])
                        if stance == "supports" and nearby_exclusion:
                            stance, reason = "weak", "nearby exclusion may also apply to this list item"
                        interpretations.setdefault(stance, reason)
                for stance, reason in interpretations.items():
                    claims.append({
                        "facility_id": record["facility_id"], "record_id": record["record_id"], "service": service,
                        "stance": stance, "excerpt": excerpt, "source_url": source_url, "source_urls": urls,
                        "source": record["source"], "scope": record["scope"], "validity": validity,
                        "validity_text": record["validity_text"], "validity_start": start, "validity_end": end,
                        "evidence_field": field, "citation_quality": citation_quality, "match_reason": reason,
                    })
    return pd.DataFrame(claims, columns=CLAIM_COLUMNS)
