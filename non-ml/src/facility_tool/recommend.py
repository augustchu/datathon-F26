"""Rank cited service matches and compare three ways to show facility fields."""

from __future__ import annotations

from datetime import date
import json
from math import asin, cos, isfinite, radians, sin, sqrt
from numbers import Integral, Real
import re
from urllib.parse import urlparse

import pandas as pd

MODES = ("confirmed", "ml", "regression")
EVIDENCE_TIERS = {"active": 0, "unknown": 1}
OBSERVED_METHODS = {"reported", "source_recovered"}
DISTANCE_METRIC = "straight_line_km"
ROUTE_METRIC = "route_minutes"
ROUTE_DISTANCE_METRIC = "route_distance_km"
ROUTE_SORT_METRICS = {"time": ROUTE_METRIC, "distance": ROUTE_DISTANCE_METRIC}
TRAVEL_LABELS = {
    DISTANCE_METRIC: "straight-line distance", ROUTE_METRIC: "route time", ROUTE_DISTANCE_METRIC: "route distance",
}
ROUTE_VALUE_FIELDS = {ROUTE_METRIC: "travel_minutes", ROUTE_DISTANCE_METRIC: "route_distance_km"}
IDENTITY_COLUMNS = (
    "facility_id", "name", "address", "city", "district", "state", "pin",
    "lat", "lon", "location_quality",
)
LOCATION_AUDIT_COLUMNS = (
    "location_routing_eligible", "location_hold_reason", "location_source_urls", "location_review_flags",
)
QUALITY_EVIDENCE_COLUMNS = ("quality_corroborated", "quality_assessed")
PATIENT_EVIDENCE_COLUMNS = ("patient_experience_value", "patient_comparison_key", "patient_experience_status")
QUALITY_RANK_COLUMNS = (
    "travel_only_rank", "ranking_policy", "ranking_policy_reason",
    "corroboration_credit", "assessment_credit", "quality_credit", "quality_credit_unit", "balanced_travel_score",
    "patient_experience_credit", "total_quality_experience_credit", "patient_ranking_status", "patient_ranking_reason",
    "patient_tier_eligible", "patient_tier_candidates",
)
CLAIM_COLUMNS = (
    "facility_id", "record_id", "service", "stance", "excerpt", "source_url",
    "source", "scope", "validity",
)
UPDATE_COLUMNS = ("facility_id", "field", "method", "value")
MEASURED_LOAD_COLUMNS = ("facility_id", "service", "unit", "as_of", "source_url", "available_units")
PRESSURE_COLUMNS = ("facility_id", "service", "demand", "capacity", "unit", "period", "basis", "source_url")
TRAVEL_COLUMNS = ("facility_id", "travel_source")
REASON_COLUMNS = (
    "distance_km", "distance_metric", "evidence_tier", "excerpt", "source_url",
    "source", "scope", "validity", "validity_text", "citation_quality",
    "record_id", "claims_json", "reason", "review_reason",
)
FIELD_COLUMNS = (
    "capacity", "capacity_method", "capacity_unit", "capacity_lower", "capacity_upper",
    "capacity_provenance", "capacity_detail", "numberDoctors", "numberDoctors_method",
    "numberDoctors_unit", "numberDoctors_lower", "numberDoctors_upper",
    "numberDoctors_provenance", "numberDoctors_detail", "field_updates_json",
)
RESULT_COLUMNS = (
    "mode", "service", "kind", "decision", "selected", "rank", "baseline_rank",
    *IDENTITY_COLUMNS, *LOCATION_AUDIT_COLUMNS, *QUALITY_EVIDENCE_COLUMNS, *PATIENT_EVIDENCE_COLUMNS,
    *QUALITY_RANK_COLUMNS,
    "evidence_as_of", *REASON_COLUMNS, *FIELD_COLUMNS,
    "model_lead_score", "model_lead_provenance", "model_lead_detail",
    "load_status", "load_available_units", "load_unit", "load_as_of", "load_source_url",
    "extra_km_vs_baseline", "route_distance_km", "travel_minutes", "travel_mode",
    "travel_source", "travel_source_url", "route_sort", "ranking_travel_metric", "ranking_travel_reason",
    "extra_route_km_vs_baseline", "extra_minutes_vs_baseline", "pressure_demand", "pressure_capacity", "pressure_unit",
    "pressure_period", "pressure_basis", "pressure_source_url", "pressure_ratio", "pressure_grade",
    "travel_penalty", "pressure_penalty", "travel_weight", "pressure_weight", "weighted_score",
    "weighted_travel_metric", "weighted_extra_travel", "weighted_detour_limit", "weighted_status",
)
_REQUIRED_PREPARED_COLUMNS = (
    "service", *IDENTITY_COLUMNS, *LOCATION_AUDIT_COLUMNS, "site_lat", "site_lon",
    "evidence_as_of", "evidence_tier", "evidence_hold_reason", "service_contradiction",
    "claim", "claims_json", "mode_fields", "model_leads", "snapshot_entity_count", "snapshot_model_methods",
)
PREPARED_COLUMNS = (*_REQUIRED_PREPARED_COLUMNS, *QUALITY_EVIDENCE_COLUMNS, *PATIENT_EVIDENCE_COLUMNS)
_DEFAULT_LOCATION_HOLD = "Facility coordinates are missing, ambiguous, or too coarse"


def _text(value) -> str:
    if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
        return ""
    return str(value).strip()


def _number(value):
    try:
        number = float(value) if not pd.api.types.is_bool(value) else None
        return number if number is not None and isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _coordinate_pair(lat, lon):
    lat, lon = _number(lat), _number(lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (lat, lon) if (lat, lon) != (0, 0) else None


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Return a straight-line great-circle distance; inputs are decimal degrees."""
    origin = _coordinate_pair(lat1, lon1)
    destination = _coordinate_pair(lat2, lon2)
    if origin is None or destination is None:
        raise ValueError("Invalid coordinates or a zero-coordinate placeholder.")
    return _haversine_points(origin, destination)


def _haversine_points(origin, destination):
    lat1, lon1, lat2, lon2 = map(radians, (*origin, *destination))
    chord = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(max(0.0, min(1.0, chord))))


def _site_coordinates(entity: dict):
    quality = _text(entity.get("location_quality")).casefold()
    unsuitable = (
        "missing", "invalid", "conflict", "ambiguous", "placeholder",
        "centroid", "approximate", "unresolved",
    )
    if not quality or any(term in quality for term in unsuitable):
        return None
    return _coordinate_pair(entity.get("lat"), entity.get("lon"))


def _url(value) -> bool:
    try:
        parsed = urlparse(_text(value))
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def _timestamp(value, *, require_timezone=False):
    if value is None or not _text(value):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(stamp) or (stamp.tzinfo is None and require_timezone):
        return None
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _require(frame, columns, label):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(sorted(missing))}.")


def _checkpoint_value(value):
    if isinstance(value, dict):
        return {key: _checkpoint_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_checkpoint_value(item) for item in value]
    if pd.api.types.is_scalar(value):
        if pd.isna(value):
            return None
        if isinstance(value, date):
            return value.isoformat()
        if pd.api.types.is_bool(value):
            return bool(value)
        if isinstance(value, Integral):
            return int(value)
        if isinstance(value, Real):
            return float(value) if isfinite(value) else None
    return value


def _quality_flag(value):
    return bool(value) if pd.api.types.is_bool(value) else None


def _patient_fields(row):
    value = _number(row.get("patient_experience_value"))
    key = row.get("patient_comparison_key")
    return {
        "patient_experience_value": value if value is not None and 0 <= value <= 1 else None,
        "patient_comparison_key": (key.strip() or None) if isinstance(key, str) else None,
        "patient_experience_status": _text(row.get("patient_experience_status")) or None,
    }


def _record_groups(frame, key):
    rows = frame.to_dict("records") if isinstance(frame, pd.DataFrame) else frame
    groups = {}
    for row in rows:
        groups.setdefault(_text(row[key]), []).append(row)
    return groups


def _same_value(row):
    raw = row["value"]
    numeric = _number(raw)
    field = _text(row["field"])
    value = numeric if field in ("capacity", "numberDoctors") or field.startswith("service:") else _text(raw)
    if value is None:
        value = _text(raw)
    return value, _text(row.get("unit")).casefold()


def _select_field(rows, mode):
    populated = [r for r in rows if _text(r["value"]) or _text(r["method"]) == "conflicting"]
    observed = [r for r in populated if _text(r["method"]) in OBSERVED_METHODS | {"conflicting"}]
    candidates = observed or [r for r in populated if _text(r["method"]) == mode and mode != "confirmed"]
    if not candidates:
        return None
    keys = ("value", "method", "unit", "lower", "upper", "provenance", "detail")
    if any(_text(r["method"]) == "conflicting" for r in candidates) or len({_same_value(r) for r in candidates}) > 1:
        return {
            "value": None, "method": "conflicting",
            "detail": "Incompatible values or units. Inspect each contributing update.",
            "alternatives": [{key: r.get(key) for key in keys} for r in candidates],
        }
    chosen = min(candidates, key=lambda r: (_text(r["method"]) != "reported", _text(r.get("provenance"))))
    return {key: chosen.get(key) for key in keys}


def _mode_fields(rows, mode):
    return {
        field: selected
        for field, records in _record_groups(rows, "field").items()
        if not field.startswith("service:") and (selected := _select_field(records, mode))
    }


def _field_payload(rows, mode):
    fields = _checkpoint_value(_mode_fields(rows, mode))
    payload = {"field_updates_json": json.dumps(fields, default=str, ensure_ascii=False)}
    for field in ("capacity", "numberDoctors"):
        value = fields.get(field, {})
        payload[field] = value.get("value")
        payload[field + "_method"] = value.get("method", "unknown")
        for key in ("unit", "lower", "upper", "provenance", "detail"):
            payload[field + "_" + key] = value.get(key)
    return payload


def _direct_claim(row):
    return (
        _text(row.get("citation_quality", "direct")) == "direct"
        and bool(_text(row["excerpt"]))
        and _url(row["source_url"])
    )


def _claim_order(row):
    return (
        EVIDENCE_TIERS.get(_text(row["validity"]), len(EVIDENCE_TIERS)),
        _text(row["source"]), _text(row["record_id"]), _text(row["excerpt"]),
    )


def _claim_dates(claims, date):
    """Reapply the query date to parsed validity ranges without unflagging suspensions."""
    if not {"validity_start", "validity_end"} <= set(claims.columns):
        return claims
    rows = []
    for row in claims.to_dict("records"):
        start = _timestamp(row["validity_start"])
        end = _timestamp(row["validity_end"])
        if _text(row["validity"]) != "inactive" and start is not None and end is not None and start <= end:
            row["validity"] = "future" if date < start.date() else "expired" if date > end.date() else "active"
        rows.append(row)
    return pd.DataFrame(rows, columns=claims.columns)


def _claim_assessment(rows):
    supports = [
        r for r in rows
        if _text(r["stance"]) == "supports" and _text(r["validity"]) in EVIDENCE_TIERS and _direct_claim(r)
    ]
    contradictions = [
        r for r in rows
        if _text(r["stance"]) == "contradicts" and _text(r["validity"]) in EVIDENCE_TIERS and _text(r["excerpt"])
    ]
    best = min(supports, key=_claim_order) if supports else None
    if contradictions:
        reason = "Conflicting service records" if best else "Source says the service is unavailable or offsite"
        return best if best else min(contradictions, key=_claim_order), reason, True
    if best:
        return best, "", False
    if not rows:
        return None, "No documented service evidence", False
    best_review = min(rows, key=_claim_order)
    reason = "Only expired, inactive, or future claims" if not any(
        _text(r["validity"]) in EVIDENCE_TIERS for r in rows
    ) else "No direct, traceable evidence that this branch offers the service"
    return best_review, reason, False


def _check_as_of(as_of):
    stamp = pd.Timestamp.now(tz="UTC") if as_of is None else _timestamp(as_of)
    if stamp is None:
        raise ValueError("as_of must be a valid date or timestamp.")
    return stamp


def _prepared_model_leads(updates, service_field):
    leads = {}
    for mode in ("ml", "regression"):
        rows = [r for r in updates if r["field"] == service_field and r["method"] == mode]
        selected = _select_field(rows, mode)
        score = _number(selected["value"]) if selected else None
        if score is not None and 0 <= score <= 1:
            leads[mode] = _checkpoint_value(dict(selected, value=score))
    return leads


def prepare_service_candidates(
    entities: pd.DataFrame,
    claims: pd.DataFrame,
    service: str,
    *,
    updates: pd.DataFrame | None = None,
    as_of=None,
) -> pd.DataFrame:
    """Prepare origin-independent, auditable candidates for a single service.

    Use branch routing flags from the location audit. The evidence date is saved
    in the checkpoint. Rebuild after changing evidence, modeled fields, or the
    date used to assess source validity.
    """
    service = _text(service)
    if not service:
        raise ValueError("Provide a canonical service.")
    as_of = _check_as_of(as_of)
    _require(entities, (*IDENTITY_COLUMNS, *LOCATION_AUDIT_COLUMNS[:2]), "entities")
    _require(claims, CLAIM_COLUMNS, "claims")
    ids = entities["facility_id"].map(_text)
    if ids.eq("").any() or ids.duplicated().any():
        raise ValueError("entities must have a unique nonempty facility_id for each branch.")
    entities = entities.assign(facility_id=ids)
    claims = _claim_dates(claims.loc[claims["service"].eq(service)], as_of.date())
    if updates is None or updates.empty:
        updates = pd.DataFrame(columns=UPDATE_COLUMNS)
    else:
        _require(updates, UPDATE_COLUMNS, "updates")
    service_field = "service:" + service
    update_fields = updates["field"].map(_text)
    updates = updates.loc[~update_fields.str.startswith("service:") | update_fields.eq(service_field)]
    methods = set(updates.loc[updates["value"].map(_text).ne(""), "method"])
    model_methods = [mode for mode in ("ml", "regression") if mode in methods]
    claim_groups = _record_groups(claims, "facility_id")
    update_groups = _record_groups(updates, "facility_id")
    candidates = []
    for entity in entities.to_dict("records"):
        ident = entity["facility_id"]
        records = claim_groups.get(ident, [])
        field_rows = update_groups.get(ident, [])
        if not records and not any(_text(r["field"]) == service_field for r in field_rows):
            continue
        selected, reason, contradiction = _claim_assessment(records)
        flag = entity["location_routing_eligible"]
        hold_reason = _text(entity["location_hold_reason"])
        coords = (
            _coordinate_pair(entity["lat"], entity["lon"])
            if pd.api.types.is_bool(flag) and flag and not hold_reason else None
        )
        lat, lon = coords if coords is not None else (None, None)
        candidates.append({
            "service": service, **{key: _checkpoint_value(entity.get(key)) for key in IDENTITY_COLUMNS},
            **{key: _quality_flag(entity.get(key)) for key in QUALITY_EVIDENCE_COLUMNS},
            **_patient_fields(entity),
            "location_routing_eligible": coords is not None,
            "location_hold_reason": "" if coords is not None else hold_reason or _DEFAULT_LOCATION_HOLD,
            "location_source_urls": _checkpoint_value(entity.get("location_source_urls")) or [],
            "location_review_flags": _checkpoint_value(entity.get("location_review_flags")) or [],
            "site_lat": lat, "site_lon": lon,
            "evidence_as_of": as_of.date().isoformat(),
            "evidence_tier": EVIDENCE_TIERS.get(_text(selected["validity"])) if selected else None,
            "evidence_hold_reason": reason, "service_contradiction": contradiction,
            "claim": _checkpoint_value(selected or {}),
            "claims_json": json.dumps(_checkpoint_value(records), default=str, ensure_ascii=False),
            "mode_fields": {mode: _field_payload(field_rows, mode) for mode in MODES},
            "model_leads": _prepared_model_leads(field_rows, service_field) if not contradiction and reason else {},
            "snapshot_entity_count": len(entities), "snapshot_model_methods": model_methods,
        })
    return pd.DataFrame(candidates, columns=PREPARED_COLUMNS, dtype=object)


def _candidates(prepared, origin, radius):
    candidates = []
    for candidate in prepared.to_dict("records"):
        distance = None
        if candidate["location_routing_eligible"]:
            distance = _haversine_points(origin, (candidate["site_lat"], candidate["site_lon"]))
        outside = distance is None or distance > radius
        geography = (
            candidate["location_hold_reason"]
            if distance is None else "Outside the requested radius" if outside else ""
        )
        audit = [reason for reason in (candidate["evidence_hold_reason"], geography) if reason]
        candidates.append({
            **candidate, "distance_km": distance, "eligible": not audit,
            "reason": "; ".join(audit) or "Direct, traceable service evidence for this branch",
            "reviewable": not outside and not candidate["service_contradiction"],
        })
    return candidates


def _unit_key(value):
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _unit_matches_service(service, unit):
    """Exclude total hospital beds as a proxy for outpatient service throughput."""
    service = _unit_key(service)
    outpatient = ("dialysis", "mri", "ct scan", "blood bank", "blood storage")
    bed_unit = re.search(r"\bbeds?\b", unit) is not None
    return bool(unit) and not (bed_unit and any(term in service for term in outpatient))


def _routes(travel_times):
    routes = {}
    if travel_times is None:
        return routes
    for ident, rows in _record_groups(travel_times, "facility_id").items():
        candidates = set()
        for row in rows:
            minutes, kilometers = map(_number, (row.get("travel_minutes"), row.get("route_distance_km")))
            minutes = minutes if minutes is not None and minutes >= 0 else None
            kilometers = kilometers if kilometers is not None and kilometers >= 0 else None
            source = _text(row["travel_source"])
            if source and (minutes is not None or kilometers is not None):
                url = _text(row.get("travel_source_url"))
                candidates.add((
                    minutes, kilometers, _text(row.get("travel_mode")).casefold(), source, url if _url(url) else None,
                ))
        if ident and len(candidates) == 1:
            minutes, kilometers, mode, source, url = next(iter(candidates))
            routes[ident] = {
                "travel_minutes": minutes, "route_distance_km": kilometers, "travel_mode": mode or None,
                "travel_source": source, "travel_source_url": url,
            }
    return routes


def _route_context(route):
    return route.get("travel_mode"), route.get("travel_source")


def _travel_metric(sites, routes, route_sort):
    requested = ROUTE_SORT_METRICS[route_sort]
    field = ROUTE_VALUE_FIELDS[requested]
    site_routes = [routes.get(r["facility_id"], {}) for r in sites]
    if site_routes and all(route.get(field) is not None for route in site_routes):
        if len({_route_context(route) for route in site_routes}) == 1:
            return requested
    return DISTANCE_METRIC


def _ranking_travel_reason(metric, route_sort, eligible, supplied):
    if not eligible:
        return "No eligible facilities to rank."
    if metric != DISTANCE_METRIC:
        return f"Comparable {TRAVEL_LABELS[metric]} available for every eligible facility."
    if supplied:
        requested = TRAVEL_LABELS[ROUTE_SORT_METRICS[route_sort]]
        return f"Comparable {requested} unavailable for the full set. Showing straight-line distance ranking."
    return "No route estimates supplied. Showing straight-line distance ranking."


def _travel_value(site, routes, metric):
    if metric == DISTANCE_METRIC:
        return site["distance_km"]
    return routes[site["facility_id"]][ROUTE_VALUE_FIELDS[metric]]


def _quality_policy_values(policy):
    if policy is None:
        return None
    if not isinstance(policy, dict):
        raise ValueError("Provide quality_policy as a dictionary.")
    share, kilometers, minutes = (
        _number(policy.get(key)) for key in ("corroboration_share", "max_credit_km", "max_credit_minutes")
    )
    if share is None or not 0 <= share <= 1 or kilometers is None or minutes is None or min(kilometers, minutes) < 0:
        raise ValueError("Provide a corroboration share from 0 to 1 and nonnegative kilometer and minute credits.")
    experience_km, experience_minutes = (
        _number(policy.get(key, 0)) for key in ("max_experience_credit_km", "max_experience_credit_minutes")
    )
    if (
        experience_km is None or experience_minutes is None or min(experience_km, experience_minutes) < 0
        or not isfinite(kilometers + experience_km) or not isfinite(minutes + experience_minutes)
    ):
        raise ValueError("Provide nonnegative patient-experience caps and finite total ranking credits.")
    return share, kilometers, minutes, experience_km, experience_minutes


def _patient_tier_ranking(rows, experience_cap, emergency):
    candidates = {}
    for row in rows:
        fields = _patient_fields(row)
        if (
            fields["patient_experience_status"] == "eligible" and fields["patient_experience_value"] is not None
            and fields["patient_comparison_key"]
        ):
            candidates[row["facility_id"]] = fields
    coverage = f"{len(candidates)} of {len(rows)} eligible facilities in this evidence tier have usable ratings."
    if emergency:
        status, cause = "emergency_travel_only", "Emergency and trauma searches prioritize travel."
    elif not experience_cap:
        status, cause = "off", "Patient-experience credit is off."
    elif len(rows) < 2:
        status, cause = "held_insufficient_candidates", "Rating-based ordering requires at least two eligible facilities."
    elif len(candidates) != len(rows):
        status, cause = "held_incomplete_coverage", "Rating-based ordering requires coverage of every eligible facility."
    elif len({fields["patient_comparison_key"] for fields in candidates.values()}) != 1:
        status, cause = "held_incomparable", "Rating-based ordering requires a shared source, metric, scale and time window."
    else:
        status, cause = "applied", "Every eligible facility has ratings from the same source, metric, scale and time window."
    return candidates, {
        "patient_ranking_status": status, "patient_ranking_reason": f"{coverage} {cause}",
        "patient_tier_eligible": len(candidates), "patient_tier_candidates": len(rows),
    }


def _quality_order(travel_only, distances, metric, policy, emergency):
    share, kilometers, minutes, experience_km, experience_minutes = policy or (0.0, 0.0, 0.0, 0.0, 0.0)
    unit = "minutes" if metric == ROUTE_METRIC else "km"
    cap = 0.0 if emergency else minutes if unit == "minutes" else kilometers
    experience_cap = 0.0 if emergency else experience_minutes if unit == "minutes" else experience_km
    details = {}
    by_tier = {}
    for row in travel_only:
        by_tier.setdefault(row["evidence_tier"], []).append(row)
    tier_reviews = {
        tier: _patient_tier_ranking(rows, experience_cap, emergency)
        for tier, rows in by_tier.items()
    }
    for row in travel_only:
        ident = row["facility_id"]
        patients, review = tier_reviews[row["evidence_tier"]]
        corroboration = cap * share if _quality_flag(row.get("quality_corroborated")) else 0.0
        assessment = cap * (1 - share) if _quality_flag(row.get("quality_assessed")) else 0.0
        credit = min(cap, corroboration + assessment)
        experience_credit = (
            experience_cap * patients[ident]["patient_experience_value"]
            if review["patient_ranking_status"] == "applied" else 0.0
        )
        total_credit = credit + experience_credit
        details[ident] = {
            "corroboration_credit": corroboration, "assessment_credit": assessment,
            "quality_credit": credit, "quality_credit_unit": unit,
            "patient_experience_credit": experience_credit, "total_quality_experience_credit": total_credit,
            "balanced_travel_score": distances[ident] - total_credit,
            **review,
        }
    reviews = [review for _, review in tier_reviews.values()]
    applied = sum(review["patient_ranking_status"] == "applied" for review in reviews)
    held = sum(review["patient_ranking_status"].startswith("held_") for review in reviews)
    documentation_applied = any(row["quality_credit"] > 0 for row in details.values())
    if emergency:
        rule = "emergency_travel_only"
        reason = "Emergency and trauma searches prioritize travel within each evidence tier."
    elif documentation_applied or applied:
        terms = []
        if documentation_applied:
            terms.append(f"up to {cap:g} {unit} for documented corroboration and assessment")
        if applied:
            terms.append(f"up to {experience_cap:g} {unit} for comparable patient experience")
        rule = (
            "documented_quality_and_experience_and_travel" if documentation_applied and applied
            else "documented_quality_and_travel" if documentation_applied else "patient_experience_and_travel"
        )
        reason = f"Within an evidence tier, subtract {' and '.join(terms)} from travel. Equal scores favor shorter travel."
    else:
        rule = "travel_only"
        reason = "Travel determines the order within each evidence tier."
    ordered = sorted(
        travel_only, key=lambda row: (row["evidence_tier"], details[row["facility_id"]]["balanced_travel_score"]),
    ) if documentation_applied or applied else travel_only
    patient_status = (
        "emergency_travel_only" if emergency else "off" if not experience_cap
        else "partially_applied" if applied and held else "applied" if applied else "held" if held else "no_candidates"
    )
    patient_reason = " ".join(review["patient_ranking_reason"] for review in reviews) or "No eligible evidence tiers."
    summary = {
        "ranking_policy": rule, "ranking_policy_reason": reason, "quality_max_credit": cap,
        "patient_experience_max_credit": experience_cap, "quality_total_max_credit": cap + experience_cap,
        "quality_credit_unit": unit,
        "quality_corroboration_share": share if policy else None,
        "quality_assessment_share": 1 - share if policy else None,
        "patient_ranking_status": patient_status, "patient_ranking_reason": patient_reason,
        "patient_applied_tiers": applied, "patient_held_tiers": held,
        "patient_eligible_candidates": sum(review["patient_tier_eligible"] for review in reviews),
        "patient_comparison_candidates": len(travel_only),
    }
    return ordered, details, summary


def _route_difference(route, reference, metric):
    field = ROUTE_VALUE_FIELDS[metric]
    value, baseline = route.get(field), reference.get(field)
    if value is None or baseline is None or _route_context(route) != _route_context(reference):
        return None
    return value - baseline


def _comparable_sites(baseline):
    reference = baseline[0]
    return [
        r for r in baseline
        if r["evidence_tier"] == reference["evidence_tier"]
        and _text(r["claim"].get("scope")) == _text(reference["claim"].get("scope"))
    ]


def _within_detour(site, reference, routes, km_cap, minute_cap):
    route, baseline = (routes.get(row["facility_id"], {}) for row in (site, reference))
    kilometers = _route_difference(route, baseline, ROUTE_DISTANCE_METRIC)
    if kilometers is None:
        kilometers = site["distance_km"] - reference["distance_km"]
    minutes = _route_difference(route, baseline, ROUTE_METRIC)
    return kilometers <= km_cap and (minutes is None or minutes <= minute_cap)


def _pressure_index(pressure, service):
    by_facility = {}
    for row in pressure.to_dict("records"):
        if _text(row["service"]) != service:
            continue
        ident, unit = _text(row["facility_id"]), _unit_key(row["unit"])
        period, basis = _text(row["period"]), _text(row["basis"])
        demand, capacity = _number(row["demand"]), _number(row["capacity"])
        if (
            not ident or not period or not basis or not _url(row["source_url"])
            or demand is None or demand < 0 or capacity is None or capacity <= 0
            or not _unit_matches_service(service, unit)
        ):
            continue
        group = (unit, period.casefold(), basis.casefold())
        by_facility.setdefault(ident, {}).setdefault(group, []).append({
            "pressure_demand": demand,
            "pressure_capacity": capacity,
            "pressure_unit": _text(row["unit"]),
            "pressure_period": period,
            "pressure_basis": basis,
            "pressure_source_url": _text(row["source_url"]),
        })
    return {
        ident: {
            group: min(rows, key=lambda r: r["pressure_source_url"])
            for group, rows in groups.items()
            if len({(r["pressure_demand"], r["pressure_capacity"]) for r in rows}) == 1
        }
        for ident, groups in by_facility.items()
    }


def _score_pressure(data, extra, limit, metric, travel_weight):
    demand, capacity = data["pressure_demand"], data["pressure_capacity"]
    ratio = demand / capacity
    pressure_penalty = ratio / (1 + ratio) if isfinite(ratio) else 1.0
    # A cohort can use straight-line scoring while a pair has a valid road
    # detour within the limit. Keep the fallback penalty on the same scale.
    travel_penalty = min(1.0, max(0.0, extra) / limit) if limit else float(extra > 0)
    if demand < capacity:
        grade = "demand_below_stated_capacity"
    elif demand == capacity:
        grade = "demand_at_stated_capacity"
    else:
        grade = "demand_above_stated_capacity"
    return dict(
        data, pressure_ratio=ratio if isfinite(ratio) else None, pressure_grade=grade,
        travel_penalty=travel_penalty, pressure_penalty=pressure_penalty,
        travel_weight=travel_weight, pressure_weight=1 - travel_weight,
        weighted_score=travel_weight * travel_penalty + (1 - travel_weight) * pressure_penalty,
        weighted_travel_metric=metric, weighted_extra_travel=extra, weighted_detour_limit=limit,
    )


def _rank_weighted(baseline, pressure, service, routes, route_sort, opt_in, km_cap, minute_cap, weight):
    if not opt_in:
        return baseline, {}, "off"
    if not baseline:
        return baseline, {}, "no_supported_facility"
    if pressure is None or pressure.empty:
        return baseline, {}, "no_pressure_inputs"
    reference = baseline[0]
    index = _pressure_index(pressure, service)
    ref_groups = index.get(reference["facility_id"], {})
    by_group = {}
    for site in _comparable_sites(baseline)[1:]:
        for group in index.get(site["facility_id"], {}).keys() & ref_groups.keys():
            by_group.setdefault(group, [reference]).append(site)
    comparisons = {}
    for group, sites in by_group.items():
        metric = _travel_metric(sites, routes, route_sort)
        near = [r for r in sites if _within_detour(r, reference, routes, km_cap, minute_cap)]
        if len(near) > 1:
            comparisons[group] = (near, metric)
    if not by_group:
        return baseline, {}, "no_comparable_pressure_inputs"
    if not comparisons:
        return baseline, {}, "no_comparable_alternative_within_detour"
    if len(comparisons) > 1:
        return baseline, {}, "choose_comparable_pressure_group"
    group, (sites, metric) = next(iter(comparisons.items()))
    limit = minute_cap if metric == ROUTE_METRIC else km_cap
    scores = {
        r["facility_id"]: _score_pressure(
            index[r["facility_id"]][group],
            _travel_value(r, routes, metric) - _travel_value(reference, routes, metric),
            limit, metric, weight,
        )
        for r in sites
    }
    # Baseline is stable when the score ties.
    winner = min(sites, key=lambda r: scores[r["facility_id"]]["weighted_score"])
    if winner["facility_id"] == reference["facility_id"]:
        return baseline, scores, "baseline_has_best_weighted_score"
    ranked = [winner, *(r for r in baseline if r["facility_id"] != winner["facility_id"])]
    return ranked, scores, "weighted_pressure_rerank"


def _load_index(measures, service, as_of, max_age):
    by_facility = {}
    for row in measures.to_dict("records"):
        if _text(row["service"]) != service:
            continue
        if _text(row.get("basis")).casefold() not in ("", "measured", "observed"):
            continue
        ident, unit = _text(row["facility_id"]), _unit_key(row["unit"])
        sampled = _timestamp(row["as_of"], require_timezone=True)
        available = _number(row["available_units"])
        if (
            not ident or available is None or available < 0 or sampled is None
            or not _url(row["source_url"]) or not _unit_matches_service(service, unit)
        ):
            continue
        age = (as_of - sampled).total_seconds() / 3600
        if not 0 <= age <= max_age:
            continue
        slots = by_facility.setdefault(ident, {})
        previous = slots.get(unit)
        observation = dict(row, available_units=available, sampled=sampled, as_of=sampled.isoformat())
        if previous is None or sampled > previous["sampled"]:
            slots[unit] = observation
        elif sampled == previous["sampled"] and available != previous["available_units"]:
            # Keep the timestamp, but discard a contradictory observation at that time.
            previous["conflict"] = True
    return {
        ident: {unit: obs for unit, obs in entries.items() if not obs.get("conflict")}
        for ident, entries in by_facility.items()
    }


def _rank_load(baseline, measures, service, as_of, opt_in, routes, route_sort, km_cap, minute_cap, max_age, max_gap, requested_unit):
    if not opt_in:
        return baseline, {}, "off"
    if not baseline:
        return baseline, {}, "no_supported_facility"
    if measures is None or measures.empty:
        return baseline, {}, "no_measured_load"
    reference = baseline[0]
    comparable_sites = _comparable_sites(baseline)
    nearby = [
        r for r in comparable_sites
        if _within_detour(r, reference, routes, km_cap, minute_cap)
    ]
    metric = _travel_metric(nearby, routes, route_sort)
    fresh = _load_index(measures, service, as_of, max_age)
    ref_measures = fresh.get(reference["facility_id"], {})
    alternatives = {
        r["facility_id"]: {
            unit: obs
            for unit, obs in fresh.get(r["facility_id"], {}).items()
            if unit in ref_measures
            and abs((obs["sampled"] - ref_measures[unit]["sampled"]).total_seconds()) <= max_gap * 3600
        }
        for r in nearby[1:]
    }
    comparable = {unit for site in alternatives.values() for unit in site}
    if requested_unit:
        comparable &= {requested_unit}
    if not comparable:
        return baseline, {}, "no_fresh_comparable_measurements"
    if len(comparable) > 1:
        return baseline, {}, "choose_comparable_load_unit"
    unit = next(iter(comparable))
    local = {ident: site[unit] for ident, site in alternatives.items() if unit in site}
    local[reference["facility_id"]] = ref_measures[unit]
    best = min(
        (r for r in nearby if r["facility_id"] in local),
        key=lambda r: (-local[r["facility_id"]]["available_units"], _travel_value(r, routes, metric), r["facility_id"]),
    )
    if best["facility_id"] == reference["facility_id"]:
        return baseline, local, "baseline_has_most_measured_availability"
    return [best, *(r for r in baseline if r["facility_id"] != best["facility_id"])], local, "measured_availability_rerank"


def _as_result(candidate, mode, service, decision, rank, baseline_rank, selected, ranking, lead=None):
    ident = candidate["facility_id"]
    ref = ranking["reference"]
    refs = ranking["routes"].get(ref["facility_id"], {}) if ref else {}
    route = ranking["routes"].get(ident, {})
    load = ranking["measured"].get(ident, {})
    pressure = ranking["weighted"].get(ident, {})
    quality = ranking["quality"].get(ident, {})
    rank_policy = ranking["quality_policy"]
    chosen = candidate["claim"]
    record = {key: candidate[key] for key in (*IDENTITY_COLUMNS, *LOCATION_AUDIT_COLUMNS)}
    record.update({
        key: chosen.get(key)
        for key in ("excerpt", "source_url", "source", "scope", "validity", "validity_text", "citation_quality", "record_id")
    })
    reason = candidate["reason"]
    if decision == "eligible":
        travel_label = TRAVEL_LABELS[ranking["travel_metric"]]
        credits = []
        if rank_policy["ranking_policy"] in ("documented_quality_and_travel", "documented_quality_and_experience_and_travel"):
            credits.append("documented quality credit")
        if quality.get("patient_ranking_status") == "applied":
            credits.append("comparable patient-experience credit")
        ordering = f"{travel_label} with {' and '.join(credits)}" if credits else travel_label
        reason = f"Sourced service, then validity and {ordering}"
        if rank == 1 and baseline_rank != 1 and ranking["load_status"] == "measured_availability_rerank":
            reason = "More measured service availability within your detour limits"
        if rank == 1 and baseline_rank != 1 and ranking["load_status"] == "weighted_pressure_rerank":
            reason = "Lowest weighted travel and demand-to-capacity score within your detour limit"
    elif decision == "lead":
        reason = "Model-inferred lead. Verify the service with the facility."
    record.update(
        mode=mode, service=service, kind="recommendation" if decision == "eligible" else decision,
        decision=decision, selected=selected, rank=rank, baseline_rank=baseline_rank,
        travel_only_rank=ranking["travel_positions"].get(ident), ranking_policy=rank_policy["ranking_policy"],
        ranking_policy_reason=rank_policy["ranking_policy_reason"],
        distance_km=candidate["distance_km"], distance_metric=DISTANCE_METRIC,
        evidence_tier=candidate["evidence_tier"], evidence_as_of=candidate["evidence_as_of"], reason=reason,
        review_reason=candidate["reason"], claims_json=candidate["claims_json"],
        model_lead_score=_number(lead["value"]) if lead else None,
        model_lead_provenance=lead.get("provenance") if lead else None,
        model_lead_detail=lead.get("detail") if lead else None,
        load_status=ranking["load_status"], weighted_status=ranking["weighted_status"],
        load_available_units=load.get("available_units"), load_unit=load.get("unit"),
        load_as_of=load.get("as_of"), load_source_url=load.get("source_url"),
        extra_km_vs_baseline=(
            candidate["distance_km"] - ref["distance_km"]
            if ref is not None and candidate["distance_km"] is not None else None
        ),
        route_sort=ranking["route_sort"], ranking_travel_metric=ranking["travel_metric"],
        ranking_travel_reason=ranking["travel_reason"],
        extra_route_km_vs_baseline=_route_difference(route, refs, ROUTE_DISTANCE_METRIC),
        extra_minutes_vs_baseline=_route_difference(route, refs, ROUTE_METRIC),
        **route,
        **{key: _quality_flag(candidate.get(key)) for key in QUALITY_EVIDENCE_COLUMNS},
        **_patient_fields(candidate),
        **quality,
        **pressure,
    )
    record.update(candidate["mode_fields"][mode])
    return record


def _clear_emergency_service(key):
    tokens = set(key.casefold().replace("_", " ").replace("-", " ").split())
    return bool(tokens & {"emergency", "trauma"})


def compare_modes(
    entities: pd.DataFrame,
    claims: pd.DataFrame,
    lat: float,
    lon: float,
    service: str,
    *,
    updates: pd.DataFrame | None = None,
    as_of=None,
    **query_options,
) -> dict[str, pd.DataFrame]:
    """Prepare then compare candidates for callers working with raw frames."""
    if "location_routing_eligible" not in entities.columns:
        eligible = [_site_coordinates(entity) is not None for entity in entities.to_dict("records")]
        entities = entities.assign(location_routing_eligible=eligible)
    if "location_hold_reason" not in entities.columns:
        entities = entities.assign(location_hold_reason="")
    as_of = _check_as_of(as_of)
    prepared = prepare_service_candidates(entities, claims, service, updates=updates, as_of=as_of)
    result = compare_prepared_modes(prepared, lat, lon, service, as_of=as_of, **query_options)
    result["summary"]["n_entities"] = len(entities)
    return result


def compare_prepared_modes(
    prepared: pd.DataFrame,
    lat: float,
    lon: float,
    service: str,
    *,
    measured_load: pd.DataFrame | None = None,
    pressure_inputs: pd.DataFrame | None = None,
    travel_times: pd.DataFrame | None = None,
    route_sort: str = "time",
    quality_policy: dict | None = None,
    as_of=None,
    max_radius_km: float = 50,
    top_k: int = 5,
    spread_demand: bool = False,
    max_extra_km: float = 10,
    max_extra_minutes: float = 15,
    travel_weight: float = 0.5,
    max_load_age_hours: float = 24,
    max_load_gap_hours: float = 2,
    lead_threshold: float = 0.5,
    load_unit: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Rank a prepared checkpoint and return recommendations, trace, and summary.

    Apply origin-specific travel and the requested lead threshold here. The
    optional query as_of evaluates measured load age. The checkpoint keeps its
    original date for service evidence validity. Use the requested route
    measure when comparable across eligible sites inside the straight-line
    radius. Documentation and comparable patient-experience credits can change
    the order within a non-emergency evidence tier. Balancing enforces both
    detour limits when the pair has routes, measured from the top recommendation
    before balancing.
    """
    origin = _coordinate_pair(lat, lon)
    if origin is None:
        raise ValueError("Provide valid origin latitude and longitude.")
    service = _text(service)
    if not service:
        raise ValueError("Provide a canonical service.")
    if route_sort not in ("time", "distance"):
        raise ValueError("route_sort must be time or distance.")
    if not isinstance(top_k, Integral) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be a positive integer.")
    radius, extra, max_age, max_gap, threshold = map(
        _number, (max_radius_km, max_extra_km, max_load_age_hours, max_load_gap_hours, lead_threshold),
    )
    if radius is None or radius <= 0 or extra is None or extra < 0:
        raise ValueError("Use a positive radius and a nonnegative extra-distance limit.")
    if max_age is None or max_age <= 0 or max_gap is None or max_gap < 0:
        raise ValueError("Use a positive load age and a nonnegative observation time gap.")
    minutes_cap, weight = map(_number, (max_extra_minutes, travel_weight))
    if minutes_cap is None or minutes_cap < 0 or weight is None or not 0 <= weight <= 1:
        raise ValueError("Provide a nonnegative minutes detour limit and travel weight from 0 to 1.")
    if threshold is None or not 0 <= threshold <= 1:
        raise ValueError("lead_threshold must be between 0 and 1.")
    policy = _quality_policy_values(quality_policy)
    as_of = _check_as_of(as_of)
    _require(prepared, _REQUIRED_PREPARED_COLUMNS, "prepared")
    prepared = prepared.loc[prepared["service"].eq(service)]
    if prepared["facility_id"].duplicated().any():
        raise ValueError("Use one prepared row per facility and service.")
    metadata = prepared.iloc[0] if not prepared.empty else {}
    model_methods = metadata.get("snapshot_model_methods", [])
    n_entities = metadata.get("snapshot_entity_count", 0)
    evidence_as_of = metadata.get("evidence_as_of")
    if measured_load is not None and not measured_load.empty:
        _require(measured_load, MEASURED_LOAD_COLUMNS, "measured_load")
    if pressure_inputs is not None and not pressure_inputs.empty:
        _require(pressure_inputs, PRESSURE_COLUMNS, "pressure_inputs")
    routes_supplied = travel_times is not None and not travel_times.empty
    if routes_supplied:
        _require(travel_times, TRAVEL_COLUMNS, "travel_times")
        if not set(ROUTE_VALUE_FIELDS.values()) & set(travel_times.columns):
            raise ValueError("travel_times must provide travel_minutes or route_distance_km.")
    routes = _routes(travel_times)
    candidates = _candidates(prepared, origin, radius)
    eligible = [r for r in candidates if r["eligible"]]
    baseline_metric = _travel_metric(eligible, routes, route_sort)
    travel_reason = _ranking_travel_reason(baseline_metric, route_sort, eligible, routes_supplied)
    distances = {r["facility_id"]: _travel_value(r, routes, baseline_metric) for r in eligible}
    travel_only = sorted(
        eligible,
        key=lambda r: (r["evidence_tier"], distances[r["facility_id"]], r["distance_km"], r["facility_id"]),
    )
    emergency = _clear_emergency_service(service)
    baseline, quality, rank_policy = _quality_order(travel_only, distances, baseline_metric, policy, emergency)
    if spread_demand and emergency:
        ranked, load, weighted = baseline, {}, {}
        load_status = weighted_status = "non_emergency_only"
    else:
        ranked, weighted, weighted_status = _rank_weighted(
            baseline, pressure_inputs, service, routes, route_sort, spread_demand, extra, minutes_cap, weight,
        )
        if weighted:
            load, load_status = {}, weighted_status
        else:
            ranked, load, load_status = _rank_load(
                baseline, measured_load, service, as_of, spread_demand, routes, route_sort,
                extra, minutes_cap, max_age, max_gap, _unit_key(load_unit),
            )
    ranking = {
        "reference": baseline[0] if baseline else None, "routes": routes,
        "travel_metric": baseline_metric, "travel_reason": travel_reason, "route_sort": route_sort,
        "travel_positions": {row["facility_id"]: index for index, row in enumerate(travel_only, start=1)},
        "quality": quality, "quality_policy": rank_policy,
        "measured": load, "weighted": weighted,
        "load_status": load_status, "weighted_status": weighted_status,
    }
    base_positions = {r["facility_id"]: i for i, r in enumerate(baseline, start=1)}
    positions = {r["facility_id"]: i for i, r in enumerate(ranked, start=1)}
    trace, summaries = [], []
    for mode in MODES:
        leads = {
            r["facility_id"]: lead for r in candidates if not r["eligible"]
            if r["reviewable"] and (lead := r["model_leads"].get(mode)) is not None
            and lead["value"] >= threshold
        }
        lead_order = sorted(
            (r for r in candidates if r["facility_id"] in leads),
            key=lambda r: (-float(leads[r["facility_id"]]["value"]), r["distance_km"], r["facility_id"]),
        )
        lead_positions = {r["facility_id"]: i for i, r in enumerate(lead_order, start=1)}
        for candidate in candidates:
            ident = candidate["facility_id"]
            decision = "eligible" if ident in positions else "lead" if ident in leads else "excluded"
            rank = positions.get(ident, lead_positions.get(ident))
            selected = rank is not None and rank <= top_k
            trace.append(_as_result(
                candidate, mode, service, decision, rank, base_positions.get(ident),
                selected, ranking, leads.get(ident),
            ))
        summaries.append(dict(
            mode=mode, service=service,
            mode_status=(
                "documented_fields_only" if mode == "confirmed"
                else "model_outputs_available" if mode in model_methods else "no_model_outputs_supplied"
            ),
            n_entities=n_entities, n_service_candidates=len(candidates),
            n_eligible=len(baseline), n_recommendations=min(len(baseline), top_k),
            n_leads=min(len(leads), top_k), n_excluded=len(candidates) - len(baseline) - len(leads),
            load_status=load_status, spread_demand=spread_demand, max_extra_km=extra,
            max_radius_km=radius, distance_metric=DISTANCE_METRIC, as_of=as_of.isoformat(),
            evidence_as_of=evidence_as_of,
            route_sort=route_sort, ranking_travel_metric=baseline_metric, ranking_travel_reason=travel_reason,
            weighted_status=weighted_status,
            weighted_travel_metric=next(iter(weighted.values()))["weighted_travel_metric"] if weighted else None,
            max_extra_minutes=minutes_cap, travel_weight=weight, pressure_weight=1 - weight,
            **rank_policy,
        ))
    trace_frame = pd.DataFrame(trace, columns=RESULT_COLUMNS)
    rows = trace_frame.loc[trace_frame["selected"].astype(bool)].copy()
    if not rows.empty:
        mode_order = {mode: index for index, mode in enumerate(MODES)}
        rows["_mode"] = rows["mode"].map(mode_order)
        rows["_kind"] = rows["kind"].ne("recommendation")
        rows = rows.sort_values(["_mode", "_kind", "rank"]).drop(columns=["_mode", "_kind"])
    return {"rows": rows.reset_index(drop=True), "trace": trace_frame, "summary": pd.DataFrame(summaries)}


def simulate_spread(
    candidates: pd.DataFrame,
    assumptions: pd.DataFrame,
    service: str,
    *,
    requests: int = 100,
    max_extra_km: float = 10,
) -> pd.DataFrame:
    """Replay hypothetical suggestions under a cap with a common capacity unit.

    Pass eligible candidates from one query and one mode and the same service.
    Each assumption has a positive capacity, its unit, and optionally a basis
    such as hypothetical or modeled. Capacities weight relative distribution
    here. The scenario neither books actual slots nor caps real admissions.
    """
    _require(candidates, ("facility_id", "distance_km"), "candidates")
    _require(assumptions, ("facility_id", "service", "unit", "capacity"), "assumptions")
    if not isinstance(requests, Integral) or isinstance(requests, bool) or not 0 <= requests <= 100_000:
        raise ValueError("requests must be an integer from 0 to 100,000.")
    extra = _number(max_extra_km)
    if extra is None or extra < 0:
        raise ValueError("max_extra_km must be nonnegative.")
    service = _text(service)
    if not service:
        raise ValueError("Provide a canonical service.")
    selected = candidates.copy()
    if "decision" in selected.columns:
        selected = selected.loc[selected["decision"].eq("eligible")]
    elif "kind" in selected.columns:
        selected = selected.loc[selected["kind"].eq("recommendation")]
    if "service" in selected.columns:
        selected = selected.loc[selected["service"].eq(service)]
    selected = selected.assign(
        facility_id=selected["facility_id"].map(_text),
        distance_km=selected["distance_km"].map(_number),
    )
    selected = selected.loc[selected["facility_id"].ne("") & selected["distance_km"].ge(0)]
    if selected["facility_id"].duplicated().any():
        raise ValueError("Pass candidates for one query and one mode; facility IDs must be unique.")
    columns = [
        "facility_id", "name", "service", "distance_km", "extra_km", "assumed_service_capacity",
        "unit", "basis", "baseline_suggestions", "balanced_suggestions", "status",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    order = ["distance_km", "facility_id"]
    if "baseline_rank" in selected.columns:
        order.insert(0, "baseline_rank")
    selected = selected.sort_values(order)
    sites = selected.to_dict("records")
    nearest = sites[0]
    nearby = [
        r for r in sites
        if r["distance_km"] <= nearest["distance_km"] + extra
        and _text(r.get("evidence_tier")) == _text(nearest.get("evidence_tier"))
        and _text(r.get("scope")) == _text(nearest.get("scope"))
    ]
    rows = [
        row for row in assumptions.to_dict("records")
        if _text(row["service"]) == service and _unit_matches_service(service, _unit_key(row["unit"]))
        and (amount := _number(row["capacity"])) is not None and amount > 0
    ]
    groups = _record_groups(rows, "facility_id")
    capacity = {}
    for site in nearby:
        ident = site["facility_id"]
        options = groups.get(ident, [])
        distinct = {(_number(r["capacity"]), _unit_key(r["unit"])) for r in options}
        if len(distinct) == 1:
            amount, unit = next(iter(distinct))
            capacity[ident] = (amount, unit, _text(options[0].get("basis")) or "hypothetical")
    nearest_measure = capacity.get(nearest["facility_id"])
    unit = nearest_measure[1] if nearest_measure else None
    comparable = [r for r in nearby if r["facility_id"] in capacity and capacity[r["facility_id"]][1] == unit]
    balanced = {r["facility_id"]: 0 for r in sites}
    if len(comparable) < 2:
        balanced[nearest["facility_id"]] = requests
        status = "no_comparable_capacity_assumptions"
    else:
        status = "simulated_suggestions_only"
        for _ in range(requests):
            site = min(comparable, key=lambda r: (
                (balanced[r["facility_id"]] + 1) / capacity[r["facility_id"]][0],
                r["distance_km"], r["facility_id"],
            ))
            balanced[site["facility_id"]] += 1
    results = []
    for site in sites:
        ident = site["facility_id"]
        amount, site_unit, basis = capacity.get(ident, (None, None, None))
        results.append(dict(
            facility_id=ident, name=site.get("name"), service=service,
            distance_km=site["distance_km"], extra_km=site["distance_km"] - nearest["distance_km"],
            assumed_service_capacity=amount, unit=site_unit, basis=basis,
            baseline_suggestions=requests if ident == nearest["facility_id"] else 0,
            balanced_suggestions=balanced[ident], status=status,
        ))
    return pd.DataFrame(results, columns=columns)
