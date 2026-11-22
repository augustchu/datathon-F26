"""Streamlit user interface for the Databricks first prototype."""
from __future__ import annotations

import json
from math import isfinite
import os
import re
from urllib.parse import quote

import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

from metric_explanations import EXPLANATIONS
from pipeline import SERVICE_TERMS, rank_candidates, resolve_location, service_candidates
from service_confidence import RECORD_TEXT_KINDS
from traveltime_adapter import travel_time_matrix

st.set_page_config(page_title="Find the right care", page_icon="🏥", layout="wide")
FACILITY_TABLE = os.getenv("FACILITY_SNAPSHOT_TABLE", "additional_india_data.default.first_prototype_facilities")
SNAPSHOT_TABLE = os.getenv("SNAPSHOT_METADATA_TABLE", "additional_india_data.default.first_prototype_snapshot_metadata")
REPOSITORY_DOCUMENTS = "https://github.com/augustchu/datathon-F26/blob/main/"
OPEN_EXPLANATIONS = "open_calculation_explanations"


@st.cache_data(ttl=300, show_spinner="Loading the prepared facility snapshot...")
def load_snapshot(table_name: str):
    """Read one prepared snapshot through the app's Databricks SQL resource."""
    http_path = os.getenv("DATABRICKS_HTTP_PATH") or os.getenv("DATABRICKS_WAREHOUSE_HTTP_PATH")
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    if not http_path and warehouse_id:
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    if not http_path:
        raise RuntimeError("Add a SQL warehouse resource to this app and set DATABRICKS_HTTP_PATH to its HTTP path.")
    cfg = Config()
    hostname = cfg.host.removeprefix("https://").removeprefix("http://").rstrip("/")
    with sql.connect(server_hostname=hostname, http_path=http_path,
                     credentials_provider=lambda: cfg.authenticate) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {table_name}")
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
    return pd.DataFrame(rows, columns=columns)


def source_urls(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def toggle_explanation(key):
    open_keys = st.session_state.get(OPEN_EXPLANATIONS, set())
    st.session_state[OPEN_EXPLANATIONS] = open_keys ^ {key}


def show_more_info(metric, label, scope="search"):
    key = f"more-info:{scope}:{metric}"
    explanation = EXPLANATIONS[metric]
    st.button("More info", key=key, help=f"Explain {label}", on_click=toggle_explanation, args=(key,))
    if key in st.session_state.get(OPEN_EXPLANATIONS, set()):
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.markdown(f"**What does this mean?**\n\n{explanation.meaning}")
            st.markdown(f"**How was this calculated?**\n\n{explanation.calculation}")
            st.markdown(f"**Why was it calculated this way?**\n\n{explanation.reason}")
            links = [f"[{title}]({REPOSITORY_DOCUMENTS}{quote(path, safe='/#')})" for title, path in explanation.sources]
            st.markdown("Repository sources · " + " · ".join(links))


def show_calculated(label, value, metric, scope="search", *, compact=False):
    if compact:
        st.markdown(f"**{label}** · {value}")
    else:
        st.metric(label, value)
    show_more_info(metric, label, scope)


@st.cache_data(ttl=1800, max_entries=32, show_spinner="Getting driving times from TravelTime...")
def cached_travel_times(origin, facility_records):
    destinations = pd.DataFrame(facility_records, columns=["facility_id", "latitude", "longitude"])
    return travel_time_matrix(origin, destinations)


st.title("Find the right care")
st.caption("Evidence-linked facility options from the prepared Virtue Foundation snapshot. This prototype is not a diagnosis or live availability service.")

try:
    facilities = load_snapshot(FACILITY_TABLE)
except Exception as exc:
    st.error(f"Could not load `{FACILITY_TABLE}`. Run `prepare_snapshot.py` in Databricks, attach a SQL warehouse resource, and check the app's table permissions. Details: {exc}")
    st.stop()

if facilities.empty:
    st.warning("The prepared snapshot contains no facility rows.")
    st.stop()

try:
    snapshot_meta = load_snapshot(SNAPSHOT_TABLE)
    snapshot_date = str(snapshot_meta.iloc[0].get("prepared_at", "unknown")) if not snapshot_meta.empty else "unknown"
except Exception:
    snapshot_date = "metadata unavailable"

with st.sidebar:
    st.header("Search")
    service = st.selectbox("Care needed", list(SERVICE_TERMS))
    severity = st.radio("Urgency", ["routine", "urgent", "emergency"], horizontal=False,
                        help="Emergency ranking uses travel, service match, and evidence confidence, with no regional demand adjustment.")
    population_share_pct = st.slider("Regional population needing this care", 0.0, 2.0, 1.0, 0.1, format="%.1f%%",
                                     help="Scenario: this share of each hospital district's modeled population needs this service. Expected cases are split evenly across evidence-backed hospitals in that district; this is not a forecast.")
    show_more_info("scenario_share", "Regional population needing this care")
    top_k = st.slider("Options to show", 1, 10, 5)
    use_traveltime = st.toggle("Use TravelTime driving estimates", value=True,
                              help="Uses weekday-morning driving minutes as the travel input when routes are available.")
    coordinate_mode = st.toggle("Enter coordinates", value=False)
    if coordinate_mode:
        lat = st.number_input("Latitude", min_value=6.0, max_value=38.0, value=19.0760, format="%.5f")
        lon = st.number_input("Longitude", min_value=68.0, max_value=98.0, value=72.8777, format="%.5f")
        location_text = ""
    else:
        location_text = st.text_input("City, district, or PIN", value="Jaipur, Rajasthan", placeholder="e.g. Jaipur, Rajasthan or 302001")
        lat = lon = None

st.caption(f"Prepared snapshot · {snapshot_date}")
show_calculated("Facility records in snapshot", f"{len(facilities):,}", "snapshot_records", compact=True)
if "location_search_eligible" not in facilities:
    st.info("Refresh the prepared snapshot to enable service evidence and location screening.")
st.warning("Bed counts do not show how many beds are staffed, free, or available for this service. Confirm service and availability with the facility before travel.")

if st.button("Find facility options", type="primary"):
    st.session_state["search_started"] = True

search_started = st.session_state.get("search_started", False)
candidates = service_candidates(facilities, service) if search_started else None
if search_started and candidates.empty:
    st.subheader(f"Options for {service}")
    st.warning(f"No facilities with supporting evidence and a usable location were found for {service} in this snapshot.")
elif search_started:
    try:
        origin, origin_basis = resolve_location(facilities, location_text, lat, lon)
        travel_times = None
        nearest_only = False
        reachable = None
        travel_status = "Straight-line distance mode"
        if use_traveltime:
            destination_records = tuple(
                (str(r.facility_id), float(r.latitude), float(r.longitude))
                for r in candidates[["facility_id", "latitude", "longitude"]].itertuples(index=False)
            )
            try:
                travel_times = cached_travel_times(tuple(origin), destination_records)
                route_minutes = (travel_times.get(fid) for fid, _, _ in destination_records)
                reachable = sum(value is not None and isfinite(value) and value >= 0 for value in route_minutes)
                if reachable:
                    travel_status = "TravelTime driving estimates"
                else:
                    travel_times = None
                    nearest_only = True
                    travel_status = "Closest by straight-line distance"
            except Exception as exc:
                travel_times = None
                travel_status = "Straight-line fallback: TravelTime unavailable"
                st.warning(f"TravelTime could not be used ({type(exc).__name__}); showing the straight-line kilometer ranking.")
        ranked = rank_candidates(facilities, service, origin, severity=severity,
                                 population_share_pct=population_share_pct, top_k=top_k,
                                 travel_times=travel_times, nearest_only=nearest_only)
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    st.subheader(f"Options for {service}")
    using_routes = travel_times is not None and not ranked.empty
    show_calculated("Search origin", f"{origin[0]:.5f}, {origin[1]:.5f} · {origin_basis}",
                    "search_origin", compact=True)
    st.caption(travel_status)
    if reachable is not None:
        show_calculated("Reachable facility records", f"{reachable:,}", "reachable_count", compact=True)
    if ranked.empty:
        if travel_times is not None:
            st.warning("No evidence-backed facility was returned as reachable within TravelTime's 3-hour search limit. This does not establish that care is unavailable.")
        else:
            st.warning("No facility in this snapshot passed the service evidence and coordinate checks. This does not establish that care is unavailable.")
    else:
        if nearest_only:
            st.warning("TravelTime found no matching facilities it could route to within its three-hour search window. Showing the closest matches by straight-line distance.")
        map_points = pd.concat([
            ranked[["latitude", "longitude"]].assign(color="#2563eb"),
            pd.DataFrame([{"latitude": origin[0], "longitude": origin[1], "color": "#c2410c"}]),
        ], ignore_index=True)
        st.map(map_points, color="color", height=340)
        st.caption("Blue shows facility results. Orange shows the search origin.")
        order = "Closest by straight-line distance" if nearest_only else "Lowest ranking score first"
        show_calculated("Result order", order, "result_order", compact=True)
        if not nearest_only:
            base = "driving minutes" if using_routes else "straight-line kilometers"
            if severity == "emergency":
                st.info(f"Emergency ranking uses {base}, service match, and evidence confidence.")
            else:
                st.info(f"Ranking uses {base}, service match, evidence confidence, and the selected regional demand scenario.")
        for rank, row in ranked.iterrows():
            distance_label = f"{row.travel_time_minutes:.0f} min drive" if using_routes else f"{row.distance_km:.1f} km straight-line"
            scope = str(row.facility_id)
            title = f"{rank + 1}. {row.facility_name} · {distance_label}"
            if row.service_evidence_kind in RECORD_TEXT_KINDS:
                title += f" · {row.service_evidence_label}"
            with st.expander(title, expanded=(rank == 0)):
                metrics = st.columns(3 if nearest_only else 4)
                c1, c2, c3 = metrics[:3]
                with c1:
                    if using_routes:
                        show_calculated("TravelTime drive", f"{row.travel_time_minutes:.0f} min", "travel_time", scope)
                    else:
                        show_calculated("Straight-line distance", f"{row.distance_km:.1f} km", "straight_line_distance", scope)
                with c2:
                    show_calculated("Service match", f"{row.service_match_score:.0f}/100", "service_match", scope)
                with c3:
                    show_calculated("Evidence confidence", f"{row.service_confidence_score:.0f}/100 · {row.service_confidence_tier}",
                                    "evidence_confidence", scope)
                if not nearest_only:
                    with metrics[3]:
                        show_calculated("Ranking score", f"{row.score:.1f}", "ranking_score", scope)
                st.caption(row.service_evidence_label)
                evidence_col, branch_col = st.columns(2)
                with evidence_col:
                    show_calculated("Service evidence points", f"{row.service_confidence_points:g}/80",
                                    "service_evidence_points", scope, compact=True)
                with branch_col:
                    show_calculated("Branch identity points", f"{row.branch_confidence_points:g}/20",
                                    "branch_identity_points", scope, compact=True)
                if using_routes:
                    show_calculated("Straight-line reference", f"{row.distance_km:.1f} km",
                                    "straight_line_distance", scope, compact=True)
                region_pop = row.get("regional_population")
                if pd.notna(region_pop):
                    st.caption(f"Population region: {row.get('nfhs_district', 'unknown')}, {row.get('nfhs_state', 'unknown')} · {row.get('population_source', 'source not recorded')}")
                    district_col, allocation_col = st.columns(2)
                    with district_col:
                        show_calculated("Modeled district population (2020)", f"{float(region_pop):,.0f}",
                                        "district_population", scope, compact=True)
                        show_calculated("Expected district cases in scenario", f"{row.regional_expected_cases:,.1f}",
                                        "district_cases", scope, compact=True)
                    with allocation_col:
                        show_calculated("Eligible district facility records", f"{int(row.regional_facility_count):,}",
                                        "allocation_count", scope, compact=True)
                        show_calculated("Scenario cases allocated to this facility", f"{row.simulated_load:,.1f}",
                                        "facility_cases", scope, compact=True)
                else:
                    show_calculated("Modeled district population (2020)", "Unavailable or ambiguous",
                                    "district_population", scope, compact=True)
                load_col, factor_col = st.columns(2)
                with load_col:
                    show_calculated("Bed proxy used for load calculation", f"{row.capacity_basis_beds:,.0f} beds",
                                    "bed_proxy", scope, compact=True)
                    show_calculated("Allocated load per proxy bed", f"{row.load_ratio:.3f}",
                                    "load_ratio", scope, compact=True)
                if pd.notna(row.get("regional_demand_factor")):
                    basis = row.get(f"nfhs_burden_basis_{re.sub(r'[^a-z0-9]+', '_', service.casefold()).strip('_')}", "")
                    with factor_col:
                        show_calculated("Relative regional demand factor", f"{row.regional_demand_factor:.2f}",
                                        "regional_demand_factor", scope, compact=True)
                        if basis:
                            st.caption(f"NFHS basis: {basis}")
                st.write(f"**Location:** {row.get('city', '')}, {row.get('district', '')}, {row.get('state', '')} {row.get('pin', '')}")
                st.write(f"**Matched service text:** {row.matched_term}")
                st.write(f"**Evidence excerpt:** {row.supporting_excerpt}")
                for i, citation in enumerate(source_urls(row.service_evidence_sources_json), 1):
                    st.markdown(f"[Open reviewed service source {i}]({citation['url']})")
                    if i > 1:
                        st.caption(citation["excerpt"])
                geography_status = row.get("region_match_status")
                if pd.notna(geography_status) and geography_status != "matched_adm2":
                    show_calculated("Geography review flag", geography_status, "geography_status", scope, compact=True)
                if str(row.capacity_release_status).casefold() == "xgboost_estimate" and pd.notna(row.beds_for_scenario):
                    show_calculated("Estimated bed capacity", f"{row.beds_for_scenario:g} beds",
                                    "model_capacity", scope, compact=True)
                    lower, upper = row.get("capacity_prediction_lower"), row.get("capacity_prediction_upper")
                    if pd.notna(lower) and pd.notna(upper):
                        show_calculated("Bed prediction interval", f"{float(lower):g}–{float(upper):g} beds",
                                        "capacity_interval", scope, compact=True)
                elif pd.notna(row.beds_for_scenario):
                    show_calculated("Released bed capacity", f"{row.beds_for_scenario:g} beds",
                                    "release_capacity", scope, compact=True)
                    st.caption(f"{row.capacity_release_status} · {row.capacity_release_source or 'source not specified'}")
                else:
                    show_calculated("Bed capacity", f"Unavailable or withheld ({row.capacity_release_status})",
                                    "capacity_unavailable", scope, compact=True)
                if row.capacity_crosscheck_status not in {"match", "both blank"}:
                    show_calculated("Capacity cross-check", row.capacity_crosscheck_status,
                                    "capacity_crosscheck", scope, compact=True)
                phone = str(row.get("phone", "")).strip()
                st.write(f"**Phone:** {phone if phone and phone.casefold() not in {'nan', 'none', '<na>'} else 'Not listed in the available records.'}")
                phone_source_url = str(row.get("phone_source_url", "")).strip()
                if phone_source_url.startswith(("https://", "http://")):
                    st.markdown(f"[Open phone source]({phone_source_url})")
                website_url = str(row.get("website_url", "")).strip()
                if website_url.startswith(("https://", "http://")):
                    st.markdown(f"[Visit facility website]({website_url})")
                else:
                    st.caption("Website: not listed in the available records.")
                capacity_url = str(row.get("capacity_release_source_url", "")).strip()
                if capacity_url.startswith(("https://", "http://")):
                    st.markdown(f"[Open capacity source]({capacity_url})")
                urls = source_urls(row.source_urls_json)
                for i, url in enumerate(urls, 1):
                    st.markdown(f"[Open facility record source {i}]({url})")
                st.caption("Text evidence does not prove the service is operating now. Call the facility to confirm the branch, service, and current availability.")

with st.expander("How this prototype decides"):
    st.markdown(
        "Search includes branches with supporting evidence for the selected service and usable locations. "
        "An explicit service conflict or a documented location hold excludes a branch. "
        "Use **More info** beside each calculated value to see its method and sources."
    )
    st.markdown(f"[Read the prototype decisions and methodology]({REPOSITORY_DOCUMENTS}first-prototype/DECISIONS.md)")
