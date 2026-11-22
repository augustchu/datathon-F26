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
FACILITY_TABLE = os.getenv("FACILITY_SNAPSHOT_TABLE", "main.first_prototype.facilities")
SNAPSHOT_TABLE = os.getenv("SNAPSHOT_METADATA_TABLE", "main.first_prototype.snapshot_metadata")


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
    intensity = st.slider("Concurrent cases to simulate", 0, 300, 60, 1,
                          help="Number of hypothetical cases routed before this patient. This is not a measurement or forecast of real demand.")
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
        ranked = rank_candidates(facilities, service, origin, severity=severity, intensity=intensity, top_k=top_k)
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    st.subheader(f"Options for {service}")
    st.caption(f"Origin: {origin[0]:.5f}, {origin[1]:.5f} · {origin_basis} · distance is straight-line, not road travel time")
    if ranked.empty:
        st.warning("No facility in this snapshot passed the text match, source-link, and coordinate checks. This does not establish that care is unavailable.")
    else:
        if severity == "emergency":
            st.info(f"Emergency ranking is nearest-first and ignores the {intensity} case scenario. Routine/urgent score = straight-line km + simulated-load penalty. Bed counts are only coarse facility-size proxies.")
        else:
            st.info(f"Ranking: {intensity} hypothetical routine cases were assigned sequentially across evidence-matching facilities, then the user's case was ranked. Score = straight-line km + simulated-load penalty. Where a reviewed NFHS field is mapped, its clipped relative regional proxy scales simulated pressure; missing/unsupported indicators use a neutral factor. Bed counts are only coarse facility-size proxies.")
        for rank, row in ranked.iterrows():
            title = f"{rank + 1}. {row.facility_name} · {row.distance_km:.1f} km"
            with st.expander(title, expanded=(rank == 0)):
                c1, c2, c3 = st.columns(3)
                c1.metric("Distance", f"{row.distance_km:.1f} km")
                c2.metric("Score", f"{row.score:.2f}")
                c3.metric("Simulated-load penalty", f"{row.load_penalty:.2f}")
                st.write(f"**Hypothetical prior cases assigned here:** {row.simulated_load:.0f} of {intensity}")
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
                    st.caption("Capacity input missing; simulated-load penalty used the 50-bed fallback and is especially uncertain.")
                urls = source_urls(row.source_urls_json)
                for i, url in enumerate(urls, 1):
                    st.markdown(f"[Open facility source {i}]({url})")
                st.caption("Text evidence does not prove the service is operating now. Call the facility to confirm the branch, service, and current availability.")

with st.expander("How this prototype decides"):
    st.markdown("""
    1. It searches facility capability, procedure, specialty, equipment, and description text for a reviewed service alias.
    2. It requires a source URL and usable India coordinates before a record can be recommended.
    3. It removes straightforward nearby negation matches and preserves the matched excerpt for inspection.
    4. It first routes the selected number of hypothetical routine cases sequentially, then ranks the user's case. For urgent and routine searches, accumulated scenario load adds a soft penalty scaled by total bed count; emergency searches remain nearest-first.
    5. The score is a prototype heuristic, not a probability, clinical recommendation, route time, or live capacity signal. No suitable citable result means the app abstains.
    """)
