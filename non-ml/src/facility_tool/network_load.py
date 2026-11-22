"""Read modeled service pressure and replay bounded routine suggestions offline."""

from heapq import heapify, heappop, heappush
from math import isfinite
from numbers import Integral
import re

import pandas as pd

from .recommend import (
    DISTANCE_METRIC,
    EVIDENCE_TIERS,
    PRESSURE_COLUMNS,
    ROUTE_DISTANCE_METRIC,
    ROUTE_METRIC,
    _capacity_prediction_flag,
    _number,
    _pressure_index,
    _require,
    _route_difference,
    _routes,
    _score_pressure,
    _text,
    _travel_metric,
    _travel_value,
    _unit_key,
    _unit_matches_service,
    _url,
    _within_detour,
    routine_load_allowed,
)


MODEL_COLUMNS = (
    "facility_id", "service", "care_needed", "unit", "scope", "period", "caseload", "capacity",
    "caseload_predicted", "capacity_predicted", "caseload_method", "capacity_method", "source_url",
    "capacity_source_url", "service_evidence", "routing_eligible",
)
PRESSURE_PROVENANCE = (
    "caseload_predicted", "capacity_predicted", "caseload_method", "capacity_method", "capacity_source_url",
)
REFERENCE_FIELDS = ("reference_start", "reference_end", "capacity_reference_period")
NETWORK_PRESSURE_COLUMNS = (*PRESSURE_COLUMNS, *PRESSURE_PROVENANCE, *REFERENCE_FIELDS, "scope", "care_needed", "service_evidence")
REPLAY_COLUMNS = (
    "facility_id", "name", "service", "evidence_tier", "evidence_scope", "distance_km", "travel_metric",
    "travel_value", "extra_km", "extra_route_km", "extra_minutes", "baseline_suggestions", "balanced_suggestions",
    "comparable", "status", "demand", "capacity", "unit", "period", "basis", "scope", "source_url",
    *PRESSURE_PROVENANCE, *REFERENCE_FIELDS, "initial_pressure_ratio", "baseline_pressure_ratio", "balanced_pressure_ratio",
)
FLOW_UNITS = {
    "administrations", "admissions", "attendances", "births", "cases", "consultations", "deliveries",
    "encounters", "examinations", "operations", "procedures", "scans", "sessions", "surgeries", "visits",
}


def _monthly_flow(unit, period):
    words = set(re.findall(r"[a-z]+", unit.casefold()))
    date_month = re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period)
    period_words = set(re.findall(r"[a-z]+", period.casefold()))
    return bool(words & FLOW_UNITS and (date_month or period_words & {"month", "monthly"}))


def read_network_pressure(path, service, eligible_ids=None):
    """Read positive-throughput rows for one canonical service from CSV or CSV.gz.

    The release routing flag is a location check. Supply service-eligible IDs
    from the caller's independent evidence review when available. The result
    contains pressure inputs and cannot establish that the service is offered.
    """
    service = _text(service)
    if not service:
        raise ValueError("Provide a canonical service.")
    modeled = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    _require(modeled, MODEL_COLUMNS, "network load model")
    selected = modeled.loc[modeled["service"].str.strip().eq(service)]
    if eligible_ids is not None:
        selected = selected.loc[selected["facility_id"].str.strip().isin({_text(ident) for ident in eligible_ids})]
    records = []
    for raw in selected.to_dict("records"):
        row = {key: _text(raw[key]) for key in MODEL_COLUMNS}
        demand, capacity = _number(row["caseload"]), _number(row["capacity"])
        demand_flag = _capacity_prediction_flag(row["caseload_predicted"])
        capacity_flag = _capacity_prediction_flag(row["capacity_predicted"])
        if (
            _capacity_prediction_flag(row["routing_eligible"]) is not True
            or not all(row[key] for key in ("facility_id", "scope", "period", "caseload_method", "capacity_method"))
            or not _unit_matches_service(service, row["unit"]) or not _monthly_flow(row["unit"], row["period"])
            or not _url(row["source_url"]) or not _url(row["capacity_source_url"])
            or demand is None or demand < 0 or capacity is None or capacity <= 0
            or demand_flag is None or capacity_flag is None
        ):
            continue
        records.append({
            **row, "demand": demand, "capacity": capacity, "basis": row["scope"],
            "caseload_predicted": demand_flag, "capacity_predicted": capacity_flag,
            **{key: raw.get(key) for key in REFERENCE_FIELDS},
        })
    pressure = pd.DataFrame(records, columns=NETWORK_PRESSURE_COLUMNS).drop_duplicates()
    # Conflicting values or provenance for the same facility comparison cannot be selected safely.
    keys = pressure[["facility_id", "period", "basis", "unit"]].copy()
    keys["unit"] = keys["unit"].map(_unit_key)
    keys["period"] = keys["period"].str.casefold()
    keys["basis"] = keys["basis"].str.casefold()
    return pressure.loc[~keys.duplicated(keep=False)].reset_index(drop=True)


def _replay_candidates(candidates):
    _require(candidates, ("facility_id", "service", "distance_km", "evidence_tier", "scope"), "candidates")
    if "decision" in candidates:
        eligible = candidates["decision"].eq("eligible")
    elif "kind" in candidates:
        eligible = candidates["kind"].eq("recommendation")
    else:
        _require(candidates, ("eligible",), "hypothetical candidates")
        eligible = candidates["eligible"].map(_capacity_prediction_flag).eq(True)
    selected = candidates.loc[eligible]
    services = {_text(value) for value in selected["service"]}
    if selected.empty:
        return [], ""
    if len(services) != 1 or "" in services or ("mode" in selected and selected["mode"].nunique() > 1):
        raise ValueError("Replay expects eligible candidates from one service, origin and mode.")
    sites = []
    for row in selected.to_dict("records"):
        ident, scope = _text(row["facility_id"]), _text(row["scope"])
        distance, tier = _number(row["distance_km"]), _number(row["evidence_tier"])
        if tier is None:
            tier = EVIDENCE_TIERS.get(_text(row["evidence_tier"]))
        if "location_routing_eligible" in row and _capacity_prediction_flag(row["location_routing_eligible"]) is not True:
            continue
        if not ident or distance is None or distance < 0 or tier not in EVIDENCE_TIERS.values():
            continue
        sites.append({**row, "facility_id": ident, "distance_km": distance, "evidence_tier": int(tier), "scope": scope})
    if len({row["facility_id"] for row in sites}) != len(sites):
        raise ValueError("Pass a single candidate row per facility.")
    return sites, next(iter(services))


def _case_group(sites, index, routes, extra_km, extra_minutes):
    if not index:
        return {}, "no_pressure_inputs"
    reference = sites[0]
    same_evidence = [site for site in sites if (
        site["evidence_tier"] == reference["evidence_tier"] and reference["scope"]
        and site["scope"] == reference["scope"]
    )]
    nearby = {site["facility_id"] for site in same_evidence
              if _within_detour(site, reference, routes, extra_km, extra_minutes)}
    reference_groups = index.get(reference["facility_id"], {})
    groups = {}
    comparable_outside_detour = False
    for group in reference_groups:
        if not _monthly_flow(group[0], group[1]):
            continue
        peers = [site["facility_id"] for site in same_evidence if group in index.get(site["facility_id"], {})]
        measures = {ident: index[ident][group] for ident in peers if ident in nearby}
        if len(measures) > 1:
            groups[group] = measures
        elif len(peers) > 1:
            comparable_outside_detour = True
    if len(groups) > 1:
        return {}, "ambiguous_pressure_group"
    if not groups:
        status = "no_comparable_alternative_within_detour" if comparable_outside_detour else "no_comparable_pressure_inputs"
        return {}, status
    return next(iter(groups.values())), "simulated_suggestions_only"


def _output_pressure(measure, provenance, baseline, balanced):
    if not measure:
        return {}
    demand, capacity = measure["pressure_demand"], measure["pressure_capacity"]
    unit, period = measure["pressure_unit"], measure["pressure_period"]
    monthly = _monthly_flow(unit, period)
    return {
        "demand": demand, "capacity": capacity, "unit": unit,
        "period": period, "basis": measure["pressure_basis"],
        "scope": provenance.get("scope") or measure["pressure_basis"], "source_url": measure["pressure_source_url"],
        **{key: provenance.get(key, measure.get(f"pressure_{key}")) for key in (*PRESSURE_PROVENANCE, *REFERENCE_FIELDS)},
        "initial_pressure_ratio": _number(demand / capacity),
        "baseline_pressure_ratio": _number((demand + baseline) / capacity) if monthly else None,
        "balanced_pressure_ratio": _number((demand + balanced) / capacity) if monthly else None,
    }


def replay_network_load(candidates, pressure, requests, max_extra_km=10, max_extra_minutes=15, travel_weight=.5):
    """Replay one origin's routine monthly suggestions against a nearest eligible baseline.

    Candidates must come from one eligible lookup trace or be explicitly marked
    eligible for a hypothetical scenario. Each suggestion adds one comparable
    service activity to background demand. The throughput estimate is a soft
    scoring input and does not book care or cap the number of suggestions.
    """
    if not isinstance(requests, Integral) or isinstance(requests, bool) or not 0 <= requests <= 100_000:
        raise ValueError("requests must be an integer from 0 to 100,000.")
    km_cap, minute_cap, weight = map(_number, (max_extra_km, max_extra_minutes, travel_weight))
    if km_cap is None or km_cap < 0 or minute_cap is None or minute_cap < 0 or weight is None or not 0 <= weight <= 1:
        raise ValueError("Use nonnegative detour limits and a travel weight from 0 to 1.")
    sites, service = _replay_candidates(candidates)
    if not sites:
        return pd.DataFrame(columns=REPLAY_COLUMNS)
    if pressure is not None and not pressure.empty:
        _require(pressure, PRESSURE_COLUMNS, "pressure")
    routes = _routes({site["facility_id"]: [site] for site in sites
                      if _text(site.get("travel_source")) and _text(site.get("travel_status")) in ("", "estimated")}, None)
    stated_metric = {_text(site.get("ranking_travel_metric")) for site in sites} - {""}
    route_sort = "distance" if stated_metric == {ROUTE_DISTANCE_METRIC} else "time"
    metric = _travel_metric(sites, routes, route_sort)
    if not stated_metric and metric == DISTANCE_METRIC:
        metric = _travel_metric(sites, routes, "distance")
    if stated_metric and stated_metric != {metric}:
        metric = DISTANCE_METRIC
    travel = {site["facility_id"]: _travel_value(site, routes, metric) for site in sites}
    sites.sort(key=lambda site: (site["evidence_tier"], travel[site["facility_id"]], site["distance_km"], site["facility_id"]))
    ref = sites[0]
    ref_id = ref["facility_id"]
    index = _pressure_index(pressure, service) if pressure is not None and not pressure.empty else {}
    comparable, status = _case_group(sites, index, routes, km_cap, minute_cap)
    if not routine_load_allowed(service):
        comparable, status = {}, "routine_only"
    counts = dict.fromkeys(travel, 0)
    if comparable:
        limit = minute_cap if metric == ROUTE_METRIC else km_cap
        options = [site["facility_id"] for site in sites if site["facility_id"] in comparable]
        score_inputs = {
            ident: _score_pressure(comparable[ident], travel[ident] - travel[ref_id], limit, metric, weight)
            for ident in options
        }

        def next_score(ident):
            data = score_inputs[ident]
            demand = data["pressure_demand"] + counts[ident] + 1
            ratio = demand / data["pressure_capacity"]
            pressure_penalty = ratio / (1 + ratio) if isfinite(ratio) else 1.0
            return weight * data["travel_penalty"] + (1 - weight) * pressure_penalty

        queue = [(next_score(ident), position, ident) for position, ident in enumerate(options)]
        heapify(queue)
        for _ in range(requests):
            _, position, ident = heappop(queue)
            counts[ident] += 1
            heappush(queue, (next_score(ident), position, ident))
    else:
        counts[ref_id] = requests
    pressure_records = pressure.to_dict("records") if index else []
    provenance = {
        (_text(row["facility_id"]), _unit_key(row["unit"]), _text(row["period"]).casefold(),
         _text(row["basis"]).casefold(), _text(row["source_url"])): row
        for row in pressure_records if _text(row["service"]) == service
    }
    records = []
    for site in sites:
        ident = site["facility_id"]
        route, ref_route = routes.get(ident, {}), routes.get(ref_id, {})
        baseline = requests if ident == ref_id else 0
        measure = comparable.get(ident)
        if measure is None and len(index.get(ident, {})) == 1:
            measure = next(iter(index[ident].values()))
        detail = {}
        if measure is not None:
            key = (ident, _unit_key(measure["pressure_unit"]), measure["pressure_period"].casefold(),
                   measure["pressure_basis"].casefold(), measure["pressure_source_url"])
            detail = provenance.get(key, {})
        records.append({
            "facility_id": ident, "name": site.get("name"), "service": service,
            "evidence_tier": site["evidence_tier"], "evidence_scope": site["scope"],
            "distance_km": site["distance_km"], "travel_metric": metric, "travel_value": travel[ident],
            "extra_km": site["distance_km"] - ref["distance_km"],
            "extra_route_km": _route_difference(route, ref_route, ROUTE_DISTANCE_METRIC),
            "extra_minutes": _route_difference(route, ref_route, ROUTE_METRIC),
            "baseline_suggestions": baseline, "balanced_suggestions": counts[ident],
            "comparable": ident in comparable, "status": status,
            **_output_pressure(measure, detail, baseline, counts[ident]),
        })
    return pd.DataFrame(records, columns=REPLAY_COLUMNS)
