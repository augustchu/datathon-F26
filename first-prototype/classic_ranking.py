"""Recommended and personalized ranking for the map-based dashboard."""
from __future__ import annotations

import streamlit as st

from classic_confidence import (
    MATCH_PENALTY_MAX_KM,
    ROUTE_MINUTES_PER_KM_EQUIVALENT,
    assess_service_confidence,
    evidence_penalty_minutes,
)

RECOMMENDED = "Recommended"
RANKED_CRITERIA = "Ranked criteria (best match first)"
SEVERITIES = ("Routine", "Urgent", "Emergency")

CRITERIA = {
    "distance": "Distance (closer is better)",
    "evidence": "Service evidence",
    "capability": "Equipment & staff match",
    "availability": "Open around the clock",
    "quality": "Accreditation & rating",
    "freshness": "Up-to-date record",
    "spare": "Spare capacity (lower occupancy)",
}


def score_recommended(results, service, aliases, *, uses_routes=False):
    """Apply the published service match and confidence adjustments to travel."""
    minutes_per_km = ROUTE_MINUTES_PER_KM_EQUIVALENT
    multiplier = minutes_per_km if uses_routes else 1.0
    units = "min equivalent" if uses_routes else "km equivalent"
    for item in results:
        confidence = assess_service_confidence(item["row"], service, aliases)
        item["service_confidence"] = confidence
        match_penalty = MATCH_PENALTY_MAX_KM * (1 - confidence.match_score / 100) * multiplier
        confidence_penalty = float(evidence_penalty_minutes(confidence.score)) * multiplier / minutes_per_km
        travel_cost = item["route_minutes"] if uses_routes else item["dist"]
        item["recommended"] = {
            "travel": travel_cost,
            "service_match": match_penalty,
            "service_confidence": confidence_penalty,
            "score": travel_cost + match_penalty + confidence_penalty,
            "units": units,
        }


def render_priorities():
    if not st.toggle("Advanced options", value=False):
        return []
    st.markdown("**My priorities**")
    st.caption("Select the criteria that matter to you and put them in order. Choose Ranked criteria in the result sort to apply them.")
    cols = st.columns(2)
    selected = [
        key for i, (key, label) in enumerate(CRITERIA.items())
        if cols[i % len(cols)].toggle(label, key=f"priority_enabled_{key}")
    ]
    if not selected:
        return []
    count = len(selected)
    ranked = []
    for position, key in enumerate(selected, 1):
        label = CRITERIA[key]
        widget_key = f"priority_order_{key}_{count}"
        name_col, rank_col = st.columns([4, 1])
        name_col.write(label)
        rank = rank_col.number_input(f"Rank for {label}", min_value=1, max_value=count,
                                     value=position, step=1, key=widget_key, label_visibility="collapsed")
        ranked.append((rank, position, key))
    order = [key for _, _, key in sorted(ranked)]
    st.caption("Priority order " + " · ".join(CRITERIA[key] for key in order))
    return order


def score_priorities(results, order):
    """Score selected criteria using rank-order centroid weights."""
    count = len(order)
    weights = [sum(1 / j for j in range(i, count + 1)) / count for i in range(1, count + 1)]
    distances = [item["dist"] for item in results]
    nearest, farthest = min(distances), max(distances)
    for item in results:
        values = {
            "distance": 1.0 if farthest == nearest else (farthest - item["dist"]) / (farthest - nearest),
            "evidence": item["service_confidence"].score / 100,
            "capability": item["comps"]["capability"],
            "availability": item["comps"]["availability"],
            "quality": item["comps"]["quality"],
            "freshness": item["comps"]["confidence"],
            "spare": 0.5 if item["spare"] is None else item["spare"],
        }
        item["priority"] = round(100 * sum(weight * values[key] for key, weight in zip(order, weights)), 1)
        item["priority_parts"] = [(CRITERIA[key], round(values[key], 2), round(weight, 3))
                                  for key, weight in zip(order, weights)]
