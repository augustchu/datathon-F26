"""Databricks snapshot and scoring adapters for the map-backed Facility Finder."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import streamlit as st

from pipeline import EVIDENCE_FIELDS, _service_key, _term_match, classic_facility_details, eligible_facility_locations
from projected_load import TIME_CRITICAL_SERVICES, prefer_projected_load
from traveltime_adapter import travel_time_matrix


FACILITY_TABLE = "additional_india_data.default.first_prototype_facilities"
METADATA_TABLE = "additional_india_data.default.first_prototype_snapshot_metadata"
SNAPSHOT_CHECK_SECONDS = 60
_LOGGER = logging.getLogger(__name__)
_SQL_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]+")
_FIELD_SEPARATOR = re.compile(r"(?:^|\s*\|\s*)(" + "|".join(EVIDENCE_FIELDS) + r"):\s*")
_OBSERVED_RELEASE = {
    "fdr_reported", "dated_external_report", "direct_review", "explicit_fdr_description",
    "approved_source_recovery",
}
_WITHHELD_RELEASE = {"missing", "branch_identity_hold", "review_unresolved", "target_quality_hold", "withheld"}
_PROJECTION_BRANCH_ID = re.compile(r"fdr:[^:\s]+")
_PROJECTION_EVIDENCE_KINDS = {"corroborated", "hospital_site", "directory", "record_listing", "classic_exact"}


@dataclass(frozen=True)
class SnapshotData:
    facilities: pd.DataFrame
    prepared_at: str
    source_count: int
    revision: str = ""
    checked_at: str = ""


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


def _table_identifier(table_name):
    parts = table_name.split(".")
    if not 1 <= len(parts) <= 3 or not all(_SQL_IDENTIFIER.fullmatch(part) for part in parts):
        raise ValueError("The snapshot table must have a valid Databricks table name.")
    return ".".join(f"`{part}`" for part in parts)


def _query_snapshot(query, http_path, *, allow_server_error=False):
    from databricks import sql
    from databricks.sdk.core import Config
    from databricks.sql.exc import ServerOperationError

    cfg = Config()
    hostname = cfg.host.removeprefix("https://").removeprefix("http://").rstrip("/")
    with sql.connect(server_hostname=hostname, http_path=http_path,
                     credentials_provider=lambda: cfg.authenticate) as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(query)
            except ServerOperationError:
                if not allow_server_error:
                    raise
                _LOGGER.debug("Optional SQL snapshot operation unavailable", exc_info=True)
                return None
            return pd.DataFrame(cursor.fetchall(), columns=[column[0] for column in cursor.description])


@st.cache_data(ttl=SNAPSHOT_CHECK_SECONDS - 5, max_entries=8, show_spinner=False)
def _check_snapshot(table_name, http_path):
    identifier = _table_identifier(table_name)
    # A view or role with only current-read access may not support Delta history.
    history = _query_snapshot(f"DESCRIBE HISTORY {identifier} LIMIT 1", http_path, allow_server_error=True)
    latest = {} if history is None or history.empty else history.iloc[0]
    version = int(latest["version"]) if "version" in latest else None
    committed_at = _text(latest.get("timestamp"))
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return version, committed_at, checked_at


@st.cache_data(max_entries=4, show_spinner="Loading the prepared facility snapshot…")
def _load_snapshot(table_name, metadata_table, http_path, version, revision_key, _from_facility_table):
    identifier = _table_identifier(table_name)
    snapshot = None
    if version is not None:
        snapshot = _query_snapshot(f"SELECT * FROM {identifier} VERSION AS OF {version}", http_path,
                                   allow_server_error=True)
    exact_version = snapshot is not None
    if not exact_version:
        snapshot = _query_snapshot(f"SELECT * FROM {identifier}", http_path)
    if snapshot.empty:
        raise ValueError("The prepared Databricks snapshot contains no facility records.")

    prepared_at = ""
    if "snapshot_prepared_at" in snapshot:
        prepared_at = max(snapshot["snapshot_prepared_at"].map(_text), default="")
    if not prepared_at:
        try:
            metadata = _query_snapshot(f"SELECT * FROM {_table_identifier(metadata_table)}", http_path)
            if not metadata.empty:
                prepared_at = _text(metadata.iloc[0].get("prepared_at"))
        except Exception:
            _LOGGER.debug("Facility preparation metadata unavailable", exc_info=True)
    revision = f"{version}:{revision_key}"
    if not exact_version:
        # SQL need not return rows in the same order on successive reads.
        rows = snapshot.to_json(orient="records", lines=True, date_format="iso", double_precision=15)
        revision = hashlib.sha256("\n".join(sorted(rows.splitlines())).encode()).hexdigest()
    facilities = snapshot_to_classic(snapshot, _from_facility_table)
    return SnapshotData(facilities, prepared_at or "Unavailable", len(snapshot), revision)


def load_classic_snapshot(from_facility_table, *, force_refresh=False):
    """Check the live source, reusing normalized results until a new Delta version appears."""
    path = _warehouse_path()
    if not path:
        raise RuntimeError("Configure a Databricks SQL warehouse resource to use this data source.")
    table = os.getenv("FACILITY_SNAPSHOT_TABLE") or FACILITY_TABLE
    metadata = os.getenv("SNAPSHOT_METADATA_TABLE") or METADATA_TABLE
    if force_refresh:
        _check_snapshot.clear(table, path)
    version, committed_at, checked_at = _check_snapshot(table, path)
    # For a source without history, a new check reads and fingerprints the current table contents.
    args = (table, metadata, path, version, checked_at if version is None else committed_at)
    if force_refresh:
        _load_snapshot.clear(*args, from_facility_table)
    return replace(_load_snapshot(*args, from_facility_table), checked_at=checked_at)


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

    eligible = eligible_facility_locations(snapshot)
    if eligible.empty:
        return pd.DataFrame()
    rows = []
    for index, row in eligible.iterrows():
        fields = _service_fields(row.get("service_text"))
        status = _text(row.get("capacity_release_status")).casefold()
        provenance = _text(row.get("beds_provenance")).casefold()
        observed = status in _OBSERVED_RELEASE or (not status and provenance == "reported")
        beds = _released_beds(row) if observed else None
        name = _text(row.get("facility_name")) or _text(row.get("name")) or "Unnamed facility"
        phone = _text(row.get("phone"))
        details = classic_facility_details(row)
        rows.append({
            **{field: fields.get(field) or _text(row.get(field)) for field in EVIDENCE_FIELDS},
            "_snapshot_address": details.pop("address"),
            **details,
            "name": name,
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "address_city": row.get("city"),
            "address_stateOrRegion": row.get("state"),
            "address_zipOrPostcode": row.get("pin"),
            "phone_numbers": re.split(r"\s+/\s+", phone),
            "phone_source_url": safe_url(row.get("phone_source_url")) or safe_url(row.get("_phone_source_url")),
            "capacity": beds if beds is not None else "",
            "source": details["facility_display_source"] or "Prepared Databricks snapshot",
            "_snapshot_row": index,
        })
    raw = pd.DataFrame(rows)
    raw["last_verified"] = pd.to_datetime(raw["last_verified"], format="mixed", errors="coerce", utc=True).dt.tz_localize(None)
    percentage = raw["occupancy"].str.endswith("%")
    raw["occupancy"] = pd.to_numeric(raw["occupancy"].str.rstrip("%"), errors="coerce")
    raw.loc[percentage, "occupancy"] /= 100
    facilities = from_facility_table(raw)
    adapted_rows = facilities.pop("_snapshot_row")
    source = snapshot.loc[adapted_rows].reset_index(drop=True)
    address = facilities.pop("_snapshot_address")
    facilities["address"] = address.where(address.ne(""), facilities["address"])

    # Preserve release-specific fields and district records using each row's original position.
    for field in source.columns.difference(facilities.columns):
        facilities[field] = source[field]
    if "district" in source:
        district = source["district"].map(_text)
        facilities["district"] = district.where(district.ne(""), facilities["city"])
        facilities.attrs["district_from_city"] = not district.ne("").any()
    if "facility_id" in source:
        ids = source["facility_id"].map(_text)
        facilities["facility_id"] = ids.where(ids.ne(""), facilities["facility_id"])
    branch_ids = pd.Series("", index=snapshot.index)
    if "facility_id" in snapshot:
        # Count duplicate suffixes when determining whether evidence can be joined by exact branch.
        branch_ids = snapshot["facility_id"].map(_text).str.extract(r"^(fdr:[^:\s]+)(?::row\d+)?$")[0].fillna("")
    if "source_id" in snapshot:
        source_ids = snapshot["source_id"].map(_text)
        canonical = source_ids.where(source_ids.str.startswith("fdr:"), "fdr:" + source_ids)
        branch_ids = canonical.where(canonical.str.fullmatch(_PROJECTION_BRANCH_ID), branch_ids)
    unique_ids = branch_ids.where(branch_ids.ne("") & ~branch_ids.duplicated(keep=False), "")
    facilities["equity_facility_id"] = unique_ids.loc[adapted_rows].to_numpy()
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


def route_classic_results(classic_results, origin, *, use_traveltime):
    """Attach drive times to eligible candidates before the dashboard scores them."""
    if not classic_results or not use_traveltime:
        return RankedSearch(classic_results, "Straight-line distance mode", "", False)

    records = tuple(
        (str(index), float(result["row"]["lat"]), float(result["row"]["lon"]))
        for index, result in enumerate(classic_results)
    )
    try:
        estimates = _cached_travel_times(tuple(origin), records)
    except Exception:
        return RankedSearch(classic_results, "Straight-line fallback",
                            "TravelTime was unavailable. Using straight-line travel for ranking.", False)

    results = []
    for (facility_id, _, _), result in zip(records, classic_results):
        minutes = _number(estimates.get(facility_id))
        if minutes is not None and 0 <= minutes <= 180:
            results.append({**result, "route_minutes": minutes})
    if not results:
        return RankedSearch(classic_results, "Straight-line fallback",
                            "TravelTime returned no reachable facilities within 3 hours. Using straight-line travel for ranking.", False)
    status = f"TravelTime driving estimates ({len(results):,} reachable within 3 hours)"
    return RankedSearch(results, status, "", True)


def _projection_branch_id(row):
    """Return an unambiguous release branch ID from a prepared snapshot row."""
    source_id = _text(row.get("source_id"))
    canonical_id = f"fdr:{source_id}" if source_id else ""
    branch_id = _text(row.get("facility_id")) or canonical_id
    if not _PROJECTION_BRANCH_ID.fullmatch(branch_id):
        return ""
    if canonical_id and branch_id != canonical_id:
        return ""
    if "equity_facility_id" in row and _text(row.get("equity_facility_id")) != branch_id:
        return ""
    return branch_id


def apply_projected_load(candidates, service, *, uses_routes, enabled=True, severity="Routine"):
    """Apply the published exact-service projection to the existing recommendation order."""
    if not candidates or not enabled or severity.casefold() != "routine" or service in TIME_CRITICAL_SERVICES:
        return candidates, 0

    claim_field = f"service_evidence_kind_{_service_key(service)}"
    branch_ids = [_projection_branch_id(item["row"]) for item in candidates]
    branch_counts = Counter(branch_ids)
    matches = []
    for item, branch_id in zip(candidates, branch_ids):
        row = item["row"]
        confidence = item.get("service_confidence")
        if confidence is not None:
            # The branch-specific assessment can confirm a service absent from old keyword lists.
            evidence_kind = confidence.kind if confidence.match_score == 100 else ""
        elif item.get("match") == 1.0 and item.get("matched_service", service) == service:
            # Older callers may only provide the classic keyword match.
            evidence_kind = _text(row[claim_field]) if claim_field in row else "classic_exact"
        else:
            evidence_kind = ""
        distance = _number(item.get("dist"))
        route_minutes = _number(item.get("route_minutes"))
        if (evidence_kind not in _PROJECTION_EVIDENCE_KINDS or branch_counts[branch_id] != 1
                or distance is None or distance < 0 or (uses_routes and (route_minutes is None or route_minutes < 0))):
            branch_id = ""
        matches.append({
            "facility_id": branch_id,
            "service_evidence_kind": evidence_kind,
            "distance_km": distance,
            "travel_time_minutes": route_minutes,
            "base_travel_cost": route_minutes if uses_routes else distance,
        })

    ranked, comparable = prefer_projected_load(pd.DataFrame(matches), service, routed=uses_routes)
    results = []
    for index, projection in ranked.iterrows():
        item = candidates[index]
        caseload = _number(projection.get("projected_caseload"))
        capacity = _number(projection.get("projected_capacity"))
        if caseload is not None and caseload >= 0 and capacity is not None and capacity > 0:
            item = {**item, "projected_load": {
                "caseload": caseload,
                "capacity": capacity,
                "unit": projection["projected_unit"],
                "scope": projection["projected_scope"],
                "period": projection["projected_period"],
                "caseload_predicted": str(projection["projected_caseload_predicted"]).casefold() == "true",
                "capacity_predicted": str(projection["projected_capacity_predicted"]).casefold() == "true",
                "caseload_method": projection["projected_caseload_method"],
                "capacity_method": projection["projected_capacity_method"],
                "source_url": safe_url(projection["projected_source_url"]),
                "capacity_source_url": safe_url(projection["projected_capacity_source_url"]),
                "comparison_score": _number(projection.get("projected_load_comparison_score")),
                "pressure": _number(projection.get("projected_pressure")),
                "extra_travel": _number(projection.get("projected_extra_travel")),
                "baseline_rank": index + 1,
            }}
        results.append(item)
    return results, comparable
