"""Streamlit user interface for the Databricks first prototype."""
from __future__ import annotations

import json
from math import isfinite
import os
from urllib.parse import quote

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as component_html
from databricks import sql
from databricks.sdk.core import Config

from facility_map import build_map
from metric_explanations import EXPLANATIONS
from pipeline import SERVICE_TERMS, rank_candidates, resolve_location, service_candidates
from projected_load import TIME_CRITICAL_SERVICES
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


def set_search_started(started):
    st.session_state["search_started"] = started


def show_facility_map(origin=None, ranked=None, *, origin_label="", using_routes=False):
    st.subheader("Map")
    markup = build_map(origin, ranked, origin_label=origin_label, using_routes=using_routes)
    height = 560 if ranked is not None and not ranked.empty else 400
    if hasattr(st, "iframe"):
        st.iframe(markup, height=height)
    else:
        component_html(markup, height=height)
    if ranked is not None and not ranked.empty:
        st.caption("Pin numbers match the result cards. The red pin marks the search origin.")


@st.cache_data(ttl=1800, max_entries=32, show_spinner="Getting driving times from TravelTime...")
def cached_travel_times(origin, facility_records):
    destinations = pd.DataFrame(facility_records, columns=["facility_id", "latitude", "longitude"])
    return travel_time_matrix(origin, destinations)


st.title("Find the right care")
st.caption("Evidence-linked facility options from the prepared Virtue Foundation snapshot. This prototype is not a diagnosis or live availability service.")

search_started = st.session_state.get("search_started", False)
if search_started:
    st.button("Back to search", on_click=set_search_started, args=(False,))
    search_controls = st.expander("Search options", expanded=False)
else:
    search_controls = st.container()

with search_controls:
    care_col, location_col = st.columns(2)
    with care_col:
        service = st.selectbox("Care needed", list(SERVICE_TERMS), key="search_service")
        severity = st.radio("Urgency", ["routine", "urgent", "emergency"], horizontal=True, key="search_urgency",
                            help="Projected load balancing applies only to routine, eligible services.")
        load_allowed = severity == "routine" and service not in TIME_CRITICAL_SERVICES
        balance_load = st.toggle("Consider projected load", value=False, disabled=not load_allowed,
                                 help="Compare published monthly caseload and throughput projections for nearby options.")
        top_k = st.slider("Options to show", 1, 10, 5, key="search_result_count")
    with location_col:
        coordinate_mode = st.toggle("Enter coordinates", value=False, key="search_coordinate_mode")
        if coordinate_mode:
            lat = st.number_input("Latitude", min_value=6.0, max_value=38.0, value=19.0760,
                                  format="%.5f", key="search_latitude")
            lon = st.number_input("Longitude", min_value=68.0, max_value=98.0, value=72.8777,
                                  format="%.5f", key="search_longitude")
            location_text = ""
        else:
            location_text = st.text_input("City, district, or PIN", value="Jaipur, Rajasthan",
                                          placeholder="e.g. Jaipur, Rajasthan or 302001", key="search_place")
            lat = lon = None
        use_traveltime = st.toggle("Use TravelTime driving estimates", value=True, key="search_use_traveltime",
                                  help="Uses weekday-morning driving minutes as the travel input when routes are available.")

if not search_started:
    st.button("Find facility options", type="primary", on_click=set_search_started, args=(True,))
    show_facility_map()
    st.stop()

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

st.caption(f"Prepared snapshot · {snapshot_date}")
show_calculated("Facility records in snapshot", f"{len(facilities):,}", "snapshot_records", compact=True)
if "location_search_eligible" not in facilities:
    st.info("Refresh the prepared snapshot to enable service evidence and location screening.")
st.warning("Bed counts do not show how many beds are staffed, free, or available for this service. Confirm service and availability with the facility before travel.")

candidates = service_candidates(facilities, service)
if candidates.empty:
    st.subheader(f"Options for {service}")
    st.warning(f"No facilities with supporting evidence and a usable location were found for {service} in this snapshot.")
    try:
        map_origin, map_origin_basis = resolve_location(facilities, location_text, lat, lon)
    except ValueError:
        show_facility_map()
    else:
        show_facility_map(map_origin, origin_label=location_text or map_origin_basis)
else:
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
                                 top_k=top_k, travel_times=travel_times, nearest_only=nearest_only,
                                 balance_projected_load=load_allowed and balance_load)
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
        show_facility_map(origin, origin_label=location_text or origin_basis)
    else:
        if nearest_only:
            st.warning("TravelTime found no matching facilities it could route to within its three-hour search window. Showing the closest matches by straight-line distance.")
        comparable = ranked.attrs.get("projected_load_comparable", 0)
        order = "Closest by straight-line distance" if nearest_only else "Lowest ranking score first"
        if comparable:
            order = "Projected load preference, then ranking score"
        show_calculated("Result order", order, "result_order", compact=True)
        if not nearest_only:
            base = "driving minutes" if using_routes else "straight-line kilometers"
            st.info(f"The baseline ranking uses {base}, service match, and evidence confidence.")
        if load_allowed and balance_load:
            if comparable:
                st.caption(f"Compared projected monthly load for {comparable} nearby facilities with comparable service evidence.")
            else:
                st.caption("No comparable projected load could be used for this search. The ordinary order applies.")
        result_column, map_column = st.columns([1.25, 1], gap="medium")
        with map_column:
            show_facility_map(origin, ranked, origin_label=location_text or origin_basis, using_routes=using_routes)
        for rank, (_, row) in enumerate(ranked.iterrows(), 1):
            distance_label = f"{row.travel_time_minutes:.0f} min drive" if using_routes else f"{row.distance_km:.1f} km straight-line"
            scope = str(row.facility_id)
            title = f"{rank}. {row.facility_name} · {distance_label}"
            if row.service_evidence_kind in RECORD_TEXT_KINDS:
                title += f" · {row.service_evidence_label}"
            with result_column.expander(title, expanded=(rank == 1)):
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
                if pd.notna(row.get("projected_capacity")):
                    demand_label = "Projected demand" if str(row.projected_caseload_predicted).casefold() == "true" else "Historical demand"
                    st.write(f"**{demand_label}** · {row.projected_caseload:,.1f} {row.projected_unit}")
                    st.write(f"**Modeled throughput** · {row.projected_capacity:,.1f} {row.projected_unit}")
                    st.caption(f"{row.projected_scope} · {row.projected_period} · Planning estimate")
                    st.markdown(f"[Projection methods and data]({REPOSITORY_DOCUMENTS}non-ml/caseload/NETWORK_CASELOAD.md)")
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
        "The optional projected load comparison uses comparable monthly caseload and throughput for nearby routine-care options. "
        "Use **More info** beside each calculated value to see its method and sources."
    )
    st.markdown(f"[Read the prototype decisions and methodology]({REPOSITORY_DOCUMENTS}first-prototype/DECISIONS.md)")
