"""Facility Finder dashboard.

Inputs (service + location) -> mapped facility matches and explainable ranking.

Run from first-prototype: streamlit run classic_dashboard.py
Data: the prepared Databricks snapshot, synthetic demo, facilities export, or dashboard-schema CSV.
"""
import html
import os
import re
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from classic_data import (
    apply_projected_load, capacity_details, load_classic_snapshot, rank_classic_results,
    safe_url, service_evidence, snapshot_configured,
)
from classic_ranking import (
    RANKED_CRITERIA, RECOMMENDED, SEVERITIES, render_priorities, score_priorities, score_recommended,
)
from dashboard_equity import (
    adjust_demo_equity_coordinates, prepare_equity_results, render_equity_card,
    render_equity_control, render_equity_summary,
)
from projected_load import TIME_CRITICAL_SERVICES


st.set_page_config(page_title="Facility Finder", page_icon="🏥", layout="wide")
CALL_PROMPT = "Call this facility before traveling to verify the service, branch, and current availability."
SNAPSHOT_MODE = "Prepared Databricks snapshot"

st.markdown("""
<style>
/* Make facility cards more compact */
[data-testid="stVerticalBlock"] {
    gap: 0.55rem;
}

/* Metric labels */
[data-testid="stMetricLabel"] {
    font-size: 0.95rem !important;
    line-height: 1.2 !important;
}

/* Metric values */
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] > div {
    font-size: 1.8rem !important;
    line-height: 1.25 !important;
    font-weight: 500 !important;
    white-space: nowrap !important;
}

/* Keep metric columns aligned */
[data-testid="stHorizontalBlock"] {
    align-items: center;
}

/* Smaller hospital name */
[data-testid="stMarkdownContainer"] h3 {
    font-size: 1.35rem !important;
    line-height: 1.25 !important;
    margin-bottom: 0.2rem !important;
}

/* Smaller descriptive text */
[data-testid="stMarkdownContainer"] p {
    font-size: 0.88rem !important;
    line-height: 1.4 !important;
    margin-bottom: 0.35rem !important;
}

/* Smaller bullet points */
[data-testid="stMarkdownContainer"] li {
    font-size: 0.86rem !important;
    line-height: 1.35 !important;
    margin-bottom: 0.15rem !important;
}

/* Compact section headings */
[data-testid="stMarkdownContainer"] h4 {
    font-size: 1rem !important;
    margin-top: 0.4rem !important;
    margin-bottom: 0.25rem !important;
}

/* Reduce padding inside bordered facility cards */
[data-testid="stVerticalBlockBorderWrapper"] {
    padding: 0.7rem !important;
}
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 1. SERVICE TAXONOMY
# ----------------------------------------------------------------------------
SERVICES = {
    "Dialysis": dict(specialty="Nephrology", area="Kidneys", urgent=False,
                     equip=["hemodialysis machine", "RO water plant"], staff=["nephrologist", "dialysis technician"], related=[],
                     aliases=["dialysis", "haemodialysis", "hemodialysis", "peritoneal dialysis"]),
    "Emergency C-section": dict(specialty="Obstetrics & Gynecology", area="Pregnancy & childbirth", urgent=True,
                                equip=["operation theatre", "blood bank"], staff=["obstetrician", "anesthetist"],
                                related=["Maternity / delivery"],
                                aliases=["emergency c-section", "emergency c section", "emergency cesarean", "emergency caesarean"]),
    "Maternity / delivery": dict(specialty="Obstetrics & Gynecology", area="Pregnancy & childbirth", urgent=False,
                                 equip=["labour room", "operation theatre"], staff=["obstetrician"], related=[],
                                 aliases=["maternity", "obstetric", "normal delivery", "labour room", "gynecology", "gynaecology"]),
    "NICU": dict(specialty="Neonatology", area="Newborn", urgent=True,
                 equip=["incubator", "ventilator", "phototherapy unit"], staff=["neonatologist"], related=["Pediatric care"],
                 aliases=["nicu", "neonatal intensive care", "sncu", "neonatology"]),
    "Pediatric care": dict(specialty="Pediatrics", area="Children", urgent=False,
                           equip=["pediatric ward"], staff=["pediatrician"], related=[],
                           aliases=["pediatric", "paediatric", "pediatrics", "paediatrics"]),
    "Cardiac surgery": dict(specialty="Cardiology & Cardiac Surgery", area="Heart", urgent=True,
                            equip=["cath lab", "heart-lung machine", "ICU"], staff=["cardiac surgeon", "perfusionist"],
                            related=["Cardiology consultation"],
                            aliases=["cardiac surgery", "cardiothoracic", "coronary artery bypass", "cabg", "heart surgery"]),
    "Cardiology consultation": dict(specialty="Cardiology & Cardiac Surgery", area="Heart", urgent=False,
                                    equip=["ECG", "echocardiography"], staff=["cardiologist"], related=[],
                                    aliases=["cardiology", "cardiologist", "echocardiography"]),
    "MRI": dict(specialty="Radiology", area="Imaging (whole body)", urgent=False,
                equip=["MRI scanner"], staff=["radiologist", "MRI technologist"], related=["CT scan"],
                aliases=["mri", "magnetic resonance"]),
    "CT scan": dict(specialty="Radiology", area="Imaging (whole body)", urgent=False,
                    equip=["CT scanner"], staff=["radiologist"], related=[],
                    aliases=["ct scan", "ct scanner", "computed tomography"]),
    "Burn care": dict(specialty="Plastic & Burn Surgery", area="Skin", urgent=True,
                      equip=["burn unit", "operation theatre", "ICU"], staff=["plastic surgeon"], related=["Trauma / emergency"],
                      aliases=["burn care", "burn unit", "burns unit", "burn treatment"]),
    "Orthopedic surgery": dict(specialty="Orthopedics", area="Bones & joints", urgent=False,
                               equip=["operation theatre", "C-arm / X-ray"], staff=["orthopedic surgeon", "anesthetist"],
                               related=["Fracture care"],
                               aliases=["orthopedic", "orthopaedic", "joint replacement", "arthroplasty"]),
    "Fracture care": dict(specialty="Orthopedics", area="Bones & joints", urgent=True,
                          equip=["X-ray", "plaster room"], staff=["orthopedic surgeon"], related=["Trauma / emergency"],
                          aliases=["fracture"]),
    "Dental care": dict(specialty="Dentistry", area="Teeth & mouth", urgent=False,
                        equip=["dental chair", "dental X-ray"], staff=["dentist"], related=["Oral surgery"],
                        aliases=["dental", "dentist", "dentistry", "orthodontic", "endodontic"]),
    "Oral surgery": dict(specialty="Dentistry", area="Teeth & mouth", urgent=False,
                         equip=["operation theatre", "dental X-ray"], staff=["oral surgeon"], related=["Dental care"],
                         aliases=["oral surgery", "maxillofacial"]),
    "Cataract surgery": dict(specialty="Ophthalmology", area="Eyes", urgent=False,
                             equip=["operating microscope", "phaco machine"], staff=["ophthalmologist"], related=[],
                             aliases=["cataract", "phacoemulsification"]),
    "Stroke care": dict(specialty="Neurology", area="Brain & nerves", urgent=True,
                        equip=["CT scanner", "ICU", "thrombolysis protocol"], staff=["neurologist"],
                        related=["Trauma / emergency"], aliases=["stroke"]),
    "Chemotherapy": dict(specialty="Oncology", area="Cancer", urgent=False,
                         equip=["chemo day-care", "pharmacy"], staff=["oncologist"], related=[],
                         aliases=["chemotherapy", "oncology", "cancer treatment"]),
    "Trauma / emergency": dict(specialty="Emergency Medicine", area="Whole body", urgent=True,
                               equip=["emergency bay", "ICU", "blood bank", "ambulance"], staff=["emergency physician"], related=[],
                               aliases=["trauma care", "trauma centre", "trauma center", "emergency care", "casualty"]),
}
ALIAS_RE = {s: re.compile(r"\b(?:" + "|".join(re.escape(a) for a in sorted(m["aliases"], key=len, reverse=True)) + r")\b")
            for s, m in SERVICES.items()}
NEGATION_RE = re.compile(r"\b(?:no|not|without|nil|unavailable)\s+(?:\w+\s+){0,2}$")
ALWAYS_OPEN_RE = re.compile(r"\b(?:24\s*[x/*×]\s*7|24\s*hours?|24[- ]hour|round[- ]the[- ]clock)\b")


def group_services(by: str) -> dict:
    key = "specialty" if by == "Specialty" else "area"
    groups: dict = {}
    for name, meta in SERVICES.items():
        groups.setdefault(meta[key], []).append(name)
    return dict(sorted(groups.items()))


# ----------------------------------------------------------------------------
# 2. LOCATION VALIDATION
# ----------------------------------------------------------------------------
def _valid_prefixes() -> set:
    ranges = [(11, 28), (30, 34), (36, 53), (56, 64), (67, 85)]
    return {f"{n:02d}" for lo, hi in ranges for n in range(lo, hi + 1)}


VALID_PIN_PREFIXES = _valid_prefixes()
INDIA_BBOX = dict(lat=(6.5, 37.5), lon=(68.0, 97.5))
PLACE_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-&()]{1,59}$")


def validate_pin(raw: str):
    """Return (pin, error). Format and postal-region check only; cannot prove a PIN is in use."""
    pin = (raw or "").strip().replace(" ", "")
    if not pin:
        return None, "Enter a 6-digit PIN code."
    if not pin.isdigit():
        return None, "PIN must contain digits only (no letters or symbols)."
    if len(pin) != 6:
        return None, f"PIN must be exactly 6 digits (you entered {len(pin)})."
    if pin[0] == "0":
        return None, "PIN cannot start with 0."
    if pin[0] == "9":
        return None, "PINs starting with 9 are Army Post Office codes, not a civilian area."
    if pin[:2] not in VALID_PIN_PREFIXES:
        return None, f"Prefix '{pin[:2]}' is not an assigned Indian postal region."
    return pin, None


def validate_place(raw: str, label: str):
    name = " ".join((raw or "").split())
    if not name:
        return None, f"Enter a {label} name."
    if not PLACE_RE.match(name):
        return None, f"{label.capitalize()} must be 2-60 characters, start with a letter, and use only letters, spaces, . ' - & ( )."
    return name, None


def validate_coordinates(lat_raw: str, lon_raw: str):
    """Return (lat, lon, error, warning)."""
    try:
        lat, lon = float((lat_raw or "").strip()), float((lon_raw or "").strip())
    except ValueError:
        return None, None, "Latitude and longitude must be decimal numbers, e.g. 18.5204 and 73.8567.", None
    if not (-90 <= lat <= 90):
        return None, None, "Latitude must be between -90 and 90.", None
    if not (-180 <= lon <= 180):
        return None, None, "Longitude must be between -180 and 180.", None
    warn = None
    if not (INDIA_BBOX["lat"][0] <= lat <= INDIA_BBOX["lat"][1] and INDIA_BBOX["lon"][0] <= lon <= INDIA_BBOX["lon"][1]):
        warn = "These coordinates are outside India; results may be empty."
    return lat, lon, None, warn


# ----------------------------------------------------------------------------
# 3. DATA: adapt the original facilities-table columns to what the dashboard needs
# ----------------------------------------------------------------------------
NULLS = {"null", "none", "nan", "n/a", "na", "", "-", "unknown", "<na>"}
HOURS_COLUMNS = ("hours", "opening_hours", "openingHours", "operating_hours", "open_hours")


def _items(value) -> list:
    """Text, JSON-array text, list or array -> list of non-empty strings."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [s for v in value for s in _items(v)]
    text = str(value).strip()
    if text.lower() in NULLS:
        return []
    if text.startswith("["):
        try:
            import json
            return [s for v in json.loads(text) for s in _items(v)]
        except ValueError:
            pass
    return [text]


def clean_phone(*values):
    """First plausibly formatted phone (10-13 digits, not a repeated-digit placeholder)."""
    for value in values:
        for item in _items(value):
            digits = re.sub(r"\D", "", item)
            if 10 <= len(digits) <= 13 and len(set(digits)) > 2:
                return item
    return ""


def _text(df, col):
    if col not in df:
        return pd.Series([""] * len(df), index=df.index)
    s = df[col].astype("string").fillna("").str.strip()
    return s.where(~s.str.lower().isin(NULLS), "")


def from_facility_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Original facilities-table columns -> dashboard columns."""
    for need in ("name", "latitude", "longitude"):
        if need not in raw.columns:
            raise ValueError(f"The facilities table needs a '{need}' column.")
    df = raw.copy().reset_index(drop=True)
    ids = _text(df, "unique_id")
    df["equity_facility_id"] = ("fdr:" + ids).where(ids.ne("") & ~ids.duplicated(keep=False), "")
    df["lat"] = pd.to_numeric(_text(df, "latitude"), errors="coerce")
    df["lon"] = pd.to_numeric(_text(df, "longitude"), errors="coerce")
    inside = df["lat"].between(*INDIA_BBOX["lat"]) & df["lon"].between(*INDIA_BBOX["lon"])
    df.attrs["dropped_no_point"] = int((~inside).sum())
    df = df[inside].reset_index(drop=True)

    zip_text = _text(df, "address_zipOrPostcode")
    addr_parts = [_text(df, c) for c in ("address_line1", "address_line2", "address_line3", "address_city", "address_stateOrRegion")]
    address = pd.concat(addr_parts + [zip_text], axis=1).apply(lambda r: ", ".join(p for p in r if p), axis=1)
    pin = zip_text.str.extract(r"(?<!\d)([1-9]\d{5})(?!\d)")[0].fillna(address.str.extract(r"(?<!\d)([1-9]\d{5})(?!\d)")[0]).fillna("")
    state = _text(df, "address_stateOrRegion")
    ids = _text(df, "unique_id")
    df["facility_id"] = ("fdr:" + ids) if "unique_id" in df else [f"row:{i}" for i in range(len(df))]
    df["city"] = _text(df, "address_city")
    df["district"] = df["city"]            # the table has no district column; PIN-directory join is in the team notebooks
    df["state"] = state.where(~state.str.islower(), state.str.title())
    df["pin"] = pin
    df["address"] = address
    df["phone"] = [clean_phone(a, b) for a, b in zip(df["phone_numbers"] if "phone_numbers" in df else [None] * len(df),
                                                     df["officialPhone"] if "officialPhone" in df else [None] * len(df))]
    hours_col = next((c for c in HOURS_COLUMNS if c in df), None)
    df["hours"] = _text(df, hours_col) if hours_col else ""
    blob = (_text(df, "capability") + " " + _text(df, "description") + " " + _text(df, "procedure") + " " + _text(df, "specialties")).str.lower()
    df["open_24x7"] = blob.map(lambda t: bool(ALWAYS_OPEN_RE.search(t)))

    service_text = (blob + " " + _text(df, "equipment").str.lower()).tolist()
    def services_in(text):
        found = []
        for name, rx in ALIAS_RE.items():
            if any(not NEGATION_RE.search(text[max(0, m.start() - 30):m.start()]) for m in rx.finditer(text)):
                found.append(name)
        return found
    df["service_list"] = [services_in(t) for t in service_text]
    evidence_fields = {
        "capability": "capability", "procedure": "procedure", "specialty": "specialties",
        "equipment": "equipment", "description": "description",
    }
    texts = [_text(df, column) for column in evidence_fields.values()]
    df["service_text"] = [
        " | ".join(f"{label}: {text}" for label, text in zip(evidence_fields, values) if text)
        for values in zip(*texts)
    ]
    df["equip_list"] = [[e.lower() for e in _items(v)] or ([t] if t else []) for v, t in zip(df["equipment"] if "equipment" in df else [None] * len(df), _text(df, "equipment"))]
    df["staff_list"] = [[s.lower() for s in _items(v)] for v in (df["specialties"] if "specialties" in df else [None] * len(df))]
    op = _text(df, "operatorTypeId")
    df["ownership"] = op.str.title()
    cap_text = _text(df, "capacity")
    df["capacity_text"] = cap_text
    df["capacity_present"] = cap_text.ne("")
    df["capacity_num"] = pd.to_numeric(cap_text.str.extract(r"^(\d+(?:\.0+)?)$")[0], errors="coerce")
    df["occupancy"] = pd.to_numeric(df["occupancy"], errors="coerce") if "occupancy" in df else np.nan
    df["occupancy"] = df["occupancy"].where(df["occupancy"].isna() | (df["occupancy"] <= 1), df["occupancy"] / 100)
    df["last_verified"] = pd.to_datetime(df["last_verified"], errors="coerce") if "last_verified" in df else pd.NaT
    df["accreditation"] = _text(df, "accreditation")
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce") if "rating" in df else np.nan
    df["source"] = _text(df, "source").replace("", "facilities table")
    df.attrs["district_from_city"] = True
    return df


def from_dashboard_csv(df: pd.DataFrame) -> pd.DataFrame:
    """CSV in this dashboard's own simple schema -> the same unified frame."""
    required = ["name", "city", "district", "state", "pin", "lat", "lon", "services"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    explicit_ids = _text(df, "facility_id")
    raw = pd.DataFrame({
        "unique_id": df["name"].astype(str), "name": df["name"], "latitude": df["lat"], "longitude": df["lon"],
        "address_city": df["city"], "address_stateOrRegion": df["state"], "address_zipOrPostcode": df["pin"],
        "specialties": df["services"], "equipment": df.get("equipment", ""), "capacity": df.get("beds", df.get("capacity")),
        "phone_numbers": df.get("phone"), "hours": df.get("hours"), "operatorTypeId": df.get("ownership"),
        "occupancy": df.get("occupancy"), "accreditation": df.get("accreditation"), "rating": df.get("rating"),
        "last_verified": df.get("last_verified"), "source": df.get("source"),
        "district_input": df["district"], "services_input": df["services"],
        "exact_equity_id": explicit_ids.where(~explicit_ids.duplicated(keep=False), ""),
    })
    out = from_facility_table(raw)
    out["district"] = _text(out, "district_input").replace("", pd.NA).fillna(out["city"])
    out["equity_facility_id"] = out.pop("exact_equity_id")
    # This schema lists services explicitly, even when other rows have invalid coordinates.
    out["service_list"] = [
        [service for service in SERVICES if service.casefold() in explicit]
        for explicit in ({term.strip().casefold() for term in str(value).split(";")}
                         for value in out.pop("services_input"))
    ]
    out.attrs["district_from_city"] = False
    return out


@st.cache_data(show_spinner=False)
def demo_table() -> pd.DataFrame:
    """Synthetic rows shaped like the original facilities table (so the demo exercises the real adapter)."""
    rng = np.random.default_rng(7)
    cities = {"New Delhi": ("Delhi", 28.6139, 77.2090, 110001), "Mumbai": ("Maharashtra", 19.0760, 72.8777, 400001),
              "Pune": ("Maharashtra", 18.5204, 73.8567, 411001), "Bengaluru": ("Karnataka", 12.9716, 77.5946, 560001),
              "Chennai": ("Tamil Nadu", 13.0827, 80.2707, 600001), "Kolkata": ("West Bengal", 22.5726, 88.3639, 700001),
              "Lucknow": ("Uttar Pradesh", 26.8467, 80.9462, 226001), "Jaipur": ("Rajasthan", 26.9124, 75.7873, 302001)}
    kinds = ["City Hospital", "Medical Centre", "Super Speciality Hospital", "Govt. Hospital", "Care Institute", "Memorial Hospital"]
    rows = []
    for city, (state, lat, lon, pin0) in cities.items():
        for i in range(10):
            offered = list(rng.choice(list(SERVICES), size=int(rng.integers(3, 9)), replace=False))
            equip = sorted({e for s in offered for e in SERVICES[s]["equip"] if rng.random() < 0.85})
            specs = [SERVICES[s]["aliases"][0] for s in offered]
            hospital = bool(rng.random() < 0.8)
            always_open = bool(rng.random() < 0.5)
            rows.append(dict(
                unique_id=f"synthetic-{city[:3].lower()}-{i:02d}", name=f"{city} {kinds[i % len(kinds)]} {i + 1}",
                phone_numbers=f'["+91 90000 {int(rng.integers(10000, 99999))}"]',
                address_line1=f"{int(rng.integers(1, 200))} Example Road", address_city=city, address_stateOrRegion=state.lower(),
                address_zipOrPostcode=str(pin0 + int(rng.integers(0, 40))), countries="India",
                facilityTypeId="hospital" if hospital else "clinic", operatorTypeId=str(rng.choice(["public", "private"])),
                description="Synthetic demo record.", numberDoctors=None if rng.random() < 0.3 else str(int(rng.integers(2, 120))),
                capacity=str(int(rng.integers(20, 400))) if rng.random() < 0.35 else None,
                yearEstablished=str(int(rng.integers(1960, 2020))),
                specialties=";".join(specs), procedure=None, equipment=";".join(equip),
                capability=("24x7 emergency services. " if always_open else "") + "General inpatient care.",
                latitude=lat + rng.uniform(-0.12, 0.12), longitude=lon + rng.uniform(-0.12, 0.12),
                organization_type="facility", affiliated_staff_presence=str(bool(rng.random() < 0.3)).lower(),
                custom_logo_presence=str(bool(rng.random() < 0.5)).lower(),
                engagement_metrics_n_followers=str(int(rng.integers(0, 3000))), distinct_social_media_presence_count=str(int(rng.integers(0, 6))),
                hours="Open 24 hours" if always_open else "Mon-Sat 9:00-17:00 (synthetic)",
                occupancy=round(float(rng.uniform(0.35, 0.98)), 2),
                last_verified=(pd.Timestamp.today() - pd.Timedelta(days=int(rng.integers(10, 600)))).date().isoformat(),
                source="synthetic demo data"))
    adjust_demo_equity_coordinates(rows)
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def prepare(raw: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Normalize facility data for the dashboard; no capacity model is loaded or run."""
    return from_dashboard_csv(raw) if kind == "csv" else from_facility_table(raw)


def load_data():
    st.sidebar.header("Data")
    modes = ["Demo (synthetic)", "Facilities table export", "Dashboard-schema CSV", SNAPSHOT_MODE]
    mode = st.sidebar.radio("Facility data", modes, index=modes.index(SNAPSHOT_MODE) if snapshot_configured() else 0)
    if mode == SNAPSHOT_MODE:
        try:
            snapshot = load_classic_snapshot(from_facility_table)
        except Exception:
            st.error("The Databricks snapshot could not be loaded. Check the SQL warehouse and table resources, or select another data source.")
            return None, mode
        st.caption(f"Prepared snapshot {snapshot.prepared_at} · {snapshot.source_count:,} facility records")
        df = snapshot.facilities
        if df.empty:
            st.warning("The prepared snapshot has no facility records with usable coordinates.")
            return None, mode
        return df, mode
    if mode == "Demo (synthetic)":
        st.sidebar.warning("Demo data is synthetic. Do not use it for real decisions.")
        return prepare(demo_table(), "table"), mode
    if mode == "Facilities table export":
        path = st.sidebar.text_input("Path to export (CSV or Parquet)", value=os.environ.get("DATATHON_FACILITY_EXPORT", ""))
        up = st.sidebar.file_uploader("...or upload it", type=["csv", "parquet"])
        if up is not None:
            raw = pd.read_parquet(up) if up.name.endswith(".parquet") else pd.read_csv(up, dtype="string")
        elif path:
            p = Path(path).expanduser()
            if not p.is_file():
                st.error(f"File not found: {p}")
                return None, mode
            raw = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype="string")
        else:
            st.info("Give the path to the private facilities export (the same file as DATATHON_FACILITY_EXPORT), or upload it.")
            return None, mode
        df = prepare(raw, "table")
    else:
        up = st.sidebar.file_uploader("CSV file", type="csv")
        st.sidebar.caption("Required: name, city, district, state, pin, lat, lon, services (';'-separated). "
                           "Optional: facility_id for equity evidence, phone, hours, beds, equipment, ownership, occupancy (0-1 or %), accreditation, rating, last_verified, source.")
        if up is None:
            st.info("Upload a CSV in the sidebar, or switch to demo data.")
            return None, mode
        df = prepare(pd.read_csv(up, dtype="string"), "csv")
    if df.attrs.get("dropped_no_point"):
        st.sidebar.caption(f"{df.attrs['dropped_no_point']:,} rows without a usable India coordinate are excluded from distance ranking.")
    if df.empty:
        st.warning("This data source has no facility records with usable coordinates.")
        return None, mode
    return df, mode


# ----------------------------------------------------------------------------
# 4. LOCATION RESOLUTION + SCORING
# ----------------------------------------------------------------------------
def resolve_location(kind, vals, df):
    """Return (lat, lon, label, note, state_hint) or raise ValueError with a user-facing message."""
    if kind == "Coordinates (lat/lon)":
        lat, lon = vals["lat"], vals["lon"]
        i = int(np.argmin(haversine_km(lat, lon, df["lat"].values, df["lon"].values)))
        return lat, lon, f"{lat:.4f}, {lon:.4f}", None, df["address_stateOrRegion"].iloc[i] if "address_stateOrRegion" in df else df["state"].iloc[i]
    if kind == "PIN code":
        pin = vals["pin"]
        hit = df[df["pin"] == pin]
        note = None
        if not len(hit):
            hit = df[df["pin"].str[:3] == pin[:3]]
            note = f"No facility on record in PIN {pin}; using the centre of PIN area {pin[:3]}xxx (approximate)."
        if not len(hit):
            raise ValueError(f"No facilities on record near PIN {pin}. Try a city or enter coordinates.")
        label = f"PIN {pin}"
    else:
        col = "city" if kind == "City" else "district"
        hit = df[df[col].str.lower() == vals["name"].lower()]
        if vals.get("state"):
            hit = hit[hit["state"].str.lower() == vals["state"].lower()]
        if not len(hit):
            raise ValueError(f"No facilities on record for {col} '{vals['name']}'. Check spelling or enter coordinates.")
        label = vals["name"]
        note = f"Using the centre of facilities in this {col}."
        if kind == "District" and df.attrs.get("district_from_city"):
            note += " The table has no district column, so districts are matched on the facility's city."
    state = hit["address_stateOrRegion"].iloc[0] if "address_stateOrRegion" in hit else hit["state"].iloc[0]
    return hit["lat"].mean(), hit["lon"].mean(), label, note, state


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _covered(required, have):
    have_l = [h.lower() for h in have]
    found = [r for r in required if any(r.lower() in h or h in r.lower() for h in have_l)]
    return found, [r for r in required if r not in found]


def reported_capacity_view(r):
    """Display only capacity values present in the source data; never infer or predict beds."""
    if r["capacity_present"]:
        num = r["capacity_num"]
        value = f"{num:.0f}" if pd.notna(num) else r["capacity_text"][:12]
        return f"Reported capacity: {r['capacity_text']}", value, "Beds (reported)"
    return "Capacity not reported.", "n/a", "Beds"


def score_facilities(df, service, lat, lon, weights):
    """Every facility that lists the service (or a related one), sorted nearest-first."""
    meta = SERVICES[service]
    d = haversine_km(lat, lon, df["lat"].values, df["lon"].values)
    pool = df.assign(distance_km=d)
    today = pd.Timestamp.today()
    results = []
    for _, r in pool.iterrows():
        have = set(r["service_list"])
        if service in have:
            match, matched_service = 1.0, service
            match_note = f"Facility text mentions **{service}** (keyword match)."
        else:
            alt = [x for x in meta["related"] if x in have]
            if not alt:
                continue
            match, matched_service = 0.5, alt[0]
            match_note = f"Facility text mentions related service **{matched_service}** (partial match for {service})."
        eq_f, eq_m = _covered(meta["equip"], r["equip_list"])
        st_f, st_m = _covered(meta["staff"], r["staff_list"])
        capability = (len(eq_f) / max(len(meta["equip"]), 1) + len(st_f) / max(len(meta["staff"]), 1)) / 2
        availability = 1.0 if r["open_24x7"] else (0.3 if meta["urgent"] else 0.8)
        quality = 0.5 * (1.0 if r["accreditation"] else 0.5) + 0.5 * ((r["rating"] / 5) if pd.notna(r["rating"]) else 0.6)
        if pd.isna(r["last_verified"]):
            age_m, confidence = None, 0.5
        else:
            age_m = (today - r["last_verified"]).days / 30
            confidence = 1.0 if age_m <= 6 else 0.8 if age_m <= 12 else 0.5
        comps = dict(capability=capability, availability=availability, quality=quality, confidence=confidence)
        fit = match * sum(weights[k] * v for k, v in comps.items()) / max(sum(weights.values()), 1e-9)
        occ = None if pd.isna(r["occupancy"]) else float(r["occupancy"])
        cap_line, cap_value, cap_label = reported_capacity_view(r)

        why = [match_note, f"**{r['distance_km']:.1f} km** straight-line from your location ({r['city'] or 'city n/a'}, {r['state'] or 'state n/a'})."]
        why.append(cap_line)
        if occ is not None:
            why.append(f"Occupancy on record: **{occ:.0%}** ({1 - occ:.0%} spare capacity).")
        if eq_f:
            why.append("Equipment text covers: " + ", ".join(eq_f) + ".")
        if r["open_24x7"]:
            why.append("Text mentions 24x7 operation.")
        caveats = []
        if match < 1:
            caveats.append("Only a related service is mentioned. Call to confirm.")
        if eq_m:
            caveats.append("Equipment not mentioned: " + ", ".join(eq_m) + ".")
        if meta["urgent"] and not r["open_24x7"]:
            caveats.append("Round-the-clock operation is not mentioned, and this service is often time-critical.")
        if occ is None:
            caveats.append("No recorded occupancy data.")
        if age_m is None:
            caveats.append("No verification date on the record.")
        elif age_m > 12:
            caveats.append(f"Record last verified {age_m / 12:.1f} years ago and may be stale.")
        results.append(dict(row=r, dist=float(r["distance_km"]), fit=round(100 * fit, 1), match=match, comps=comps,
                            occ=occ, spare=None if occ is None else max(0.0, 1 - occ), why=why, caveats=caveats,
                            matched_service=matched_service,
                            cap_value=cap_value, cap_label=cap_label,
                            equity_tier=(tuple(eq_m), tuple(st_m)) if match == 1 else None,
                            verified=None if pd.isna(r["last_verified"]) else r["last_verified"].date().isoformat()))
    return sorted(results, key=lambda x: x["dist"])


def render_source_details(result, service):
    row = result["row"]
    confidence = result["service_confidence"]
    st.markdown(f"**Service confidence** {confidence.score:g}/100 · {confidence.tier}")
    st.caption(f"{service} · Service evidence {confidence.evidence_points:g}/80 · "
               f"Branch identity {confidence.branch_points:g}/20")
    record_evidence = service_evidence(row, result["matched_service"], (), SERVICES)
    evidence = confidence if confidence.term else record_evidence
    if evidence.term:
        label = "Matched service text" if confidence.term else "Facility-record keyword text"
        st.write(f"**{label}** {evidence.term}")
    if evidence.excerpt:
        st.write(f"**Evidence excerpt** {evidence.excerpt}")
        if not confidence.urls and not record_evidence.urls:
            st.caption("Source link unavailable for this facility text.")
    for i, url in enumerate(confidence.urls, 1):
        st.markdown(f"[Open service source {i}](<{url}>)")
    for i, url in enumerate((url for url in record_evidence.urls if url not in confidence.urls), 1):
        st.markdown(f"[Open facility source {i}](<{url}>)")

    if result.get("scenario") is not None:
        capacity = capacity_details(row)
        st.caption(capacity.detail)
        if capacity.crosscheck:
            st.caption(capacity.crosscheck)
        for label, url in (
            ("Open capacity source", capacity.source_url),
            ("Open phone source", row.get("phone_source_url")),
            ("Visit facility website", row.get("website_url")),
        ):
            if link := safe_url(url):
                st.markdown(f"[{label}](<{link}>)")


def render_load_details(result):
    if projection := result.get("projected_load"):
        demand_label = "Projected demand" if projection["caseload_predicted"] else "Historical demand"
        st.markdown(f"**{demand_label}** {projection['caseload']:,.1f} {projection['unit']}")
        st.markdown(f"**Modeled throughput** {projection['capacity']:,.1f} {projection['unit']}")
        st.caption(f"{projection['scope']} · {projection['period']} · Planning estimate")
        st.markdown("[Projection methods and data](https://github.com/augustchu/datathon-F26/blob/main/non-ml/caseload/NETWORK_CASELOAD.md)")


def render_recommended_breakdown(result):
    score = result["recommended"]
    labels = ["Travel", "Service match penalty", "Service confidence penalty", "Total"]
    parts = ["travel", "service_match", "service_confidence", "score"]
    st.dataframe(pd.DataFrame({"Recommended score component": labels,
                               score["units"]: [round(score[key], 2) for key in parts]}),
                 hide_index=True, use_container_width=True)


# ----------------------------------------------------------------------------
# 5. MAP WITH PINS
# ----------------------------------------------------------------------------
def safe(value) -> str:
    """HTML-escape and neutralise characters that could break folium's JS template strings."""
    return html.escape(str(value)).replace("`", "&#96;").replace("$", "&#36;").replace("\\", "&#92;")


def show_html(markup: str, height: int):
    """st.iframe on current Streamlit; components.html on older versions (e.g. Databricks Apps' 1.38)."""
    if hasattr(st, "iframe"):
        st.iframe(markup, height=height)
    else:
        components.html(markup, height=height)


def build_map(origin=None, top=(), label="") -> str:
    center = origin if origin is not None else (22.5, 80.0)
    m = folium.Map(location=center, zoom_start=11 if origin is not None else 5,
                   tiles="OpenStreetMap", control_scale=True)
    if origin is None:
        return m.get_root().render()
    lat0, lon0 = origin
    folium.Marker([lat0, lon0], tooltip=f"Your location: {safe(label)}",
                  icon=folium.Icon(color="red", icon="home", prefix="fa")).add_to(m)
    points = [[lat0, lon0]]
    for i, res in enumerate(top, 1):
        r = res["row"]
        phone = r["phone"]
        phone_html = f'<a href="tel:{safe(re.sub(r"[^+0-9]", "", phone))}">{safe(phone)}</a>' if phone else "Not listed"
        hours = safe(r["hours"]) if r["hours"] else "Not listed (call to confirm)"
        gmaps = f"https://www.google.com/maps/dir/?api=1&destination={r['lat']},{r['lon']}"
        route = res.get("route_minutes")
        drive = f"<b>Estimated drive:</b> {route:.0f} min<br>" if route is not None else ""
        popup = (f'<div style="min-width:230px;font:13px/1.45 sans-serif">'
                 f'<b>{i}. {safe(r["name"])}</b><br>'
                 f'<b>Address:</b> {safe(r["address"] or "Not listed")}<br>'
                 f'<b>Phone:</b> {phone_html}<br><b>Hours:</b> {hours}<br>'
                 f'<b>Distance:</b> {res["dist"]:.1f} km (straight line)<br>'
                 f'{drive}'
                 f'<b>{safe(res["cap_label"])}:</b> {safe(res["cap_value"])}<br>'
                 f'<a href="{gmaps}" target="_blank" rel="noopener">Directions</a><br>'
                 f'<i style="color:#666">{safe(CALL_PROMPT)}</i></div>')
        icon = folium.DivIcon(
            html=(
                '<div style="'
                'width:36px;height:42px;position:relative;'
                '">'
                '<div style="'
                'position:absolute;left:3px;top:3px;'
                'width:30px;height:30px;'
                'background:#1d70b8;color:#fff;'
                'border:2px solid #fff;'
                'border-radius:50% 50% 50% 0;'
                'transform:rotate(-45deg);'
                'box-shadow:0 1px 4px rgba(0,0,0,.5);'
                '">'
                '<div style="'
                'width:100%;height:100%;'
                'display:flex;align-items:center;justify-content:center;'
                'transform:rotate(45deg);'
                'font:bold 13px sans-serif;'
                '">'
                f'{i}'
                '</div>'
                '</div>'
                '</div>'
            ),
            icon_size=(36, 42),
            icon_anchor=(18, 42),
        )
        folium.Marker([r["lat"], r["lon"]], icon=icon, tooltip=f"{i}. {safe(r['name'])}",
                      popup=folium.Popup(popup, max_width=320)).add_to(m)
        points.append([r["lat"], r["lon"]])
    if len(points) > 1:
        m.fit_bounds(points, padding=(30, 30))
    return m.get_root().render()


# ----------------------------------------------------------------------------
# 6. UI
# ----------------------------------------------------------------------------
st.title("🏥 Facility Finder")
st.caption("Choose a service and location to compare mapped options, fit scores, and available source evidence.")
st.warning("Beds are a rough measure of facility size. Call the facility to confirm the service, branch, and current availability before traveling.")

df, data_mode = load_data()
snapshot_mode = data_mode == SNAPSHOT_MODE
synthetic = data_mode == "Demo (synthetic)"

with st.sidebar:
    st.header("Results")
    top_n = st.slider("Results to show", 1, 10, 5)
    with st.expander("Fit-score weights"):
        st.caption("The fit score rates how well each facility's own text supports delivering the service. The Fit sort uses these weights.")
        weights = dict(
            capability=st.slider("Equipment & staff match", 0.0, 1.0, 0.40, 0.05),
            availability=st.slider("Availability (24x7)", 0.0, 1.0, 0.25, 0.05),
            quality=st.slider("Accreditation & rating", 0.0, 1.0, 0.20, 0.05),
            confidence=st.slider("Data freshness", 0.0, 1.0, 0.15, 0.05))

left, right = st.columns(2)
with left:
    with st.container(height=360, border=True):
        st.subheader("1. Service")
        by = st.radio("Browse services by", ["Specialty", "Afflicted body area"], horizontal=True)
        groups = group_services(by)
        all_specialties = "All specialties"
        group_options = [all_specialties, *groups] if by == "Specialty" else list(groups)
        group = st.selectbox(by, group_options)
        services = [name for matches in groups.values() for name in matches] if group == all_specialties else groups[group]
        service = st.selectbox("Service", services)
        m = SERVICES[service]
        st.caption(f"{m['specialty']} · {m['area']}" + (" · time-critical" if m["urgent"] else ""))
with right:
    with st.container(height=360, border=True):
        st.subheader("2. Location")
        kind = st.selectbox("Location type", ["PIN code", "City", "District", "Coordinates (lat/lon)"])
        vals, errors, warnings = {}, [], []
        if kind == "PIN code":
            raw_in = st.text_input("PIN code", max_chars=6, placeholder="e.g. 411001")
            if raw_in:
                vals["pin"], err = validate_pin(raw_in)
                errors += [err] if err else []
        elif kind in ("City", "District"):
            raw_in = st.text_input(kind, placeholder="e.g. Pune")
            state_in = st.text_input("State (optional, to disambiguate)", placeholder="e.g. Maharashtra")
            if raw_in:
                vals["name"], err = validate_place(raw_in, kind.lower())
                errors += [err] if err else []
            if state_in:
                vals["state"], err = validate_place(state_in, "state")
                errors += [err] if err else []
        else:
            c1, c2 = st.columns(2)
            lat_raw, lon_raw = c1.text_input("Latitude", placeholder="18.5204"), c2.text_input("Longitude", placeholder="73.8567")
            if lat_raw or lon_raw:
                la, lo, err, warn = validate_coordinates(lat_raw, lon_raw)
                vals.update(lat=la, lon=lo)
                errors += [err] if err else []
                warnings += [warn] if warn else []
        for e in errors:
            st.error(e)
        for w in warnings:
            st.warning(w)
with st.container(border=True):
    st.subheader("3. Options")
    severity = st.selectbox("How urgent is this need?", SEVERITIES, index=1 if m["urgent"] else 0,
                            key=f"severity_{service}",
                            help="Recommended uses travel and service evidence at every urgency level. Projected load comparison is available for eligible routine searches.")
    consider_projected_load = False
    use_traveltime = False
    if snapshot_mode:
        load_col, routing_col = st.columns(2)
        load_allowed = severity == "Routine" and not m["urgent"] and service not in TIME_CRITICAL_SERVICES
        consider_projected_load = load_col.toggle(
            "Consider projected load", value=False, disabled=not load_allowed,
            help="Recommended compares published monthly caseload and throughput for nearby facilities with comparable service evidence.",
        )
        consider_projected_load = consider_projected_load and load_allowed
        use_traveltime = routing_col.toggle(
            "Use TravelTime driving estimates", value=True,
            help="Recommended uses weekday-morning driving estimates when available and straight-line distance as a fallback.",
        )
    priority_keys = render_priorities()
    consider_equity = render_equity_control(service, synthetic=synthetic)

ready = df is not None and not errors and (vals.get("pin") or vals.get("name") or vals.get("lat") is not None)
if st.button("Find facilities", type="primary", disabled=not ready):
    st.session_state["search_started"] = True
elif df is not None and not ready and not errors:
    st.caption("Enter a valid location to enable search.")

search = None
if ready and st.session_state.get("search_started"):
    try:
        search = resolve_location(kind, vals, df)
    except ValueError as exc:
        st.error(str(exc))

if search:
    lat, lon, label, note, _ = search
    if note:
        st.info(note)
    svc = service
    ranked = score_facilities(df, svc, lat, lon, weights)
    if not ranked:
        st.warning(f"No facility's text mentions {svc} or a related service.")
        st.subheader("Map")
        show_html(build_map((lat, lon), label=label), 420)
    else:
        results_context = st.container()
        results_col, map_col = st.columns([1, 1], gap="medium")
        sort_options = [
            RECOMMENDED,
            "Distance (nearest first)",
            "Fit (highest first)",
            "Beds (most first)",
            "Occupancy (lowest first)",
        ]
        if priority_keys:
            sort_options.append(RANKED_CRITERIA)
        sort_key = "facility_sort_by"
        if st.session_state.get(sort_key) not in (None, *sort_options):
            st.session_state[sort_key] = RECOMMENDED
        with results_col:
            header_col, sort_col = st.columns([3, 2])
            header_col.subheader("Facility options")
            sort_by = sort_col.selectbox("Sort by", sort_options, key=sort_key, label_visibility="collapsed")

        evaluated = None
        uses_routes = False
        if snapshot_mode:
            evaluated = rank_classic_results(
                ranked, svc, (lat, lon), population_share_pct=0.0,
                severity=severity.lower(), use_traveltime=use_traveltime and sort_by == RECOMMENDED,
            )
            uses_routes = evaluated.uses_routes
            ranked = sorted(evaluated.results, key=lambda item: item["dist"])
            if uses_routes:
                for item in ranked:
                    item["route_minutes"] = float(item["scenario"]["travel_time_minutes"])

        candidates = ranked

        score_recommended(candidates, svc, SERVICES[svc]["aliases"], uses_routes=uses_routes)
        if sort_by == RECOMMENDED:
            candidates = sorted(candidates, key=lambda item: (
                item["recommended"]["score"],
                item["recommended"]["travel"],
                item["dist"],
            ))
        elif sort_by == "Fit (highest first)":
            candidates = sorted(candidates, key=lambda item: -item["fit"])
        elif sort_by == "Beds (most first)":
            candidates = sorted(candidates, key=lambda item: (
                pd.isna(item["row"]["capacity_num"]),
                -float(item["row"]["capacity_num"]) if pd.notna(item["row"]["capacity_num"]) else 0,
            ))
        elif sort_by == "Occupancy (lowest first)":
            candidates = sorted(candidates, key=lambda item: (item["occ"] is None, item["occ"] or 0))
        elif sort_by == RANKED_CRITERIA and candidates:
            score_priorities(candidates, priority_keys)
            candidates = sorted(candidates, key=lambda item: (-item["priority"], item["dist"]))
        else:
            candidates = sorted(candidates, key=lambda item: item["dist"])

        compare_projected_load = consider_projected_load and sort_by == RECOMMENDED
        projected_comparable = 0
        if compare_projected_load:
            candidates, projected_comparable = apply_projected_load(candidates, svc, uses_routes=uses_routes)

        equity_view = prepare_equity_results(
            candidates, svc, top_n, enabled=consider_equity, synthetic=synthetic,
            urgent=m["urgent"] or severity in {"Urgent", "Emergency"},
        )
        top = equity_view.results
        with results_context:
            if evaluated is not None and sort_by == RECOMMENDED:
                st.caption(evaluated.routing_status)
                if evaluated.routing_warning:
                    st.warning(evaluated.routing_warning)
            if compare_projected_load:
                if projected_comparable:
                    st.caption(f"Compared projected monthly load for {projected_comparable} nearby facilities with comparable service evidence.")
                else:
                    st.caption("Comparable projected load is unavailable for this search. Showing the baseline Recommended order.")
            render_equity_summary(equity_view)
            st.caption("Service matches use keywords from each facility’s own text. The team’s service evidence "
                       "and verification notebooks (04 and 04b) document scope checks. Call to verify the service and branch.")

        with results_col:
            # Fixed-height container with independent scrolling
            with st.container(height=720, border=False):
                for i, res in enumerate(top, 1):
                    r = res["row"]

                    with st.container(border=True):
                        # Hospital name and location
                        st.markdown(f"### {i}. {r['name']}")
                        st.caption(
                            f"{r['city'] or 'city n/a'}, "
                            f"{r['state'] or 'state n/a'} · "
                            f"PIN {r['pin'] or 'n/a'} · "
                            f"{r['ownership'] or 'ownership n/a'}"
                        )

                        # Metrics below the name, with enough room for values
                        metrics = [
                            ("Distance", f"{res['dist']:.1f} km"),
                            ("Fit (/100)", f"{res['fit']:.1f}"),
                            (res["cap_label"], res["cap_value"]),
                        ]

                        if res["occ"] is not None:
                            metrics.append(
                                ("Occupancy", f"{res['occ']:.0%}")
                            )

                        metric_cols = st.columns(len(metrics))

                        for col, (lab, val) in zip(metric_cols, metrics):
                            with col:
                                st.metric(lab, val)

                        if (minutes := res.get("route_minutes")) is not None:
                            st.caption(f"Estimated drive {minutes:.0f} min · weekday morning")
                        if sort_by == RECOMMENDED:
                            recommended = res["recommended"]
                            score_label = "Baseline Recommended score" if projected_comparable else "Recommended score"
                            st.caption(f"{score_label} {recommended['score']:.1f} {recommended['units']} · lower is better")
                        elif "priority" in res:
                            st.caption(f"Ranked criteria score {res['priority']:.1f}/100")
                        phone = (
                            f"[{r['phone']}](tel:{re.sub(r'[^+0-9]', '', r['phone'])})"
                            if r["phone"] else "Not listed"
                        )

                        st.markdown(
                            f"**Address:** {r['address'] or 'Not listed'}  \n"
                            f"**Phone:** {phone}  \n"
                            f"**Hours:** {r['hours'] or 'Not listed (call to confirm)'}"
                        )

                        st.caption(CALL_PROMPT)

                        st.markdown("**Why this facility**")
                        st.markdown(
                            "\n".join(f"- {w}" for w in res["why"])
                        )

                        if res["caveats"]:
                            st.markdown("**Caveats**")
                            st.markdown(
                                "\n".join(f"- {w}" for w in res["caveats"])
                            )

                        render_source_details(res, svc)
                        render_load_details(res)
                        render_equity_card(equity_view, i - 1)

                        with st.expander("Fit breakdown and source"):
                            comp = pd.DataFrame({
                                "component": list(res["comps"]),
                                "value (0-1)": [
                                    round(v, 2) for v in res["comps"].values()
                                ],
                                "weight": [
                                    weights[k] for k in res["comps"]
                                ],
                            })
                            st.dataframe(
                                comp,
                                hide_index=True,
                                use_container_width=True,
                            )
                            if sort_by == RECOMMENDED:
                                render_recommended_breakdown(res)
                            if "priority_parts" in res:
                                st.dataframe(
                                    pd.DataFrame(res["priority_parts"], columns=["Criterion", "Value", "Weight"]),
                                    hide_index=True, use_container_width=True,
                                )
                            st.caption(
                                f"Source: {r['source']} · "
                                f"facility_id {r['facility_id']} · "
                                f"last verified: {res['verified'] or 'unknown'}"
                            )

        with map_col:
            st.subheader("Map")
            show_html(build_map((lat, lon), top, label), 720)

            st.caption(
                "Click a pin for address, phone, hours and directions. "
                "Red = your location, numbered blue pins = results."
            )

            contacts = pd.DataFrame([
                {
                    "#": i,
                    "Facility": x["row"]["name"],
                    "Phone": x["row"]["phone"] or "Not listed",
                    "Hours": x["row"]["hours"] or "Not listed",
                    "Address": x["row"]["address"] or "Not listed",
                    "Distance (km)": round(x["dist"], 1)
                }
                for i, x in enumerate(top, 1)
            ])

            with st.expander("View all facility contact details"):
                st.dataframe(
                    contacts,
                    hide_index=True,
                    use_container_width=True
                )
else:
    st.subheader("Map")
    show_html(build_map(), 420)

with st.expander("How this prototype decides"):
    st.markdown(
        """
        - Service matching uses the facility’s own description, equipment, specialties, capabilities, and procedures.
          Related matches are labeled. Links and excerpts help you check the source when available.
        - Recommended combines travel, service match, and confidence for the selected service at the facility’s
          branch. Confidence combines up to 80 points for service evidence and 20 for branch identity.
          A branch earns zero confidence for a service with no supporting evidence.
          With straight-line distance, the confidence penalty converts to kilometers at two minutes per kilometer.
        - TravelTime estimates weekday-morning drives for Recommended results from the Databricks snapshot.
          A successful route search shows reachable facilities. The dashboard falls back to straight-line estimates
          if routing is unavailable or cannot find any routes within three hours.
        - Consider projected load compares published monthly caseload and throughput for nearby routine-care options
          with comparable evidence. The comparison uses planning estimates.
        - Ranked criteria weights your selected priorities in order. Equity scoring is optional and uses comparable
          evidence for the same service and branch. The map follows the final order shown on the cards.
        """
    )

st.markdown("""
<style>
/* Force larger scoring numbers inside Streamlit metrics */
div[data-testid="stMetricValue"],
div[data-testid="stMetricValue"] *,
[data-testid="stMetricValue"] span {
    font-size: 1.7rem !important;
    line-height: 1.25 !important;
    font-weight: 600 !important;
    white-space: nowrap !important;
}

/* Make metric labels slightly larger too */
div[data-testid="stMetricLabel"],
div[data-testid="stMetricLabel"] * {
    font-size: 0.95rem !important;
    line-height: 1.2 !important;
}
</style>
""", unsafe_allow_html=True)
