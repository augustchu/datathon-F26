"""Streamlit user interface for the Databricks first prototype."""
from __future__ import annotations

import json
import os
import re

import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

from pipeline import SERVICE_TERMS, rank_candidates, resolve_location, service_candidates
from traveltime_adapter import travel_time_matrix

st.set_page_config(page_title="Find the right care", page_icon="🏥", layout="wide")
FACILITY_TABLE = os.getenv("FACILITY_SNAPSHOT_TABLE", "additional_india_data.default.first_prototype_facilities")
SNAPSHOT_TABLE = os.getenv("SNAPSHOT_METADATA_TABLE", "additional_india_data.default.first_prototype_snapshot_metadata")


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
                        help="Emergency ranking skips the regional demand load adjustment, but still accounts for data confidence.")
    population_share_pct = st.slider("Regional population needing this care", 0.0, 2.0, 1.0, 0.1, format="%.1f%%",
                                     help="Scenario: this share of each hospital district's modeled population needs this service. Expected cases are split evenly across evidence-backed hospitals in that district; this is not a forecast.")
    top_k = st.slider("Options to show", 1, 10, 5)
    use_traveltime = st.toggle("Use TravelTime driving estimates", value=True,
                              help="When credentials and routing work, ranks by weekday-morning driving minutes. If unavailable, the app automatically uses its straight-line kilometer score.")
    coordinate_mode = st.toggle("Enter coordinates", value=False)
    if coordinate_mode:
        lat = st.number_input("Latitude", min_value=6.0, max_value=38.0, value=19.0760, format="%.5f")
        lon = st.number_input("Longitude", min_value=68.0, max_value=98.0, value=72.8777, format="%.5f")
        location_text = ""
    else:
        location_text = st.text_input("City, district, or PIN", value="Jaipur, Rajasthan", placeholder="e.g. Jaipur, Rajasthan or 302001")
        lat = lon = None

st.caption(f"Prepared snapshot: {snapshot_date} · {len(facilities):,} facility records")
st.warning("A bed count is only a rough facility-size proxy. It does not mean beds are staffed, free, or available for this service. Confirm service and availability with the facility before travel.")

if st.button("Find facility options", type="primary"):
    st.session_state["search_started"] = True

if st.session_state.get("search_started", False):
    try:
        origin, origin_basis = resolve_location(facilities, location_text, lat, lon)
        travel_times = None
        travel_status = "Straight-line distance mode"
        if use_traveltime:
            candidates = service_candidates(facilities, service)
            destination_records = tuple(
                (str(r.facility_id), float(r.latitude), float(r.longitude))
                for r in candidates[["facility_id", "latitude", "longitude"]].itertuples(index=False)
            )
            try:
                travel_times = cached_travel_times(tuple(origin), destination_records)
                reachable = sum(value is not None for value in travel_times.values())
                if reachable:
                    travel_status = f"TravelTime driving estimates ({reachable:,} reachable within 3 hours)"
                else:
                    travel_times = None
                    travel_status = "Straight-line fallback: TravelTime returned no reachable destinations"
                    st.warning("TravelTime returned no reachable destinations within its 3-hour limit. Showing the straight-line kilometer ranking instead.")
            except Exception as exc:
                travel_times = None
                travel_status = "Straight-line fallback: TravelTime unavailable"
                st.warning(f"TravelTime could not be used ({type(exc).__name__}); showing the straight-line kilometer ranking.")
        ranked = rank_candidates(facilities, service, origin, severity=severity, population_share_pct=population_share_pct, top_k=top_k, travel_times=travel_times)
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    st.subheader(f"Options for {service}")
    using_routes = travel_times is not None and not ranked.empty
    st.caption(f"Origin: {origin[0]:.5f}, {origin[1]:.5f} · {origin_basis} · {travel_status}")
    if ranked.empty:
        if travel_times is not None:
            st.warning("No evidence-backed facility was returned as reachable within TravelTime's 3-hour search limit. This does not establish that care is unavailable.")
        else:
            st.warning("No facility in this snapshot passed the text match, source-link, and coordinate checks. This does not establish that care is unavailable.")
    else:
        if severity == "emergency":
            st.info(f"Emergency ranking skips the {population_share_pct:.1f}% demand load adjustment. All rankings include a data-confidence penalty. Score = {'route minutes' if using_routes else 'straight-line km'} + confidence adjustment.")
        else:
            st.info(f"Scenario: {population_share_pct:.1f}% of each district's modeled population needs {service.lower()}. That district's expected cases are divided equally across all citable, coordinate-valid facilities in this snapshot that mention the service. Score = {'route minutes' if using_routes else 'straight-line km'} + load penalty + confidence adjustment. In TravelTime mode, each km-equivalent penalty is converted at 2 minutes per km (1 minute ≈ 0.5 km). Confidence is a data-quality score, not a probability the hospital is good or currently available.")
        for rank, row in ranked.iterrows():
            distance_label = f"{row.travel_time_minutes:.0f} min drive" if using_routes else f"{row.distance_km:.1f} km straight-line"
            unit = "minutes" if using_routes else "km-equivalent"
            title = f"{rank + 1}. {row.facility_name} · {distance_label}"
            with st.expander(title, expanded=(rank == 0)):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("TravelTime drive" if using_routes else "Straight-line distance", f"{row.travel_time_minutes:.0f} min" if using_routes else f"{row.distance_km:.1f} km")
                c2.metric("Adjusted score", f"{row.score:.1f} {unit}")
                confidence_score = row.get("confidence_score")
                confidence_label = (f"{float(confidence_score):.0f}/100 · {row.confidence_tier}"
                                    if pd.notna(confidence_score) else "Unknown")
                c3.metric("Data confidence", confidence_label)
                c4.metric("Confidence adjustment", f"+{row.confidence_penalty:.0f} {unit}")
                st.write(f"**Regional-demand load penalty:** +{row.load_penalty:.1f} {unit}")
                st.caption(f"Scoring base: {row.routing_basis}; straight-line reference: {row.distance_km:.1f} km.")
                region_pop = row.get("regional_population")
                if pd.notna(region_pop):
                    st.write(f"**Modeled district population (2020):** {float(region_pop):,.0f}")
                    st.caption(f"Population region: {row.get('nfhs_district', 'unknown')}, {row.get('nfhs_state', 'unknown')} · {row.get('population_source', 'source not recorded')}")
                    st.write(f"**Expected district cases in scenario:** {float(row.regional_expected_cases):,.1f} total; **allocated to this facility:** {float(row.simulated_load):,.1f} across {int(row.regional_facility_count)} evidence-backed facilities")
                else:
                    st.write("**Regional population estimate:** unavailable or ambiguous; no demand-based penalty is applied.")
                st.write(f"**Bed proxy used for load calculation:** {float(row.capacity_basis_beds):,.0f} beds")
                st.write(f"**Allocated load / bed proxy:** {float(row.load_ratio):.3f}")
                if row.get("regional_demand_factor") is not None:
                    st.write(f"**Relative regional demand factor:** {float(row.regional_demand_factor):.2f} (NFHS proxy; 1.00 is neutral)")
                    basis = row.get(f"nfhs_burden_basis_{re.sub(r'[^a-z0-9]+', '_', service.casefold()).strip('_')}", "")
                    if basis:
                        st.caption(f"NFHS basis: {basis}")
                st.write(f"**Location:** {row.get('city', '')}, {row.get('district', '')}, {row.get('state', '')} {row.get('pin', '')}")
                st.write(f"**Matched service text:** {row.matched_term}")
                st.write(f"**Evidence excerpt:** {row.supporting_excerpt}")
                if row.get("region_match_status") not in (None, "matched_adm2"):
                    st.caption(f"Geography review flag: {row.get('region_match_status')}. The recommendation distance still uses the source coordinates.")
                if str(row.capacity_release_status).casefold() == "xgboost_estimate" and pd.notna(row.beds_for_scenario):
                    lower, upper = row.get("capacity_prediction_lower"), row.get("capacity_prediction_upper")
                    interval = f" Estimated interval: {float(lower):g}–{float(upper):g} beds." if pd.notna(lower) and pd.notna(upper) else ""
                    st.caption(f"This is an estimated capacity: {row.beds_for_scenario:g} beds.{interval} It is not current availability.")
                elif pd.notna(row.beds_for_scenario):
                    st.caption(f"Capacity value: {row.beds_for_scenario:g} beds ({row.capacity_release_status}; {row.capacity_release_source or 'source not specified'}). This is not current availability.")
                else:
                    st.caption(f"Capacity unavailable or withheld ({row.capacity_release_status}); the load calculation used a 50-bed fallback and is especially uncertain.")
                if row.capacity_crosscheck_status not in {"match", "both blank"}:
                    st.caption(f"Capacity cross-check against the Marketplace field: {row.capacity_crosscheck_status}. The release value above is used for calculations.")
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
                    st.markdown(f"[Open facility source {i}]({url})")
                st.caption("Text evidence does not prove the service is operating now. Call the facility to confirm the branch, service, and current availability.")

with st.expander("How this prototype decides"):
    st.markdown("""
    1. It searches facility capability, procedure, specialty, equipment, and description text for a reviewed service alias.
    2. It requires a source URL and usable India coordinates before a record can be recommended.
    3. It removes straightforward nearby negation matches and preserves the matched excerpt for inspection.
    4. It estimates district demand as modeled 2020 district population × the selected percentage (0–2%). It divides that demand equally among every citable, coordinate-valid facility record for the selected service in that district. This is a scenario allocation, not a forecast.
    5. When TravelTime is available, the score starts with its weekday-morning driving estimate in minutes. Otherwise it starts with straight-line kilometers. Routine and urgent searches add the demand-load adjustment; emergency searches skip it.
    6. Load and confidence adjustments are defined in km-equivalent units. TravelTime mode converts them at 2 minutes per km (1 minute ≈ 0.5 km); straight-line mode uses the original kilometer values. Confidence tiers are high (50–100/100) +0 km-equivalent, medium (25–49) +40, low (0–24) +80, and unknown +80. These are explicit prototype rules, not clinical validation.
    7. Capacity uses the release value when present. `xgboost_estimate` is shown with the note “This is an estimated capacity.” The release's original capacity is cross-checked against the Marketplace field; mismatches are surfaced, while the released value remains the calculation input. Phone and website come from the release when available, then fall back to Marketplace fields.
    8. TravelTime results are provider driving estimates, not live traffic. A route unavailable within the provider's three-hour search window is omitted. The score is a prototype heuristic, not a clinical recommendation or live capacity signal. No suitable citable result means the app abstains.
    """)
