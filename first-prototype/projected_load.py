"""Apply the project's same-service caseload and throughput projections."""

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


PROJECTION_PATH = Path(__file__).with_name("projected_load.csv")
TIME_CRITICAL_SERVICES = {
    "Emergency C-section", "Trauma / emergency", "NICU", "Burn care", "Cardiac surgery",
    "Fracture care", "Stroke care",
}
MAX_EXTRA_KM = 10.0
MAX_EXTRA_MINUTES = 15.0


@lru_cache(maxsize=1)
def load_projections():
    return pd.read_csv(PROJECTION_PATH, keep_default_na=False)


def prefer_projected_load(ranked, service, *, routed):
    """Promote a nearby option with comparable projected pressure, preserving the remaining order."""
    if ranked.empty or service in TIME_CRITICAL_SERVICES:
        return ranked, 0
    try:
        projections = load_projections()
    except (OSError, pd.errors.ParserError):
        return ranked, 0

    service_matches = projections["care_needed"].str.strip().str.casefold().eq(service.casefold())
    selected = projections.loc[service_matches & projections["facility_id"].ne("")]
    selected = selected.drop(columns="care_needed").rename(columns={"projection_facility_id": "canonical_id"})
    selected = selected.drop_duplicates().drop_duplicates("facility_id", keep=False)
    work = ranked.join(selected.set_index("facility_id").add_prefix("projected_"), on="facility_id")
    demand = pd.to_numeric(work["projected_caseload"], errors="coerce")
    capacity = pd.to_numeric(work["projected_capacity"], errors="coerce")
    usable = np.isfinite(demand) & demand.ge(0) & np.isfinite(capacity) & capacity.gt(0)
    for field in ("projected_unit", "projected_scope", "projected_period", "projected_canonical_id"):
        usable &= work[field].fillna("").str.strip().ne("")
    work["projected_caseload"] = demand.where(usable)
    work["projected_capacity"] = capacity.where(usable)
    if not usable.iloc[0]:
        return work, 0

    baseline = work.iloc[0]
    same_scope = work["service_evidence_kind"].eq(baseline["service_evidence_kind"])
    for field in ("projected_unit", "projected_scope", "projected_period"):
        same_scope &= work[field].eq(baseline[field])
    nearby = work["distance_km"].sub(baseline["distance_km"]).le(MAX_EXTRA_KM)
    if routed:
        nearby &= work["travel_time_minutes"].sub(baseline["travel_time_minutes"]).le(MAX_EXTRA_MINUTES)
    comparable = work.loc[same_scope & nearby & usable].drop_duplicates("projected_canonical_id")
    n_comparable = len(comparable)
    if n_comparable < 2:
        return work, 0

    # This is a relative planning score, not a probability of getting care.
    max_extra = MAX_EXTRA_MINUTES if routed else MAX_EXTRA_KM
    extra_travel = comparable["base_travel_cost"].sub(baseline["base_travel_cost"]).clip(lower=0)
    demand, capacity = comparable["projected_caseload"], comparable["projected_capacity"]
    pressure = 1 - 1 / (1 + demand.div(capacity))
    cost = (extra_travel.div(max_extra) + pressure) / 2
    work["projected_pressure"] = pressure
    work["projected_extra_travel"] = extra_travel
    work["projected_load_comparison_score"] = cost
    winner = cost.idxmin()
    return pd.concat([work.loc[[winner]], work.drop(index=winner)]), n_comparable
