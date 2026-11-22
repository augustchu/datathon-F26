"""Regional demand scenarios and sequential load-distribution simulation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from care_engine import SERVICE_PROFILES, URGENCY, rank_facilities
from issue_catalog import ISSUE_CATALOG


def _numeric_indicator(series):
    raw = series.astype(str).str.strip()
    low_confidence = raw.str.startswith("(") & raw.str.endswith(")")
    raw = raw.str.replace(r"^\((.*)\)$", r"\1", regex=True).replace("*", pd.NA)
    return pd.to_numeric(raw, errors="coerce"), low_confidence


def regional_burden(regions, service):
    """Return a relative demand index; NFHS percentages are proxies, not incidence counts."""
    profile = SERVICE_PROFILES.get(service.lower(), {"indicators": []})
    fields = [c for c in profile["indicators"] if c in regions.columns]
    out = regions.copy()
    if not fields:
        out["burden_proxy"] = 1.0
        out["burden_basis"] = "no mapped NFHS indicator; uniform within population"
        return out
    numeric = pd.DataFrame(index=out.index)
    for field in fields:
        numeric[field], _ = _numeric_indicator(out[field])
    raw = numeric.mean(axis=1, skipna=True)
    national = numeric.stack().mean()
    if pd.isna(national) or national <= 0:
        relative = pd.Series(1.0, index=out.index)
    else:
        relative = (raw / national).replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0.5, 2.0)
    out["burden_proxy"] = relative
    out["burden_basis"] = "relative NFHS-5 indicator proxy: " + ", ".join(fields)
    return out


def generate_cases(regions, region_geometries, service, n_cases=30, seed=7):
    """Sample synthetic demand by population x relative NFHS burden."""
    rng = np.random.default_rng(seed)
    burden = regional_burden(regions, service)
    pop_col = "pop_2020"
    if pop_col not in burden:
        raise ValueError("Regional input needs pop_2020.")
    burden = burden[pd.to_numeric(burden[pop_col], errors="coerce").fillna(0) > 0].copy()
    burden = burden.merge(
        region_geometries[["nfhs_district_key", "geometry"]],
        on="nfhs_district_key", how="inner", validate="one_to_one",
    )
    weights = burden[pop_col].astype(float) * burden.burden_proxy.astype(float)
    if weights.sum() <= 0:
        raise ValueError("No regions with both mapped population and geometry.")
    selected = rng.choice(burden.index.to_numpy(), size=n_cases, p=(weights / weights.sum()).to_numpy())
    severity = rng.choice(["emergency", "urgent", "routine"], size=n_cases, p=[0.05, 0.20, 0.75])
    rows = []
    for i, idx in enumerate(selected):
        region = burden.loc[idx]
        point = region.geometry.representative_point()
        rows.append({
            "case_id": f"case_{i+1:04d}",
            "nfhs_district_key": region.nfhs_district_key,
            "district_name": region.nfhs_district_name,
            "state_ut": region.nfhs_state,
            "latitude": point.y,
            "longitude": point.x,
            "service": service,
            "severity": severity[i],
            "burden_proxy": region.burden_proxy,
            "burden_basis": region.burden_basis,
        })
    return pd.DataFrame(rows)


def generate_issue_cases(regions, region_geometries, issue_id, intensity, seed=7):
    """Generate exactly `intensity` synthetic cases for one dropdown issue."""
    if issue_id not in ISSUE_CATALOG:
        raise ValueError(f"Unknown medical issue id: {issue_id}")
    if int(intensity) < 0:
        raise ValueError("Simulation intensity must be zero or greater.")
    issue = ISSUE_CATALOG[issue_id]
    cases = generate_cases(regions, region_geometries, issue["nfhs_profile"],
                           n_cases=int(intensity), seed=seed)
    cases["medical_issue_id"] = issue_id
    cases["medical_issue"] = issue["label"]
    return cases


def run_load_simulation(cases, facilities, travel_provider):
    """Assign each synthetic case in turn and update facility load."""
    route_facilities = facilities.dropna(subset=["latitude", "longitude"]).copy()
    route_facilities = route_facilities[
        route_facilities.latitude.between(-90, 90) & route_facilities.longitude.between(-180, 180)
    ]
    loads = {fid: 0.0 for fid in route_facilities.facility_id}
    assignments = []
    for case in cases.to_dict("records"):
        travel, provider_name = travel_provider(
            (case["latitude"], case["longitude"]), route_facilities
        )
        ranked = rank_facilities(
            route_facilities, case["service"], case["severity"], travel,
            current_load=loads,
        )
        ranked = [row for row in ranked if row.get("travel_time_minutes") is not None]
        if not ranked:
            assignments.append({**case, "facility_id": None, "assignment_status": "no_evidence_match",
                                "travel_provider": provider_name})
            continue
        chosen = ranked[0]
        facility_id = chosen["facility_id"]
        resource_units = URGENCY[case["severity"]]["resource_units"]
        loads[facility_id] += resource_units
        assignments.append({
            **case,
            "facility_id": facility_id,
            "facility_name": chosen["facility_name"],
            "travel_time_minutes": chosen["travel_time_minutes"],
            "distance_km": chosen["distance_km"],
            "service_confidence": chosen["service_confidence"],
            "matched_terms": chosen["matched_terms"],
            "load_before": chosen["current_load"],
            "capacity_units": chosen["capacity_units"],
            "load_after": loads[facility_id],
            "load_ratio_after": loads[facility_id] / chosen["capacity_units"]
                if chosen["capacity_units"] else None,
            "load_penalty_applied": chosen["load_penalty_applied"],
            "score": chosen["score"],
            "source_url": chosen["source_url"],
            "travel_provider": provider_name,
            "assignment_status": "assigned",
        })
    return pd.DataFrame(assignments), pd.DataFrame(
        [{"facility_id": fid, "simulated_load_units": amount} for fid, amount in loads.items()]
    )


def run_issue_scenario(regions, region_geometries, facilities, issue_id, intensity,
                       travel_provider, seed=7):
    """Backend-ready entry point: issue id + numeric intensity -> cases and assignments."""
    issue = ISSUE_CATALOG.get(issue_id)
    if issue is None:
        raise ValueError(f"Unknown medical issue id: {issue_id}")
    cases = generate_issue_cases(regions, region_geometries, issue_id, intensity, seed)
    cases["service"] = issue["service_key"]
    assignments, loads = run_load_simulation(cases, facilities, travel_provider)
    return cases, assignments, loads

