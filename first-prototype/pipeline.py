"""Small, auditable preparation and ranking layer for the Databricks prototype."""
from __future__ import annotations

import json
import math
import re
import zipfile
from urllib.parse import urlparse

import numpy as np
import pandas as pd

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
    "city": ["address_city", "city", "addressCity"],
    "district": ["address_district", "district", "addressDistrict"],
    "state": ["address_stateOrRegion", "state", "state_name", "address_state"],
    "pin": ["address_zipOrPostcode", "postcode", "pincode", "pin", "postal_code"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon", "lng"],
    "capacity": ["capacity", "bed_capacity", "beds", "number_of_beds"],
    "id": ["unique_id", "facility_id", "id", "facilityId"],
    "url": ["source_urls", "sourceUrls", "source_url", "url", "website", "sources"],
}
EVIDENCE_FIELDS = ["specialties", "capability", "procedure", "equipment", "description"]
NEGATION = re.compile(r"\b(?:does not|do not|not available|not provide|without|no longer|unavailable|doesn't|isn't)\b", re.I)
NFHS_HINTS = {
    "dialysis": ["blood_sugar", "diabetes"],
    "emergency_c_section": ["institutional_birth", "csection", "caesarean"],
    "nicu": ["children_prev_symptoms", "diarrhoea", "diarrhea", "stunted", "underweight"],
    "cardiac_surgery": ["blood_sugar", "hypertension"],
    "maternity_delivery": ["institutional_birth", "csection", "caesarean"],
    "general_care": ["children_prev_symptoms", "diarrhoea", "diarrhea", "blood_sugar"],
}


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


def _term_match(text, terms):
    low = text.casefold()
    for term in sorted(terms, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)")
        m = pattern.search(low)
        if m:
            start, end = max(0, m.start() - 150), min(len(text), m.end() + 220)
            excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
            # Only suppress clear nearby negation; the rule intentionally favors review over claiming certainty.
            prefix = text[max(0, m.start() - 60):m.start()]
            if NEGATION.search(prefix):
                continue
            return term, excerpt
    return None, ""


def prepare_facilities(raw: pd.DataFrame, predicted_beds=None, model_label="not applied") -> pd.DataFrame:
    """Flatten source rows while retaining the source text and claim-level citations."""
    if raw.empty:
        raise ValueError("The facility source table returned no rows.")
    cols = {key: _col(raw, aliases) for key, aliases in FIELD_CANDIDATES.items()}
    if not cols["name"]:
        raise ValueError(f"Could not identify a facility name column. Available columns: {list(raw.columns)}")
    missing = [f for f in EVIDENCE_FIELDS if _col(raw, [f]) is None]
    id_seen = {}
    rows = []
    for pos, (_, source) in enumerate(raw.iterrows()):
        name_value = source[cols["name"]]
        name = "Unnamed facility" if pd.isna(name_value) else str(name_value)
        source_id = str(source[cols["id"]]) if cols["id"] and pd.notna(source[cols["id"]]) else str(pos)
        base_id = f"fdr:{source_id}"
        id_seen[base_id] = id_seen.get(base_id, 0) + 1
        fid = base_id if id_seen[base_id] == 1 else f"{base_id}:row{pos}"
        field_texts = []
        for field in EVIDENCE_FIELDS:
            col = _col(raw, [field])
            if col:
                values = _items(source[col])
                if values:
                    field_texts.append(f"{field}: {'; '.join(values)}")
        full_text = " | ".join(field_texts)
        urls = _urls(source[cols["url"]]) if cols["url"] else []
        beds = _number(source[cols["capacity"]]) if cols["capacity"] else None
        if (beds is None or beds <= 0) and predicted_beds is not None and fid in predicted_beds:
            beds = predicted_beds[fid]
            bed_source = model_label
        else:
            bed_source = "reported" if beds is not None else "missing"
        row = {
            "facility_id": fid, "source_id": source_id, "facility_name": name,
            "city": str(source[cols["city"]]) if cols["city"] and pd.notna(source[cols["city"]]) else "",
            "district": str(source[cols["district"]]) if cols["district"] and pd.notna(source[cols["district"]]) else "",
            "state": str(source[cols["state"]]) if cols["state"] and pd.notna(source[cols["state"]]) else "",
            "pin": str(source[cols["pin"]]) if cols["pin"] and pd.notna(source[cols["pin"]]) else "",
            "latitude": _number(source[cols["lat"]]) if cols["lat"] else None,
            "longitude": _number(source[cols["lon"]]) if cols["lon"] else None,
            "beds_for_scenario": beds, "beds_provenance": bed_source,
            "source_urls_json": json.dumps(urls), "service_text": full_text,
            "service_fields_missing": ",".join(missing),
        }
        for service, terms in SERVICE_TERMS.items():
            term, excerpt = _term_match(full_text, terms)
            # Citation is required for a result in the user-facing app.
            row[f"term_{_service_key(service)}"] = term or ""
            row[f"excerpt_{_service_key(service)}"] = excerpt if urls else ""
            row[f"claim_citable_{_service_key(service)}"] = bool(term and urls)
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


def rank_candidates(frame, service, origin, severity="routine", intensity=0, top_k=5):
    """Rank only cited text matches; return score components for transparent rendering."""
    key = _service_key(service)
    term_col, excerpt_col, cite_col = f"term_{key}", f"excerpt_{key}", f"claim_citable_{key}"
    if cite_col not in frame.columns:
        return pd.DataFrame()
    work = frame.loc[frame[cite_col].fillna(False).astype(bool)].copy()
    work = work.dropna(subset=["latitude", "longitude"])
    work = work[work.latitude.between(6, 38) & work.longitude.between(68, 98)]
    if work.empty:
        return work
    work["distance_km"] = work.apply(lambda r: haversine_km(origin[0], origin[1], float(r.latitude), float(r.longitude)), axis=1)
    intensity = max(0, int(intensity))
    work["simulated_cases"] = intensity
    work["severity"] = severity
    work["beds_for_scenario"] = pd.to_numeric(work.beds_for_scenario, errors="coerce")
    if severity == "emergency":
        # The user's emergency routing stays nearest-first. Scenario outputs are shown for audit.
        work["simulated_load"] = 0.0
        work["load_penalty"] = 0.0
        work["score"] = work.distance_km
    else:
        # Allocate synthetic routine cases one-by-one. Each case chooses the current
        # lowest distance + load score, then contributes one scenario unit there.
        # Total bed count is only a coarse scale proxy, never service availability.
        loads = {fid: 0.0 for fid in work.facility_id}
        capacities = {r.facility_id: (float(r.beds_for_scenario) if pd.notna(r.beds_for_scenario) and float(r.beds_for_scenario) > 0 else 50.0)
                      for r in work.itertuples()}
        distances = {r.facility_id: float(r.distance_km) for r in work.itertuples()}
        burden_col = f"nfhs_burden_{key}"
        if burden_col in work.columns:
            work["regional_demand_factor"] = pd.to_numeric(work[burden_col], errors="coerce").fillna(1.0).clip(0.5, 2.0)
        else:
            work["regional_demand_factor"] = 1.0
        burden_factors = dict(zip(work.facility_id, work.regional_demand_factor.astype(float)))
        ids = list(work.facility_id)
        for _ in range(intensity):
            chosen = min(ids, key=lambda fid: distances[fid] + loads[fid] * burden_factors[fid] * 12.0 / math.sqrt(capacities[fid]))
            loads[chosen] += 1.0
        work["simulated_load"] = work.facility_id.map(loads)
        work["load_penalty"] = work.simulated_load * work.regional_demand_factor * 12.0 / np.sqrt(work.beds_for_scenario.clip(lower=1).fillna(50.0))
        work["score"] = work.distance_km + work.load_penalty
    work["matched_term"] = work[term_col]
    work["supporting_excerpt"] = work[excerpt_col]
    work["source_urls"] = work.source_urls_json.map(lambda x: json.loads(x) if isinstance(x, str) else [])
    return work.sort_values(["score", "distance_km"], ascending=True).head(int(top_k)).reset_index(drop=True)


def resolve_location(frame, location_text="", lat=None, lon=None):
    if lat is not None and lon is not None:
        lat, lon = float(lat), float(lon)
        if not (6 <= lat <= 38 and 68 <= lon <= 98):
            raise ValueError("Coordinates must fall within the India bounds used by this prototype.")
        return (lat, lon), "User-provided coordinates"
    q = re.sub(r"\s+", " ", str(location_text or "")).strip().casefold()
    if not q:
        raise ValueError("Enter a city, district, PIN code, or coordinates.")
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
    hit = hit.dropna(subset=["latitude", "longitude"])
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
