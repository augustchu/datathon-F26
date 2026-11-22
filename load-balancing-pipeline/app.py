"""Streamlit demo for evidence-based facility recommendations and load simulation."""
from pathlib import Path
import re
import os

import geopandas as gpd
import pandas as pd
import streamlit as st

from care_engine import rank_facilities
from facility_mapping import normalize_facilities, assign_facility_regions, build_region_geometries
from issue_catalog import ISSUE_CATALOG
from simulation import run_issue_scenario
from traveltime_adapter import travel_time_matrix, straight_line_minutes

st.set_page_config(page_title="Care Finder | India", layout="wide")

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
POLYGONS = OUTPUTS / "geoBoundaries-IND-ADM2.geojson"
CROSSWALK = OUTPUTS / "resolved_polygon_crosswalk.csv"
POPULATION = OUTPUTS / "nfhs_district_population_2020.csv"
NFHS = ROOT / "NFHS-5 Dataset.csv"
FACILITIES = ROOT / "facilities dataset.csv"


def _key(value):
    if pd.isna(value):
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


@st.cache_data(show_spinner="Loading approved region crosswalk and population...")
def load_regions():
    pop = pd.read_csv(POPULATION)
    health = pd.read_csv(NFHS)
    pop["_join_key"] = pop.district_name.map(_key) + "::" + pop.state_ut.map(_key)
    health["_join_key"] = health.district_name.map(_key) + "::" + health.state_ut.map(_key)
    health = health.drop_duplicates("_join_key")
    merged = pop.merge(health.drop(columns=["district_name", "state_ut"]),
                       on="_join_key", how="left", suffixes=("", "_nfhs"))
    return merged


def _travel_time_with_config(origin, destinations):
    """Read credentials from env/Streamlit secrets; never put them in source."""
    app_id = os.getenv("TRAVELTIME_APP_ID")
    api_key = os.getenv("TRAVELTIME_API_KEY")
    try:
        app_id = app_id or st.secrets.get("TRAVELTIME_APP_ID")
        api_key = api_key or st.secrets.get("TRAVELTIME_API_KEY")
    except Exception:
        # A missing Streamlit secrets.toml is normal for local development.
        pass
    return travel_time_matrix((origin[0], origin[1]), destinations,
                              app_id=app_id, api_key=api_key)


st.title("Find care, with evidence")
st.caption("A prototype using facility claims, reviewed region links, NFHS-5 indicators, and modeled population. Verify urgent care directly with local emergency services.")

if not all(path.exists() for path in [POLYGONS, CROSSWALK, POPULATION, NFHS]):
    st.error("Required region outputs are missing. Run the population pipeline first.")
    st.stop()

with st.sidebar:
    st.header("Facility data")
    upload = st.file_uploader("Optional facility CSV override", type=["csv"])
    st.caption("By default, the app uses facilities dataset.csv in this project folder. An uploaded file overrides it for this session.")

if upload is None and not FACILITIES.exists():
    st.error("No facility CSV was uploaded and facilities dataset.csv was not found.")
    st.stop()

raw = pd.read_csv(upload) if upload is not None else pd.read_csv(FACILITIES, low_memory=False)
columns = list(raw.columns)
if not columns:
    st.error("The uploaded CSV has no columns.")
    st.stop()

with st.sidebar:
    st.subheader("Map facility columns")
    def choose(label, required=True, preferred=()):
        options = ( ["(none)"] if not required else [] ) + columns
        default = next((columns.index(x) + (0 if required else 1) for x in preferred if x in columns), 0)
        return st.selectbox(label, options, index=default)
    name_col = choose("Facility name", preferred=("facility_name", "name", "hospital_name"))
    lat_col = choose("Latitude", preferred=("latitude", "lat"))
    lon_col = choose("Longitude", preferred=("longitude", "lon", "lng"))
    service_cols = st.multiselect("Service/evidence text columns", columns,
                                  default=[c for c in columns if any(
                                      term in c.lower() for term in
                                      ("capability", "procedure", "specialty", "equipment", "description"))])
    capacity_col = choose("Capacity column (optional)", required=False,
                          preferred=("capacity", "bed_capacity", "number_of_beds"))
    source_col = choose("Source URL column (optional)", required=False,
                        preferred=("source_urls", "source_url", "url", "website"))
    facility_type_col = choose("Facility type column (optional)", required=False,
                                preferred=("facilityTypeId", "facility_type", "type", "category"))
    id_col = choose("Facility ID column (optional)", required=False,
                    preferred=("unique_id", "facility_id", "id"))
    city_col = choose("City column (optional)", required=False,
                      preferred=("address_city", "city"))
    state_col = choose("Address state column (optional)", required=False,
                       preferred=("address_stateOrRegion", "state", "state_name"))
    postcode_col = choose("Postcode column (optional)", required=False,
                          preferred=("address_zipOrPostcode", "postcode", "pincode"))

if "(none)" in (name_col, lat_col, lon_col) or not service_cols:
    st.warning("Select name, latitude, longitude, and at least one service/evidence column in the sidebar.")
    st.stop()

facilities = normalize_facilities(raw, name_col, lat_col, lon_col, service_cols,
                                  None if capacity_col == "(none)" else capacity_col,
                                  None if source_col == "(none)" else source_col,
                                  None if facility_type_col == "(none)" else facility_type_col,
                                  None if id_col == "(none)" else id_col,
                                  None if city_col == "(none)" else city_col,
                                  None if state_col == "(none)" else state_col,
                                  None if postcode_col == "(none)" else postcode_col)
facilities = assign_facility_regions(facilities, POLYGONS, CROSSWALK)
state_aliases = {"nctofdelhi": "delhi", "nationalcapitalterritoryofdelhi": "delhi",
                 "orissa": "odisha", "uttaranchal": "uttarakhand",
                 "jammukashmir": "jammuandkashmir", "pondicherry": "puducherry"}
facilities["address_state_key"] = facilities.address_state.map(_key).replace(state_aliases)
facilities["assigned_state_key"] = facilities.nfhs_state.map(_key).replace(state_aliases)
has_address_state = facilities.address_state_key.ne("") & facilities.assigned_state_key.notna()
facilities["address_state_conflict"] = has_address_state & (
    facilities.address_state_key != facilities.assigned_state_key
)
regions = load_regions()
geometries = build_region_geometries(POLYGONS, CROSSWALK)

mapped, unmapped, conflicts, missing_capacity, duplicate_ids = st.columns(5)
mapped.metric("Facilities assigned to resolved regions", int((facilities.region_match_status == "matched").sum()))
unmapped.metric("Unmapped or ambiguous", int((facilities.region_match_status != "matched").sum()))
conflicts.metric("Address/state conflicts", int(facilities.address_state_conflict.sum()))
missing_capacity.metric("Capacity missing", int(facilities.capacity_units.isna().sum()))
has_source_id = facilities.source_facility_id.ne("")
duplicate_ids.metric("Rows with repeated IDs", int((has_source_id & facilities.source_facility_id.duplicated(keep=False)).sum()))
with st.expander("Review facility-to-region assignments"):
    st.dataframe(facilities[["facility_name", "address_city", "address_state", "address_postcode",
                             "latitude", "longitude", "nfhs_district_name", "nfhs_state",
                             "region_match_status", "address_state_conflict", "capacity_units"]],
                 use_container_width=True)

tab_recommend, tab_sim = st.tabs(["Find a facility", "Simulate concurrent demand"])
with tab_recommend:
    st.subheader("Patient location and care need")
    c1, c2, c3 = st.columns(3)
    lat = c1.number_input("Latitude", value=19.0760, format="%.5f")
    lon = c2.number_input("Longitude", value=72.8777, format="%.5f")
    issue_id = c3.selectbox("Medical issue", list(ISSUE_CATALOG),
                            format_func=lambda key: ISSUE_CATALOG[key]["label"])
    service = ISSUE_CATALOG[issue_id]["service_key"]
    severity = st.radio("Severity", ["emergency", "urgent", "routine"], horizontal=True)
    st.caption("TravelTime Fast uses a typical weekday-morning driving estimate, not live traffic. This is a decision-support prototype, not a diagnosis or a live bed-availability feed.")
    if st.button("Find options", type="primary"):
        evidence_matches = rank_facilities(facilities, service, severity, travel={})
        candidate_ids = {row["facility_id"] for row in evidence_matches}
        matching = facilities[facilities.facility_id.isin(candidate_ids)].copy()
        if not len(matching):
            st.warning("No facility passed the service evidence threshold from the uploaded text.")
            st.stop()
        candidates = matching.dropna(subset=["latitude", "longitude"]).copy()
        candidates = candidates[candidates.latitude.between(-90, 90) & candidates.longitude.between(-180, 180)]
        if not len(candidates):
            st.warning("Text evidence matched, but none of those records has usable coordinates for travel-time routing.")
            st.stop()
        try:
            travel, provider = _travel_time_with_config((lat, lon), candidates)
        except Exception as exc:
            st.error(f"TravelTime could not calculate routes: {exc}")
            st.info("Configure both TRAVELTIME_APP_ID and TRAVELTIME_API_KEY in the environment or Streamlit secrets.")
            st.stop()
        ranked = rank_facilities(candidates, service, severity, travel)
        ranked = [row for row in ranked if row.get("travel_time_minutes") is not None]
        st.caption(f"Travel basis: {provider}. One API request evaluated {len(candidates):,} evidence-matching facilities with coordinates; results are limited to routes within 3 hours.")
        if not ranked:
            st.warning("No evidence-matching facility was reachable within the API's 3-hour search limit. This is not proof that no suitable care exists.")
        for rank, item in enumerate(ranked[:10], start=1):
            title = f"{rank}. {item['facility_name']} - {item['travel_time_minutes']:.0f} min" if item.get("travel_time_minutes") is not None else f"{rank}. {item['facility_name']} - travel time unavailable"
            with st.expander(title, expanded=(rank == 1)):
                st.write(f"**Region:** {item.get('nfhs_district_name', 'Unmapped')}, {item.get('nfhs_state', '')}")
                st.write(f"**Why included:** matched text: {item['matched_terms']}; rules-based evidence strength: {item['service_confidence']:.0%}.")
                st.write(f"**How ranked:** score {item['score']:.1f} (lower is better); severity = {severity}; capacity/load pressure is {'ignored for emergency priority' if severity == 'emergency' else 'included only when capacity is supplied'}.")
                st.code(item["supporting_text"] or "No supporting service text", language=None)
                urls = item.get("source_urls", [])
                if isinstance(urls, str):
                    urls = [urls] if urls else []
                for source_number, source_url in enumerate(urls[:5], start=1):
                    st.markdown(f"[Open facility source {source_number}]({source_url})")
                if item.get("capacity_units") is None:
                    st.caption("Capacity not supplied; no load penalty was applied.")

with tab_sim:
    st.subheader("Synthetic simultaneous cases")
    st.warning("NFHS-5 measures survey indicators, not disease incidence. These are relative demand proxies used to vary synthetic case mix, not forecasts of real patients.")
    s1, s2, s3 = st.columns(3)
    sim_issue_id = s1.selectbox("Medical issue", list(ISSUE_CATALOG),
                                format_func=lambda key: ISSUE_CATALOG[key]["label"], key="sim_issue")
    case_count = s2.slider("Issue intensity (synthetic cases)", 0, 300, 60, 1)
    seed = s3.number_input("Random seed", min_value=0, value=7, step=1)
    sim_travel = st.selectbox("Simulation travel method", ["Straight-line proxy", "TravelTime API"],
                              help="The API option makes one matrix request per synthetic case; the proxy is cheaper for scenario runs.")
    if st.button("Run simulation"):
        usable = regions[(regions.district_population_status == "available") & regions.pop_2020.notna()].copy()
        provider = (lambda origin, rows: (straight_line_minutes(origin, rows), "straight-line speed proxy")) \
            if sim_travel == "Straight-line proxy" else _travel_time_with_config
        cases, result, load = run_issue_scenario(
            usable, geometries, facilities, sim_issue_id, int(case_count), provider, int(seed)
        )
        st.write("Assignment logic: cases are assigned in generated order; routine/urgent cases receive a soft quadratic load penalty when capacity is known. Emergency cases rank by fastest capable facility and ignore simulated load.")
        st.dataframe(result, use_container_width=True)
        st.subheader("Simulated distribution")
        st.dataframe(load.merge(facilities[["facility_id", "facility_name", "capacity_units"]], on="facility_id", how="left"), use_container_width=True)
        if not result.empty:
            st.download_button("Download case assignments", result.to_csv(index=False), "simulation_assignments.csv", "text/csv")
