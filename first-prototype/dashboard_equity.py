"""Connect the classic dashboard to the opt-in paired equity ranking."""

from dataclasses import dataclass
import re

import streamlit as st

from equity import (
    MAX_EXTRA_KM,
    EquityCandidate,
    EquityScore,
    load_paired_equity_evidence,
    rank_for_paired_equity,
    synthetic_equity_evidence,
)


DEMO_SERVICE = "Maternity / delivery"
DEMO_BASELINE = "synthetic-new-01"
DEMO_HIGHER_SCORE = "synthetic-new-06"


@dataclass(frozen=True)
class EquityView:
    results: list[dict]
    prior_indices: tuple[int, ...]
    scores: dict[int, EquityScore]
    status: str


def adjust_demo_equity_coordinates(rows):
    """Put two fictional maternity facilities within a kilometer of each other."""
    facilities = {row["unique_id"]: row for row in rows}
    baseline = facilities[DEMO_BASELINE]
    higher_score = facilities[DEMO_HIGHER_SCORE]
    higher_score["latitude"] = baseline["latitude"] + 0.005
    higher_score["longitude"] = baseline["longitude"]


def render_equity_control(service, *, synthetic=False):
    control, explanation, _ = st.columns([2, 1, 5], gap="small", vertical_alignment="center")
    with control:
        enabled = st.toggle("Consider equity score", value=False, key="consider_equity_score")
    with explanation.popover("What's that?"):
        st.write("Women and people from marginalized groups can receive worse care. "
                 "The equity score combines patient-reported respect and clinical outcomes to help choose between similar hospitals.")
    if enabled and synthetic and service == DEMO_SERVICE:
        st.caption("Try the fictional maternity example at coordinates 28.551395, 77.185600.")
    return enabled


def prepare_equity_results(candidates, service, top_n, *, enabled=False, synthetic=False, urgent=False):
    """Apply equity after the chosen sort, then take the visible results for the cards and map."""
    base_indices = tuple(range(min(len(candidates), top_n)))
    if not enabled or urgent:
        return EquityView(candidates[:top_n], base_indices, {}, "urgent" if enabled else "off")

    service_key = re.sub(r"[^a-z0-9]+", "_", service.lower()).strip("_")
    try:
        if synthetic:
            evidence = synthetic_equity_evidence("maternity_delivery", f"fdr:{DEMO_BASELINE}", f"fdr:{DEMO_HIGHER_SCORE}")
        else:
            evidence = load_paired_equity_evidence()
    except (OSError, ValueError):
        return EquityView(candidates[:top_n], base_indices, {}, "unreadable")

    inputs = []
    for candidate in candidates:
        row = candidate["row"]
        minutes = candidate.get("route_minutes")
        inputs.append(EquityCandidate(
            facility_id=row.get("equity_facility_id", ""),
            tier=candidate.get("equity_tier"),
            distance_km=candidate["dist"],
            minutes=minutes,
            route_context="current_search" if minutes is not None else None,
        ))
    ranking = rank_for_paired_equity(inputs, service_key, enabled=True, evidence=evidence)
    visible = ranking.order[:top_n]
    scores = {index: ranking.scores[index] for index in visible if index in ranking.scores}
    if scores:
        status = "reranked" if visible != base_indices else "matched"
    else:
        status = "unavailable"
    return EquityView([candidates[index] for index in visible], visible, scores, status)


def render_equity_summary(view):
    if view.status == "reranked":
        st.caption(f"Equity scores moved comparable options within {MAX_EXTRA_KM:g} km of additional straight-line travel. "
                   "Each card shows its rank before the adjustment.")
    elif view.status == "matched":
        st.info("The available equity scores kept the displayed order.")
    elif view.status == "unavailable":
        st.info("Comparable patient-reported respect and clinical outcomes are unavailable for the options shown. "
                "The original order is shown.")
    elif view.status == "unreadable":
        st.warning("The equity evidence file could not be read. The original order is shown.")
    elif view.status == "urgent":
        st.info("Equity scoring is paused for time-critical searches.")


def render_equity_card(view, result_index):
    if not view.scores:
        return
    prior_index = view.prior_indices[result_index]
    st.caption(f"Rank before equity score {prior_index + 1}")
    score = view.scores.get(prior_index)
    if score is None:
        st.caption("Comparable equity score unavailable for this branch and service.")
        return

    record = score.evidence
    label = "Fictional equity score" if record.fictional else "Equity score"
    st.markdown(f"**{label} {score.value:.0f}/100**")
    with st.expander("Equity score evidence"):
        st.caption(f"Uncertainty range {score.lower:.0f}–{score.upper:.0f} out of 100")
        st.write(f"{record.respect_measure} · {record.respect_rate:.1%} (n={record.respect_n:,})")
        st.write(f"{record.outcome_measure} · {record.outcome_rate:.1%} (n={record.outcome_n:,})")
        st.caption(f"{record.population} · {record.period_start} to {record.period_end}")
        st.caption(f"Clinical adjustment · {record.adjustment_method}")
        if record.source_url:
            st.link_button(record.source_title, record.source_url)
        else:
            st.caption(record.source_title)
