"""Streamlit user interface for the Databricks first prototype."""
from __future__ import annotations

import json
import os
import re

import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

from pipeline import SERVICE_TERMS, rank_candidates, resolve_location

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
                        help="Emergency ranking is nearest-first and ignores the simulated load adjustment.")
    population_share_pct = st.slider("Regional population needing this care", 0.0, 2.0, 1.0, 0.1, format="%.1f%%",
                                     help="Scenario: this share of each hospital district's modeled population needs this service. Expected cases are split evenly across evidence-backed hospitals in that district; this is not a forecast.")
    top_k = st.slider("Options to show", 1, 10, 5)
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
        ranked = rank_candidates(facilities, service, origin, severity=severity, population_share_pct=population_share_pct, top_k=top_k)
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    st.subheader(f"Options for {service}")
    st.caption(f"Origin: {origin[0]:.5f}, {origin[1]:.5f} · {origin_basis} · distance is straight-line, not road travel time")
    if ranked.empty:
        st.warning("No facility in this snapshot passed the text match, source-link, and coordinate checks. This does not establish that care is unavailable.")
    else:
        if severity == "emergency":
            st.info(f"Emergency ranking is nearest-first and ignores the {population_share_pct:.1f}% demand scenario. Routine/urgent score = straight-line km + a heuristic load penalty. Bed counts are only coarse facility-size proxies.")
        else:
            st.info(f"Scenario: {population_share_pct:.1f}% of each district's modeled population needs {service.lower()}. That district's expected cases are divided equally across all citable, coordinate-valid facilities in this snapshot that mention the service. Score = straight-line km + heuristic load penalty. The scenario is illustrative, not an incidence estimate or forecast.")
        for rank, row in ranked.iterrows():
            title = f"{rank + 1}. {row.facility_name} · {row.distance_km:.1f} km"
            with st.expander(title, expanded=(rank == 0)):
                c1, c2, c3 = st.columns(3)
                c1.metric("Distance", f"{row.distance_km:.1f} km")
                c2.metric("Score", f"{row.score:.2f}")
                c3.metric("Load stress penalty", f"{row.load_penalty:.2f} km-equivalent")
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
                if row.beds_provenance == "reported":
                    st.caption(f"Capacity input: {row.beds_for_scenario:g} reported beds (not current availability).")
                elif str(row.beds_provenance).startswith("model:"):
                    st.caption(f"Capacity input: about {row.beds_for_scenario:g} estimated beds (model estimate, not a count or current availability).")
                else:
                    st.caption("Capacity input missing; load calculation used a 50-bed fallback and is especially uncertain.")
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
    5. For routine and urgent searches, allocated cases divided by total bed proxy is multiplied by the NFHS relative demand proxy and converted to a capped 0–12 km-equivalent penalty: 12 × (1 − exp(−load ratio × NFHS factor)). Missing population estimates receive no load adjustment; missing bed counts use a disclosed 50-bed fallback. Emergency searches remain nearest-first.
    6. The score is a prototype heuristic, not a probability, clinical recommendation, route time, or live capacity signal. No suitable citable result means the app abstains.
    """)
