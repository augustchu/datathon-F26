"""Transparent service evidence and load-aware facility ranking."""
from __future__ import annotations

import math
import re


SERVICE_PROFILES = {
    "general care": {
        "terms": ["general medicine", "general physician", "primary care", "multispecialty",
                  "multi-specialty", "general hospital", "community health centre",
                  "community health center", "primary health centre", "primary health center",
                  "medical college hospital", "general care"],
        "indicators": [],
    },
    "dialysis": {
        "terms": ["dialysis", "haemodialysis", "hemodialysis", "renal dialysis"],
        "indicators": ["m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct",
                       "w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct"],
    },
    "emergency c-section": {
        "terms": ["emergency c-section", "emergency caesarean", "emergency cesarean",
                  "cesarean section", "caesarean section", "obstetric surgery"],
        "indicators": ["institutional_birth_5y_pct", "births_delivered_by_csection_5y_pct"],
    },
    "nicu": {
        "terms": ["nicu", "neonatal intensive care", "neonatal icu", "newborn intensive care"],
        "indicators": ["population_below_age_15_years_pct", "child_u5_who_are_stunted_height_for_age_18_pct"],
    },
    "cardiac surgery": {
        "terms": ["cardiac surgery", "heart surgery", "cardiothoracic surgery"],
        "indicators": ["m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct",
                       "w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct"],
    },
    "mri": {"terms": ["mri", "magnetic resonance imaging"], "indicators": []},
    "burn care": {"terms": ["burn care", "burn unit", "burns"], "indicators": []},
    "trauma": {"terms": ["trauma", "emergency medicine", "trauma care", "trauma center", "trauma centre"], "indicators": []},
    "pediatric illness": {
        "terms": ["pediatrics", "paediatrics", "pediatric", "paediatric", "child health", "nicu"],
        "indicators": ["children_prev_symptoms_of_acute_respiratory_infection_ari_2_pct",
                       "prev_diarrhoea_2wk_child_u5_pct",
                       "child_u5_who_are_stunted_height_for_age_18_pct"],
    },
    "maternal care": {
        "terms": ["maternity", "obstetrics", "obstetric", "delivery", "labour ward", "labor ward"],
        "indicators": ["institutional_birth_5y_pct", "births_delivered_by_csection_5y_pct"],
    },
}

GENERAL_PROVIDER_TERMS = [
    "hospital", "clinic", "health centre", "health center", "general medicine",
    "general physician", "primary care", "multispecialty",
    "multi-specialty", "general hospital", "community health centre",
    "community health center", "primary health centre", "primary health center",
    "medical college hospital", "general care", "district hospital",
]
SPECIALIST_ONLY_TERMS = [
    "dental clinic", "dentist", "dental", "dentistry", "eye clinic", "eye hospital",
    "ophthalmology clinic", "fertility clinic", "ivf clinic", "diagnostic centre",
    "diagnostic center", "imaging centre", "imaging center", "cancer hospital",
    "oncology centre", "oncology center", "cardiac hospital", "heart institute",
    "orthopedic hospital", "orthopaedic hospital",
]

URGENCY = {
    "emergency": {"load_weight": 0.0, "resource_units": 2.0},
    "urgent": {"load_weight": 0.18, "resource_units": 1.5},
    "routine": {"load_weight": 0.55, "resource_units": 1.0},
}


def _has_term(text, term):
    if not term:
        return False
    if len(term) <= 3:
        return re.search(r"\b" + re.escape(term) + r"\b", text, flags=re.I) is not None
    return term.lower() in text.lower()


def service_evidence(service_text, service):
    profile = SERVICE_PROFILES.get(service.lower(), {"terms": [service], "indicators": []})
    haystack = str(service_text or "")
    hits = [term for term in profile["terms"] if _has_term(haystack, term)]
    if not hits:
        return {"confidence": 0.0, "matched_terms": [], "evidence": ""}
    # The canonical service_text retains field labels supplied by the app.
    structured = any(tag in haystack.lower() for tag in
                     ["capability:", "procedure:", "specialty:", "equipment:"])
    confidence = 0.82 if structured else 0.58
    if len(hits) > 1:
        confidence = min(0.97, confidence + 0.07 * (len(hits) - 1))
    return {"confidence": confidence, "matched_terms": hits, "evidence": haystack[:1000]}


def _general_provider_evidence(row):
    """Conservatively include evidenced general facilities, not known specialist-only centers."""
    identity_text = " ".join(str(row.get(column, "") or "") for column in
                             ("facility_name", "facility_type", "service_text"))
    lower = identity_text.lower()
    general_hits = [term for term in GENERAL_PROVIDER_TERMS if _has_term(lower, term)]
    specialist_hits = [term for term in SPECIALIST_ONLY_TERMS if _has_term(lower, term)]
    if not general_hits or specialist_hits:
        return {"confidence": 0.0, "matched_terms": [], "evidence": ""}
    return {
        "confidence": 0.72,
        "matched_terms": general_hits,
        "evidence": identity_text[:1000],
    }


def rank_facilities(facilities, service, severity, travel, current_load=None,
                    minimum_evidence=0.35):
    """Rank candidate rows. Emergency ranking deliberately ignores load pressure."""
    current_load = current_load or {}
    urgency = URGENCY.get(severity, URGENCY["routine"])
    ranked = []
    for row in facilities.to_dict("records"):
        evidence = (_general_provider_evidence(row) if service.lower() == "general care"
                    else service_evidence(row.get("service_text"), service))
        if evidence["confidence"] < minimum_evidence:
            continue
        facility_id = row["facility_id"]
        metrics = travel.get(facility_id, {})
        minutes = metrics.get("travel_time_minutes")
        distance_km = metrics.get("distance_km")
        capacity = row.get("capacity_units")
        try:
            capacity = float(capacity)
            if not math.isfinite(capacity) or capacity <= 0:
                capacity = None
        except (TypeError, ValueError):
            capacity = None
        load = float(current_load.get(facility_id, 0.0))
        load_ratio = min(2.0, load / capacity) if capacity else None
        confidence_penalty = 1.0 - evidence["confidence"]
        time_minutes = float(minutes) if minutes is not None else None
        if severity == "emergency":
            score = time_minutes if time_minutes is not None else (distance_km or 1e9)
        else:
            # Lower is better; capacity is a soft penalty, never a clinical gate.
            travel_component = time_minutes if time_minutes is not None else (distance_km or 1e9) * 2
            load_penalty = (urgency["load_weight"] * 60 * (load_ratio ** 2)) if load_ratio is not None else 0
            score = travel_component + 35 * confidence_penalty + load_penalty
        ranked.append({
            **row,
            "score": score,
            "severity": severity,
            "service_confidence": evidence["confidence"],
            "matched_terms": ", ".join(evidence["matched_terms"]),
            "supporting_text": evidence["evidence"],
            "travel_time_minutes": time_minutes,
            "distance_km": distance_km,
            "current_load": load,
            "capacity_units": capacity,
            "load_ratio": load_ratio,
            "load_penalty_applied": 0.0 if severity == "emergency" else (
                urgency["load_weight"] * 60 * (load_ratio ** 2) if load_ratio is not None else 0.0
            ),
            "source_url": row.get("source_url", ""),
        })
    return sorted(ranked, key=lambda r: (r["score"], -r["service_confidence"]))

