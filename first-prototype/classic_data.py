"""Databricks snapshot and scoring adapters for the map-backed Facility Finder."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import streamlit as st

from pipeline import EVIDENCE_FIELDS, _service_key, _term_match, rank_candidates
from traveltime_adapter import travel_time_matrix


FACILITY_TABLE = "additional_india_data.default.first_prototype_facilities"
METADATA_TABLE = "additional_india_data.default.first_prototype_snapshot_metadata"
_SQL_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]+")
_FIELD_SEPARATOR = re.compile(r"(?:^|\s*\|\s*)(" + "|".join(EVIDENCE_FIELDS) + r"):\s*")
_OBSERVED_RELEASE = {
    "fdr_reported", "dated_external_report", "direct_review", "explicit_fdr_description",
    "approved_source_recovery",
}
_WITHHELD_RELEASE = {"missing", "branch_identity_hold", "review_unresolved", "target_quality_hold", "withheld"}


@dataclass(frozen=True)
class SnapshotData:
    facilities: pd.DataFrame
    prepared_at: str
    source_count: int


@dataclass(frozen=True)
class RankedSearch:
    results: list[dict]
    routing_status: str
    routing_warning: str
    uses_routes: bool


@dataclass(frozen=True)
class CapacityDetails:
    label: str
    value: str
    detail: str
    source_url: str = ""
    crosscheck: str = ""


@dataclass(frozen=True)
class ServiceEvidence:
    term: str
    excerpt: str
    urls: tuple[str, ...]
    service: str


def _text(value):
    if value is None or value is pd.NA:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "null", "<na>", "nat"} else text


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def safe_url(value):
    """Accept only absolute HTTP(S) URLs safe to use as link targets."""
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


def _source_urls(row):
    for field in ("source_urls_json", "source_urls", "sourceUrls", "source_url", "sourceUrl"):
        values = row.get(field)
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except ValueError:
                pass
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, np.ndarray)):
            urls = tuple(dict.fromkeys(url for value in values if (url := safe_url(value))))
            if urls:
                return urls
    return ()


def _released_beds(row):
    status = _text(row.get("capacity_release_status")).casefold()
    beds = _number(row.get("beds_for_scenario"))
    if status in _WITHHELD_RELEASE or beds is None or beds <= 0:
        return None
    return beds


def _warehouse_path():
    path = os.getenv("DATABRICKS_HTTP_PATH") or os.getenv("DATABRICKS_WAREHOUSE_HTTP_PATH")
    warehouse = os.getenv("DATABRICKS_WAREHOUSE_ID")
    return path or (f"/sql/1.0/warehouses/{warehouse}" if warehouse else "")


def snapshot_configured():
    return bool(_warehouse_path())


@st.cache_data(ttl=300, show_spinner="Loading the prepared facility snapshot…")
def _load_snapshot(table_name: str, http_path: str):
    from databricks import sql
    from databricks.sdk.core import Config

    parts = table_name.split(".")
    if not 1 <= len(parts) <= 3 or not all(_SQL_IDENTIFIER.fullmatch(part) for part in parts):
        raise ValueError("The snapshot table must have a valid Databricks table name.")
    identifier = ".".join(f"`{part}`" for part in parts)
    cfg = Config()
    hostname = cfg.host.removeprefix("https://").removeprefix("http://").rstrip("/")
    with sql.connect(server_hostname=hostname, http_path=http_path,
                     credentials_provider=lambda: cfg.authenticate) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {identifier}")
            return pd.DataFrame(cursor.fetchall(), columns=[column[0] for column in cursor.description])


def load_classic_snapshot(from_facility_table):
    """Load Databricks resources and preserve the classic search and result schema."""
    path = _warehouse_path()
    if not path:
        raise RuntimeError("Configure a Databricks SQL warehouse resource to use this data source.")
    snapshot = _load_snapshot(os.getenv("FACILITY_SNAPSHOT_TABLE") or FACILITY_TABLE, path)
    if snapshot.empty:
        raise ValueError("The prepared Databricks snapshot contains no facility records.")

    prepared_at = ""
    try:
        metadata = _load_snapshot(os.getenv("SNAPSHOT_METADATA_TABLE") or METADATA_TABLE, path)
        if not metadata.empty:
            prepared_at = _text(metadata.iloc[0].get("prepared_at"))
    except Exception:
        # Some deployments grant access to the facility table alone.
        pass
    if not prepared_at and "snapshot_prepared_at" in snapshot:
        prepared_at = next((text for value in snapshot.snapshot_prepared_at if (text := _text(value))), "")
    return SnapshotData(snapshot_to_classic(snapshot, from_facility_table), prepared_at or "Unavailable", len(snapshot))


def _service_fields(value):
    """Recover the same evidence fields the existing snapshot combines into service_text."""
    text = _text(value)
    matches = list(_FIELD_SEPARATOR.finditer(text))
    if not matches:
        return {"description": text}
    fields = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields[match[1]] = text[match.end():end].strip()
    return fields


def snapshot_to_classic(snapshot, from_facility_table):
    """Adapt snapshot names, text, coordinates, and metadata through the classic matcher."""
    snapshot = snapshot.reset_index(drop=True)
    for field in ("latitude", "longitude"):
        if field not in snapshot:
            raise ValueError(f"The prepared Databricks snapshot is missing the {field} column.")

    rows = []
    for index, row in snapshot.iterrows():
        fields = _service_fields(row.get("service_text"))
        status = _text(row.get("capacity_release_status")).casefold()
        provenance = _text(row.get("beds_provenance")).casefold()
        observed = status in _OBSERVED_RELEASE or (not status and provenance == "reported")
        beds = _released_beds(row) if observed else None
        name = _text(row.get("facility_name")) or _text(row.get("name")) or "Unnamed facility"
        phone = _text(row.get("phone"))
        rows.append({
            **{field: fields.get(field) or _text(row.get(field)) for field in EVIDENCE_FIELDS},
            "name": name,
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "address_line1": row.get("address_line1"),
            "address_line2": row.get("address_line2"),
            "address_line3": row.get("address_line3"),
            "address_city": row.get("city"),
            "address_stateOrRegion": row.get("state"),
            "address_zipOrPostcode": row.get("pin"),
            "phone_numbers": re.split(r"\s+/\s+", phone),
            "hours": row.get("hours"),
            "capacity": beds if beds is not None else "",
            "operatorTypeId": row.get("operatorTypeId", row.get("ownership")),
            "occupancy": row.get("occupancy"),
            "last_verified": row.get("last_verified"),
            "accreditation": row.get("accreditation"),
            "rating": row.get("rating"),
            "source": _text(row.get("facility_display_source")) or "Prepared Databricks snapshot",
            "_snapshot_row": index,
        })
    facilities = from_facility_table(pd.DataFrame(rows))
    source = snapshot.loc[facilities.pop("_snapshot_row")].reset_index(drop=True)

    # Preserve release-specific fields and district records using each row's original position.
    for field in source.columns.difference(facilities.columns):
        facilities[field] = source[field]
    if "district" in source:
        district = source["district"].map(_text)
        facilities["district"] = district.where(district.ne(""), facilities["city"])
        facilities.attrs["district_from_city"] = not district.ne("").any()
    if "address" in source:
        address = source["address"].map(_text)
        facilities["address"] = address.where(address.ne(""), facilities["address"])
    if "facility_id" in source:
        ids = source["facility_id"].map(_text)
        facilities["facility_id"] = ids.where(ids.ne(""), facilities["facility_id"])
    if "source_id" in source:
        all_ids = snapshot["source_id"].map(_text)
        unique_ids = set(all_ids[all_ids.ne("") & ~all_ids.duplicated(keep=False)])
        source_ids = source["source_id"].map(_text)
        facilities["equity_facility_id"] = source_ids.map(
            lambda source_id: "fdr:" + source_id if source_id in unique_ids else ""
        )
    return facilities


def capacity_details(row):
    """Keep estimated, observed, and withheld release values distinguishable."""
    status = _text(row.get("capacity_release_status"))
    provenance = _text(row.get("beds_provenance"))
    beds = _released_beds(row)
    source = _text(row.get("capacity_release_source"))
    source_url = safe_url(row.get("capacity_release_source_url"))
    crosscheck = _text(row.get("capacity_crosscheck_status"))
    if crosscheck in {"match", "both blank"}:
        crosscheck = ""
    label = "Beds"
    value = "n/a"
    if beds is None:
        detail = f"Capacity unavailable or withheld ({status.replace('_', ' ') or 'missing'})."
    elif status.casefold().endswith("estimate") or provenance.casefold().startswith("model:"):
        label = "Beds (estimated)"
        value = f"~{beds:g}"
        detail = f"This is an estimated capacity of {beds:g} beds."
        lower = _number(row.get("capacity_prediction_lower"))
        upper = _number(row.get("capacity_prediction_upper"))
        if lower is not None and upper is not None and 0 <= lower <= upper:
            detail += f" Estimated interval {lower:g}–{upper:g} beds."
    else:
        observed = status.casefold() in _OBSERVED_RELEASE or provenance.casefold() == "reported"
        label = "Beds (reported)" if observed else "Beds (release)"
        value = f"{beds:g}"
        detail = f"Capacity value {beds:g} beds ({status.replace('_', ' ') or 'snapshot record'})."
    if source:
        detail += f" Source is {source}."
    return CapacityDetails(label, value, detail, source_url, crosscheck)


def service_evidence(row, service, related_services, service_aliases):
    """Find the text behind a classic match without filtering the result by citation."""
    urls = _source_urls(row)
    candidates = (service, *related_services)
    matched_services = row.get("service_list")
    for candidate in candidates:
        if isinstance(matched_services, (list, tuple)) and candidate not in matched_services:
            continue
        key = _service_key(candidate)
        term = _text(row.get(f"term_{key}"))
        excerpt = _text(row.get(f"excerpt_{key}"))
        if not term or not excerpt:
            text = _text(row.get("service_text"))
            if not text:
                text = " | ".join(
                    f"{field}: {value}" for field in EVIDENCE_FIELDS if (value := _text(row.get(field)))
                )
            aliases = service_aliases.get(candidate, ())
            if isinstance(aliases, dict):
                aliases = aliases.get("aliases", ())
            inferred_term, inferred_excerpt = _term_match(text, aliases)
            term = term or inferred_term
            excerpt = excerpt or inferred_excerpt
        if term:
            return ServiceEvidence(term, excerpt, urls, candidate)
    return ServiceEvidence("", "", urls, service)


@st.cache_data(ttl=1800, max_entries=32, show_spinner="Getting driving times from TravelTime…")
def _cached_travel_times(origin, records):
    destinations = pd.DataFrame(records, columns=["facility_id", "latitude", "longitude"])
    return travel_time_matrix(origin, destinations)


def rank_classic_results(classic_results, service, origin, *, population_share_pct, severity, use_traveltime):
    """Use prototype scoring on the candidate list already accepted by the classic UI."""
    status = "Straight-line distance mode"
    warning = ""
    if not classic_results:
        return RankedSearch([], status, warning, False)

    key = _service_key(service)
    rows = []
    for index, result in enumerate(classic_results):
        row = result["row"]
        district = _text(row.get("nfhs_district"))
        state = _text(row.get("nfhs_state"))
        rows.append({
            "facility_id": str(index),
            "latitude": float(row["lat"]),
            "longitude": float(row["lon"]),
            "beds_for_scenario": _released_beds(row),
            "confidence_score": _number(row.get("confidence_score")),
            "nfhs_district": district,
            "nfhs_state": state,
            "regional_population": _number(row.get("regional_population")) if district and state else None,
            "population_source": _text(row.get("population_source")),
            f"nfhs_burden_{key}": _number(row.get(f"nfhs_burden_{key}")),
            f"claim_citable_{key}": True,
            f"term_{key}": _text(row.get(f"term_{key}")),
            f"excerpt_{key}": _text(row.get(f"excerpt_{key}")),
            "source_urls_json": json.dumps(_source_urls(row)),
        })
    frame = pd.DataFrame(rows)

    travel_times = None
    if use_traveltime:
        records = tuple(frame[["facility_id", "latitude", "longitude"]].itertuples(index=False, name=None))
        try:
            estimates = _cached_travel_times(tuple(origin), records)
            reachable = {fid: minutes for fid, _, _ in records
                         if (minutes := _number(estimates.get(fid))) is not None and minutes >= 0}
            if reachable:
                travel_times = reachable
                status = f"TravelTime driving estimates ({len(reachable):,} reachable within 3 hours)"
            else:
                status = "Straight-line fallback"
                warning = "TravelTime returned no reachable facilities within 3 hours. Showing straight-line distances."
        except Exception:
            status = "Straight-line fallback"
            warning = "TravelTime was unavailable. Showing straight-line distances."

    ranked = rank_candidates(frame, service, tuple(origin), severity=severity.casefold(),
                             population_share_pct=population_share_pct, top_k=len(frame), travel_times=travel_times)
    results = []
    for _, scenario in ranked.iterrows():
        result = classic_results[int(scenario["facility_id"])].copy()
        result["scenario"] = scenario
        results.append(result)
    return RankedSearch(results, status, warning, travel_times is not None)
