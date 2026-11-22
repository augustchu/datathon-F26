"""Apply the project's same-service caseload and throughput projections."""

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


PROJECTION_PATH = Path(__file__).with_name("projected_load.csv")
TIME_CRITICAL_SERVICES = {"Emergency C-section", "Trauma / emergency", "NICU", "Burn care", "Cardiac surgery"}
MAX_EXTRA_KM = 10.0
MAX_EXTRA_MINUTES = 15.0


@lru_cache(maxsize=1)
def load_projections():
    return pd.read_csv(PROJECTION_PATH, keep_default_na=False)


def prefer_projected_load(ranked, service, *, routed):
    """Promote a nearby option with comparable projected pressure, preserving the remaining order."""
    try:
        projections = load_projections()
    except OSError:
        return ranked, 0

    selected = projections.loc[projections["care_needed"].str.strip().str.casefold().eq(service.casefold())]
    selected = selected.drop(columns="care_needed").rename(columns={"projection_facility_id": "canonical_id"})
    selected = selected.drop_duplicates().drop_duplicates("facility_id", keep=False)
    work = ranked.join(selected.set_index("facility_id").add_prefix("projected_"), on="facility_id")
    if work.empty:
        return work, 0
    baseline = work.iloc[0]
    if not pd.notna(baseline["projected_capacity"]):
        return work, 0

    same_scope = work["service_evidence_kind"].eq(baseline["service_evidence_kind"])
    for field in ("projected_unit", "projected_scope", "projected_period"):
        same_scope &= work[field].eq(baseline[field])
    nearby = work["distance_km"].sub(baseline["distance_km"]).le(MAX_EXTRA_KM)
    if routed:
        nearby &= work["travel_time_minutes"].sub(baseline["travel_time_minutes"]).le(MAX_EXTRA_MINUTES)
    demand, capacity = work["projected_caseload"], work["projected_capacity"]
    usable = np.isfinite(demand) & demand.ge(0) & np.isfinite(capacity) & capacity.gt(0)
    comparable = work.loc[same_scope & nearby & usable].drop_duplicates("projected_canonical_id")
    n_comparable = len(comparable)
    if n_comparable < 2:
        return work, 0

    # This is a relative planning score, not a probability of getting care.
    max_extra = MAX_EXTRA_MINUTES if routed else MAX_EXTRA_KM
    detour = comparable["base_travel_cost"].sub(baseline["base_travel_cost"]).clip(lower=0).div(max_extra)
    ratio = comparable["projected_caseload"].div(comparable["projected_capacity"])
    cost = (detour + ratio.div(1 + ratio)) / 2
    work["projected_load_comparison_score"] = cost
    winner = cost.idxmin()
    return pd.concat([work.loc[[winner]], work.drop(index=winner)]), n_comparable
