"""Recommended and personalized ranking for the map-based dashboard."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import care_engine

RECOMMENDED = "Recommended"
RANKED_CRITERIA = "Ranked criteria (best match first)"
SEVERITIES = ("Routine", "Urgent", "Emergency")

ENGINE_SERVICE = {
    "Maternity / delivery": "maternal care",
    "Pediatric care": "pediatric illness",
    "Trauma / emergency": "trauma",
}
CRITERIA = {
    "distance": "Distance (closer is better)",
    "evidence": "Service evidence",
    "capability": "Equipment & staff match",
    "availability": "Open around the clock",
    "quality": "Accreditation & rating",
    "freshness": "Up-to-date record",
    "spare": "Spare capacity (lower occupancy)",
}


def score_recommended(results, service, aliases, severity, *, uses_routes=False):
    """Combine travel, service evidence, and the snapshot confidence adjustment."""
    if not results:
        return

    service_key = ENGINE_SERVICE.get(service, service.lower())
    # Classic services with no predefined care_engine profile use the original catalog aliases.
    care_engine.SERVICE_PROFILES.setdefault(service_key, {"terms": [*aliases, service.lower()], "indicators": []})
    frame = []
    travel = {}
    for i, item in enumerate(results):
        row = item["row"]
        frame.append({
            "facility_id": i,
            "facility_name": row["name"],
            "facility_type": row.get("facilityTypeId", ""),
            "service_text": row["service_text"],
        })
        travel[i] = {
            "distance_km": max(item["dist"], 0.01),
            "travel_time_minutes": item["scenario"]["travel_time_minutes"] if uses_routes else None,
        }

    severity = severity.lower()
    emergency = severity == "emergency"
    # Existing care_engine uses a 2 min/km proxy for routine and urgent straight-line searches.
    # The snapshot penalties are specified in km equivalents, so convert them to the same units.
    multiplier = 2 if uses_routes or not emergency else 1
    units = "min equivalent" if multiplier == 2 else "km equivalent"
    scored = care_engine.rank_facilities(pd.DataFrame(frame), service_key, severity,
                                        travel, minimum_evidence=0.0)
    for engine in scored:
        item = results[engine["facility_id"]]
        scenario = item.get("scenario")
        confidence_penalty = multiplier * float(scenario["confidence_penalty_km"]) if scenario is not None else 0.0
        travel_cost = engine["travel_time_minutes"]
        if not uses_routes:
            travel_cost = multiplier * engine["distance_km"]
        item["recommended"] = {
            "travel": travel_cost,
            "evidence": 0.0 if emergency else 35 * (1 - engine["service_confidence"]),
            "data_confidence": confidence_penalty,
            "score": engine["score"] + confidence_penalty,
            "units": units,
            "service_confidence": engine["service_confidence"],
            "matched_terms": engine["matched_terms"],
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
            "evidence": item["recommended"]["service_confidence"] * item["match"],
            "capability": item["comps"]["capability"],
            "availability": item["comps"]["availability"],
            "quality": item["comps"]["quality"],
            "freshness": item["comps"]["confidence"],
            "spare": 0.5 if item["spare"] is None else item["spare"],
        }
        item["priority"] = round(100 * sum(weight * values[key] for key, weight in zip(order, weights)), 1)
        item["priority_parts"] = [(CRITERIA[key], round(values[key], 2), round(weight, 3))
                                  for key, weight in zip(order, weights)]
