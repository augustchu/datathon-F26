"""Facility Finder dashboard.

Inputs (service + location) -> facility matches, a care_engine "Recommended" ranking, fit scores, and reported data.
Advanced options: switch on the criteria you care about and rank them for a "Ranked criteria" sort.

Run:  streamlit run dashboard/dashboard.py
Data: synthetic demo, a private facilities export (original table columns), or a CSV in this
      dashboard's own schema. Only source-reported capacity is displayed; no model predictions are used.
"""
import html
import importlib.util
import os
import re
import sys
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "non-ml" / "src"))
from facility_tool.equity import (  # noqa: E402
    MAX_EXTRA_KM, EquityCandidate, load_paired_equity_evidence, rank_for_paired_equity, synthetic_equity_evidence,
)
from facility_tool.network_load import read_network_pressure, replay_network_load  # noqa: E402
from facility_tool.recommend import compare_observed_availability, routine_load_allowed  # noqa: E402



def _load_care_engine():
    """Load care_engine.py by file path, so there is no import for the editor to resolve and no sys.path change.

    Looks in CARE_ENGINE_DIR, next to this file, then ../load-balancing-pipeline/. Returns (module, problem).
    """
    here = Path(__file__).resolve().parent
    tried = []
    for folder in (os.environ.get("CARE_ENGINE_DIR"), here, here.parent / "load-balancing-pipeline"):
        if not folder:
            continue
        path = Path(folder) / "care_engine.py"
        tried.append(str(path))
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("care_engine", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["care_engine"] = module
            spec.loader.exec_module(module)
            return module, None
        except Exception as exc:  # noqa: BLE001 - shown in the sidebar
            return None, f"{path} failed to load: {type(exc).__name__}: {exc}"
    return None, "care_engine.py not found. Looked in: " + "; ".join(tried)


care_engine, CARE_ENGINE_PROBLEM = _load_care_engine()

st.set_page_config(page_title="Facility Finder", page_icon="🏥", layout="wide")
CALL_PROMPT = "Call this facility before traveling to verify the service, branch, and current availability."

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
    df["facility_id"] = ("fdr:" + _text(df, "unique_id")) if "unique_id" in df else [f"row:{i}" for i in range(len(df))]
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
    df["equip_list"] = [[e.lower() for e in _items(v)] or ([t] if t else []) for v, t in zip(df["equipment"] if "equipment" in df else [None] * len(df), _text(df, "equipment"))]
    df["staff_list"] = [[s.lower() for s in _items(v)] for v in (df["specialties"] if "specialties" in df else [None] * len(df))]
    # care_engine matches service terms in labelled text ("capability: ...", "procedure: ...").
    labelled = {"capability": "capability", "procedure": "procedure", "specialty": "specialties",
                "equipment": "equipment", "description": "description"}
    pieces = {lab: ["; ".join(_items(v)) for v in (df[col] if col in df else [None] * len(df))] for lab, col in labelled.items()}
    df["service_text"] = [" | ".join(f"{lab}: {pieces[lab][i]}" for lab in labelled if pieces[lab][i]) for i in range(len(df))]
    df["facility_type"] = _text(df, "facilityTypeId")
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
    raw = pd.DataFrame({
        "unique_id": df["name"].astype(str), "name": df["name"], "latitude": df["lat"], "longitude": df["lon"],
        "address_city": df["city"], "address_stateOrRegion": df["state"], "address_zipOrPostcode": df["pin"],
        "specialties": df["services"], "equipment": df.get("equipment", ""), "capacity": df.get("beds", df.get("capacity")),
        "phone_numbers": df.get("phone"), "hours": df.get("hours"), "operatorTypeId": df.get("ownership"),
        "occupancy": df.get("occupancy"), "accreditation": df.get("accreditation"), "rating": df.get("rating"),
        "last_verified": df.get("last_verified"), "source": df.get("source"),
    })
    ids = _text(df, "facility_id")
    raw["_dashboard_equity_id"] = ids.where(~ids.duplicated(keep=False), "")
    out = from_facility_table(raw)
    out["equity_facility_id"] = out.pop("_dashboard_equity_id")
    out["district"] = _text(df.reset_index(drop=True).loc[out.index] if len(df) == len(out) else out, "district").where(lambda s: s != "", out["city"]) if "district" in out else out["district"]
    # In this schema services are listed explicitly, split on ';'.
    explicit = df.reset_index(drop=True).get("services")
    if explicit is not None and len(out) == len(df):
        out["service_list"] = [[s for s in SERVICES if s.lower() in {x.strip().lower() for x in str(v).split(";")}] for v in explicit]
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
    # Place two fictional Delhi maternity options close together for the equity-score example.
    rows[6]["latitude"] = rows[1]["latitude"] + 0.005
    rows[6]["longitude"] = rows[1]["longitude"]
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def prepare(raw: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Normalize facility data for the dashboard; no capacity model is loaded or run."""
    return from_dashboard_csv(raw) if kind == "csv" else from_facility_table(raw)


def load_data():
    st.sidebar.header("Data")
    mode = st.sidebar.radio("Facility data", ["Demo (synthetic)", "Facilities table export", "Dashboard-schema CSV"])
    if mode == "Demo (synthetic)":
        st.sidebar.warning("Demo data is synthetic. Do not use it for real decisions.")
        demo = prepare(demo_table(), "table")
        demo.attrs["synthetic_demo"] = True
        return demo
    if mode == "Facilities table export":
        path = st.sidebar.text_input("Path to export (CSV or Parquet)", value=os.environ.get("DATATHON_FACILITY_EXPORT", ""))
        up = st.sidebar.file_uploader("...or upload it", type=["csv", "parquet"])
        if up is not None:
            raw = pd.read_parquet(up) if up.name.endswith(".parquet") else pd.read_csv(up, dtype="string")
        elif path:
            p = Path(path).expanduser()
            if not p.is_file():
                st.error(f"File not found: {p}")
                st.stop()
            raw = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype="string")
        else:
            st.info("Give the path to the private facilities export (the same file as DATATHON_FACILITY_EXPORT), or upload it.")
            st.stop()
        df = prepare(raw, "table")
    else:
        up = st.sidebar.file_uploader("CSV file", type="csv")
        st.sidebar.caption("Required: name, city, district, state, pin, lat, lon, services (';'-separated). "
                           "Optional: facility_id (exact evidence ID), phone, hours, beds, equipment, ownership, "
                           "occupancy (0-1 or %), accreditation, rating, last_verified, source.")
        if up is None:
            st.info("Upload a CSV in the sidebar, or switch to demo data.")
            st.stop()
        df = prepare(pd.read_csv(up, dtype="string"), "csv")
    if df.attrs.get("dropped_no_point"):
        st.sidebar.caption(f"{df.attrs['dropped_no_point']:,} rows without a usable India coordinate are excluded from distance ranking.")
    return df


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
            match, match_note = 1.0, f"Facility text mentions **{service}** (keyword match; not a cited source)."
        else:
            alt = [x for x in meta["related"] if x in have]
            if not alt:
                continue
            match, match_note = 0.5, f"Does not mention {service}, but mentions related service **{alt[0]}** (partial match)."
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
        why = [match_note, f"**{r['distance_km']:.1f} km** straight-line from your location ({r['city'] or 'city n/a'}, {r['state'] or 'state n/a'})."]
        if occ is not None:
            why.append(f"Facility-wide occupancy on record: **{occ:.0%}**.")
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
        if age_m is None:
            caveats.append("No verification date on the record.")
        elif age_m > 12:
            caveats.append(f"Record last verified {age_m / 12:.1f} years ago and may be stale.")
        results.append(dict(row=r, dist=float(r["distance_km"]), fit=round(100 * fit, 1), match=match, comps=comps,
                            equity_tier=(tuple(eq_m), tuple(st_m)) if match == 1 else None,
                            occ=occ, spare=None if occ is None else max(0.0, 1 - occ), why=why, caveats=caveats,
                            verified=None if pd.isna(r["last_verified"]) else r["last_verified"].date().isoformat()))
    return sorted(results, key=lambda x: x["dist"])


def balance_rank(ranked, service, measures, extra_km, as_of):
    """Compare current service availability using the closest direct match as reference."""
    nearest = next((item for item in ranked if item["match"] == 1), None)
    reference_id = nearest["row"]["equity_facility_id"] if nearest else ""
    if not reference_id:
        return ranked, nearest, {}

    candidates = {
        item["row"]["equity_facility_id"]: item
        for item in ranked
        if item["match"] == 1 and item["row"]["equity_facility_id"]
        and item["equity_tier"] == nearest["equity_tier"] and item["dist"] <= nearest["dist"] + extra_km
    }
    observations, _ = compare_observed_availability(
        measures, equity_service_key(service), as_of, reference_id,
        [ident for ident in candidates if ident != reference_id], require_window=True,
    )
    if not observations:
        return ranked, nearest, {}

    best = candidates[min(observations, key=lambda ident: (
        -observations[ident]["available_units"], candidates[ident]["dist"], ident,
    ))]
    first = [nearest] if best is nearest else [best, nearest]
    return [*first, *(item for item in ranked if item is not best and item is not nearest)], nearest, observations


def show_network_plan(facilities, service, lat, lon):
    default = Path(__file__).resolve().parents[1] / "non-ml/caseload/network_caseload.csv.gz"
    path = Path(os.environ.get("DATATHON_NETWORK_CASELOAD_CSV", str(default))).expanduser()
    if not path.is_file():
        return
    with st.expander("Historical load planning"):
        if not st.checkbox("Run a monthly planning scenario"):
            return
        st.caption("This compares hypothetical activity using historical caseloads and modeled throughput. "
                   "Distance is straight-line.")
        assumed = st.checkbox("Include documented service leads as hypothetical providers")
        requests = st.number_input("Additional activities in the planning month", min_value=0, max_value=100000, value=100)
        sites = facilities.loc[facilities["equity_facility_id"].ne(""),
                               ["equity_facility_id", "name", "lat", "lon"]].set_index("equity_facility_id")
        distances = haversine_km(lat, lon, sites["lat"].to_numpy(), sites["lon"].to_numpy())
        sites = sites.assign(distance_km=distances).loc[distances <= 50]
        key = equity_service_key(service)
        try:
            pressure = read_network_pressure(path, key, sites.index)
            supported = {"reported_activity_in_scope"}
            if assumed:
                supported.update({"verified_service_broad_scope", "service_listed_unverified"})
            pressure = pressure.loc[pressure["service_evidence"].isin(supported)]
            candidates = pd.DataFrame([
                {"facility_id": row.facility_id, "service": key, "name": sites.at[row.facility_id, "name"],
                 "distance_km": sites.at[row.facility_id, "distance_km"], "scope": row.scope, "eligible": True,
                 "evidence_tier": "unknown"} for row in pressure.itertuples()
            ])
            if candidates.empty:
                st.info("No branches met the planning criteria within 50 km.")
                return
            plan = replay_network_load(candidates, pressure, requests=int(requests))
        except (OSError, ValueError) as exc:
            st.warning(f"The planning data could not be used. {exc}")
            return
        if not plan["comparable"].any():
            st.info("No pair had comparable planning inputs within the 10 km detour limit.")
        plan = plan.merge(pressure[["facility_id", "service_evidence"]], on="facility_id", how="left", validate="one_to_one")
        plan["service_evidence"] = plan["service_evidence"].map({
            "reported_activity_in_scope": "Historical activity reported for this scope",
            "verified_service_broad_scope": "Scenario assumption based on verified broader service",
            "service_listed_unverified": "Scenario assumption based on unverified service listing",
        })
        columns = {"name": "Facility", "distance_km": "Distance (km)", "service_evidence": "Service evidence",
                   "unit": "Activity unit", "demand": "Typical monthly caseload",
                   "capacity": "Modeled monthly throughput", "caseload_predicted": "Caseload predicted",
                   "capacity_predicted": "Capacity predicted", "reference_end": "Caseload reference end",
                   "capacity_reference_period": "Capacity reference period", "baseline_suggestions": "Nearest-only suggestions",
                   "balanced_suggestions": "Load-balanced suggestions"}
        st.dataframe(plan[list(columns)].rename(columns=columns), hide_index=True)


# --- Recommended ranking: scored by care_engine.rank_facilities -------------------
SEVERITIES = ["Routine", "Urgent", "Emergency"]
# Dashboard service -> care_engine profile name. Services without a profile are registered at runtime
# from this dashboard's aliases so the engine can match them (care_engine.py itself is not modified).
ENGINE_SERVICE = {"Dialysis": "dialysis", "Emergency C-section": "emergency c-section", "Maternity / delivery": "maternal care",
                  "NICU": "nicu", "Pediatric care": "pediatric illness", "Cardiac surgery": "cardiac surgery", "MRI": "mri",
                  "Burn care": "burn care", "Trauma / emergency": "trauma"}


def apply_engine(results, service, severity):
    """Attach care_engine's score to each result (lower = better). Returns False if the engine is unavailable.

    Inputs: evidence from each facility's labelled text; straight-line distance (the engine's own fallback
    travel proxy is distance x 2 when no road travel time exists); occupancy as load over a capacity of 1.0
    when occupancy is known (unknown load adds no penalty). Emergency ranking ignores load by design.
    """
    for res in results:
        res["engine"] = None
    if care_engine is None or not results:
        return False
    key = ENGINE_SERVICE.get(service, service.lower())
    care_engine.SERVICE_PROFILES.setdefault(key, {"terms": SERVICES[service]["aliases"] + [service.lower()], "indicators": []})
    frame = pd.DataFrame([dict(facility_id=str(i), facility_name=x["row"]["name"], facility_type=x["row"].get("facility_type", ""),
                               service_text=x["row"].get("service_text", ""), capacity_units=1.0 if x["occ"] is not None else None,
                               source_url="") for i, x in enumerate(results)])
    travel = {str(i): {"distance_km": max(x["dist"], 0.01), "travel_time_minutes": None} for i, x in enumerate(results)}
    load = {str(i): x["occ"] for i, x in enumerate(results) if x["occ"] is not None}
    scored = {r["facility_id"]: r for r in care_engine.rank_facilities(frame, key, severity.lower(), travel, load, minimum_evidence=0.0)}
    for i, x in enumerate(results):
        r = scored[str(i)]
        emergency = severity.lower() == "emergency"
        x["engine"] = dict(score=r["score"], confidence=r["service_confidence"], terms=r["matched_terms"],
                           load_penalty=r["load_penalty_applied"], travel_part=x["dist"] if emergency else x["dist"] * 2,
                           evidence_penalty=0.0 if emergency else 35 * (1 - r["service_confidence"]))
        terms = f" (matched: {r['matched_terms']})" if r["matched_terms"] else " (no matching terms found)"
        x["why"].insert(1, f"care_engine service evidence: **{r['service_confidence']:.0%}**{terms}.")
    return True


# --- User priorities: toggle what you care about, then rank it ---------------------
CRITERIA = [("distance", "Distance (closer is better)"), ("evidence", "Service evidence"),
            ("capability", "Equipment & staff match"), ("availability", "Open around the clock"),
            ("quality", "Accreditation & rating"), ("freshness", "Up-to-date record"),
            ("spare", "Spare capacity (lower occupancy)")]
CRITERION_LABEL = dict(CRITERIA)


def roc_weights(k: int) -> list:
    """Rank-order-centroid weights: rank 1 gets the most weight, and the weights sum to 1."""
    return [sum(1.0 / j for j in range(i, k + 1)) / k for i in range(1, k + 1)]


def score_priorities(results, ordered_keys):
    """Add res['priority'] (0-100): weighted match to the user's ranked criteria, best match = highest."""
    dists = [x["dist"] for x in results]
    dmin, dmax = min(dists), max(dists)
    weights_by_key = dict(zip(ordered_keys, roc_weights(len(ordered_keys))))
    for x in results:
        vals = {
            "distance": 1.0 if dmax == dmin else 1 - (x["dist"] - dmin) / (dmax - dmin),
            "evidence": (x["engine"]["confidence"] if x.get("engine") else 1.0) * x["match"],
            "capability": x["comps"]["capability"], "availability": x["comps"]["availability"],
            "quality": x["comps"]["quality"], "freshness": x["comps"]["confidence"],
            "spare": 0.5 if x["spare"] is None else x["spare"],   # unknown load counts as neutral
        }
        x["priority"] = round(100 * sum(weights_by_key[k] * vals[k] for k in ordered_keys), 1)
        x["priority_parts"] = [(CRITERION_LABEL[k], round(vals[k], 2), round(weights_by_key[k], 3)) for k in ordered_keys]


# ----------------------------------------------------------------------------
# 5. EQUITY EVIDENCE AND MAP
# ----------------------------------------------------------------------------
def equity_service_key(service):
    if service == "Maternity / delivery":
        return "maternity_delivery"
    return re.sub(r"[^a-z0-9]+", "_", service.lower()).strip("_")


def equity_evidence_for_dashboard(synthetic):
    if synthetic:
        return synthetic_equity_evidence("maternity_delivery", "fdr:synthetic-new-01", "fdr:synthetic-new-06")
    return load_paired_equity_evidence()


def safe(value) -> str:
    """HTML-escape and neutralise characters that could break folium's JS template strings."""
    return html.escape(str(value)).replace("`", "&#96;").replace("$", "&#36;").replace("\\", "&#92;")


def show_html(markup: str, height: int):
    """st.iframe on current Streamlit; components.html on older versions (e.g. Databricks Apps' 1.38)."""
    if hasattr(st, "iframe"):
        st.iframe(markup, height=height)
    else:
        components.html(markup, height=height)


def build_map(origin, top, label) -> str:
    lat0, lon0 = origin
    m = folium.Map(location=[lat0, lon0], zoom_start=11, tiles="OpenStreetMap", control_scale=True)
    folium.Marker([lat0, lon0], tooltip=f"Your location: {safe(label)}", 
                  icon=folium.Icon(color="red", icon="home", prefix="fa")).add_to(m)
    points = [[lat0, lon0]]
    for i, res in enumerate(top, 1):
        r = res["row"]
        phone = r["phone"]
        phone_html = f'<a href="tel:{safe(re.sub(r"[^+0-9]", "", phone))}">{safe(phone)}</a>' if phone else "Not listed"
        hours = safe(r["hours"]) if r["hours"] else "Not listed (call to confirm)"
        gmaps = f"https://www.google.com/maps/dir/?api=1&destination={r['lat']},{r['lon']}"
        popup = (f'<div style="min-width:230px;font:13px/1.45 sans-serif">'
                 f'<b>{i}. {safe(r["name"])}</b><br>'
                 f'<b>Address:</b> {safe(r["address"] or "Not listed")}<br>'
                 f'<b>Phone:</b> {phone_html}<br><b>Hours:</b> {hours}<br>'
                 f'<b>Distance:</b> {res["dist"]:.1f} km (straight line)<br>'
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
st.caption("Choose a service and a location to compare nearby facility records.")

df = load_data()

with st.sidebar:
    st.header("Results")
    top_n = st.slider("Results to show", 1, 10, 5)
    if care_engine is None:
        st.warning(f"'Recommended' is falling back to nearest-first. {CARE_ENGINE_PROBLEM}")

left, right = st.columns(2)
with left:
    with st.container(height=440, border=True):
        st.subheader("1. Service")
        by = st.radio("Browse services by", ["Specialty", "Afflicted body area"], horizontal=True)
        groups = group_services("Specialty" if by == "Specialty" else "Area")
        group = st.selectbox(by, list(groups))
        service = st.selectbox("Service", groups[group])
        m = SERVICES[service]
        st.caption(f"{m['specialty']} · {m['area']}" + (" · time-critical" if m["urgent"] else ""))
with right:
    with st.container(height=440, border=True):
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
weights = dict(capability=0.40, availability=0.25, quality=0.20, confidence=0.15)   # fixed fit-score weights
with st.container(border=True):
    st.subheader("3. Options")
    default_severity = "Urgent" if m["urgent"] else "Routine"
    severity = st.selectbox("How urgent is this need?", SEVERITIES, index=SEVERITIES.index(default_severity),
                            key=f"severity_{service}",
                            help="Used by the Recommended ranking. Emergency ranks purely by travel; routine and urgent also weigh how busy a facility is.")

    routine_comparison = severity == "Routine" and not m["urgent"] and routine_load_allowed(equity_service_key(service))
    balance = st.toggle("Compare current service availability", value=False, disabled=not routine_comparison,
                        help="For routine care with comparable, recent availability measurements.")
    extra_km, measurements_file = 25, None
    if balance and routine_comparison:
        extra_km = st.slider("Max extra straight-line distance (km)", 0, 150, 25, 5)
        measurements_file = st.sidebar.file_uploader("Service availability CSV", type="csv",
                                                    help="Use an authorised facility-level availability export.")

    equity_control, equity_help, _ = st.columns([2, 1, 5], gap="small")
    with equity_control:
        consider_equity = st.toggle("Consider equity score", value=False)
    with equity_help.popover("What's that?"):
        st.write("Women and people from marginalized groups can receive worse care. "
                 "The equity score combines patient-reported respect and clinical outcomes to help choose between similar hospitals.")
    if consider_equity and df.attrs.get("synthetic_demo") and service == "Maternity / delivery":
        st.caption("Try the fictional maternity example at coordinates 28.551395, 77.185600.")

    advanced = st.toggle("Advanced options", value=False)
    priority_keys = []
    if advanced:
        st.markdown("**My priorities**")
        st.caption("Switch on what matters to you, then rank only the ones you switched on (1 = most important). "
                   "Choose 'Ranked criteria (best match first)' in the Sort by menu to use them.")
        toggled = []
        toggle_cols = st.columns(2)
        for pos, (key, label) in enumerate(CRITERIA):
            if toggle_cols[pos % 2].toggle(label, key=f"crit_on_{key}"):
                toggled.append(key)
        k = len(toggled)
        if k:
            st.markdown(f"**Rank your {k} priorit{'y' if k == 1 else 'ies'}** (1 to {k})")
            chosen = []
            for pos, key in enumerate(toggled, 1):
                rank_key = f"crit_rank_{key}"
                stored = st.session_state.get(rank_key)
                if stored is not None and not (1 <= int(stored) <= k):   # a switch was turned off: keep the rank inside 1..k
                    st.session_state[rank_key] = k
                name_col, rank_col = st.columns([4, 1])
                name_col.markdown(f"{CRITERION_LABEL[key]} · rank out of {k}")
                rank = rank_col.number_input(f"Rank of {CRITERION_LABEL[key]} (1 to {k})", min_value=1, max_value=k,
                                             value=pos, step=1, key=rank_key, label_visibility="collapsed")
                chosen.append((int(rank), pos, key))
            priority_keys = [key for _, _, key in sorted(chosen)]
            st.caption("Your order: " + " > ".join(f"{i}. {CRITERION_LABEL[key]}" for i, key in enumerate(priority_keys, 1))
                       + " (ties go to the order listed above).")
        else:
            st.caption("No criteria switched on yet.")
ready = not errors and (("pin" in vals and vals["pin"]) or ("name" in vals and vals["name"]) or ("lat" in vals and vals["lat"] is not None))

if st.button("Find facilities", type="primary", disabled=not ready):
    try:
        lat, lon, label, note, state_hint = resolve_location(kind, vals, df)
    except ValueError as exc:
        st.session_state.pop("search", None)
        st.error(str(exc))
    else:
        st.session_state["search"] = dict(lat=float(lat), lon=float(lon), label=label, note=note, state=state_hint, service=service)
elif not ready and "search" not in st.session_state:
    st.caption("Enter a valid location to enable search.")

search = st.session_state.get("search")
if search:
    lat, lon, label, svc = search["lat"], search["lon"], search["label"], search["service"]
    if search["note"]:
        st.info(search["note"])
    if svc != service:
        st.caption(f"Showing results for **{svc}**. Press Find facilities to search for {service}.")
    if (severity == "Routine" and not SERVICES[svc]["urgent"] and routine_load_allowed(equity_service_key(svc))
            and not df.attrs.get("synthetic_demo")):
        show_network_plan(df, svc, lat, lon)
    ranked = score_facilities(df, svc, lat, lon, weights)
    if not ranked:
        st.warning(f"No facility's text mentions {svc} or a related service.")
    else:
        apply_engine(ranked, svc, severity)
        if advanced and priority_keys:
            score_priorities(ranked, priority_keys)

        availability_order, nearest, observations = ranked, None, {}
        if balance and svc == service and routine_comparison:
            measured_source = measurements_file
            measure_path = os.environ.get("DATATHON_MEASURED_LOAD_CSV", "").strip()
            if measured_source is None and measure_path:
                measured_source = Path(measure_path).expanduser()
            if measured_source is not None:
                try:
                    measures = pd.read_csv(measured_source, dtype="string")
                    availability_order, nearest, observations = balance_rank(
                        ranked, svc, measures, extra_km, pd.Timestamp.now(tz="UTC"),
                    )
                except (OSError, ValueError):
                    st.warning("The service availability CSV could not be read or has missing fields.")
            if not observations:
                st.info("Comparable current availability is unavailable for these facilities. Showing the ordinary order.")
        balance_on = bool(observations)
        results_col, map_col = st.columns([1, 1], gap="medium")
        SORT_RECOMMENDED, SORT_RANKED = "Recommended", "Ranked criteria (best match first)"
        sort_options = [SORT_RECOMMENDED, "Distance (nearest first)", "Fit (highest first)", "Occupancy (lowest first)"]
        if advanced:
            sort_options.append(SORT_RANKED)
        if st.session_state.get("facility_sort_by") not in sort_options:
            st.session_state["facility_sort_by"] = SORT_RECOMMENDED   # also the starting choice

        with results_col:
            header_col, sort_col = st.columns([3, 2])
            with header_col:
                st.subheader("Facility options")
            with sort_col:
                sort_by = st.selectbox("Sort by", sort_options, key="facility_sort_by", label_visibility="collapsed")

            ranked_note = None
            if sort_by == SORT_RANKED and not priority_keys:
                ranked_note = "Switch on at least one criterion under Advanced options to rank by your priorities. Showing the Recommended order for now."
                sort_by = SORT_RECOMMENDED

            ordered = ranked
            if sort_by == SORT_RECOMMENDED:
                ordered = sorted(ranked, key=lambda x: (x["engine"]["score"], -x["engine"]["confidence"]) if x["engine"] else (x["dist"], 0))
            elif sort_by == "Fit (highest first)":
                ordered = sorted(ranked, key=lambda x: x["fit"], reverse=True)
            elif sort_by == "Occupancy (lowest first)":
                ordered = sorted(ranked, key=lambda x: (x["occ"] is None, x["occ"] if x["occ"] is not None else 0))
            elif sort_by == SORT_RANKED:
                ordered = sorted(ranked, key=lambda x: -x["priority"])

            equity = None
            top_indices = range(len(ordered))
            if balance_on:
                preferred = availability_order[0]
                ordered = [preferred, *(item for item in ordered if item is not preferred)]
                st.caption(f"Compared current availability within {extra_km} straight-line km of the closest "
                           f"direct match, {nearest['row']['name']} ({nearest['dist']:.1f} km). "
                           "The facility with the most comparable available units is shown first.")
                if consider_equity:
                    st.info("The current availability comparison determines the first option.")
            elif consider_equity and svc == service and severity != "Emergency" and not SERVICES[svc]["urgent"]:
                if sort_by != SORT_RECOMMENDED:
                    st.info("Choose Recommended sorting to consider equity scores.")
                else:
                    try:
                        evidence = equity_evidence_for_dashboard(df.attrs.get("synthetic_demo", False))
                    except (OSError, ValueError):
                        st.warning("The equity evidence file could not be read. Showing the original order.")
                    else:
                        equity_candidates = [
                            EquityCandidate(item["row"]["equity_facility_id"], item["equity_tier"], item["dist"])
                            for item in ordered
                        ]
                        equity = rank_for_paired_equity(
                            equity_candidates, equity_service_key(svc), enabled=True, evidence=evidence,
                        )
                        top_indices = equity.order
            elif consider_equity and svc == service:
                st.info("Equity scoring is paused for time-critical searches.")

            top_indices = top_indices[:top_n]
            top = [ordered[index] for index in top_indices]
            equity_visible = equity is not None and any(index in equity.scores for index in top_indices)
            if equity_visible and tuple(top_indices) != tuple(range(len(top))):
                st.caption(f"Equity evidence changed the order within the {MAX_EXTRA_KM:g} km straight-line limit. "
                           "The prior rank is shown on each affected card.")
            elif equity_visible:
                st.info("The available equity scores did not change the displayed order.")
            elif equity is not None:
                st.info("Comparable patient-reported respect and clinical outcomes are unavailable for the options shown.")

            if ranked_note:
                st.info(ranked_note)
            if sort_by == SORT_RECOMMENDED and care_engine is not None:
                st.caption("Recommended uses care_engine's score: travel (straight-line distance x 2, no road times), a penalty for weaker "
                           "service evidence, and, for routine or urgent needs, a penalty for busy facilities. Lower scores rank first.")
            st.caption("Service matches are keyword matches in each facility's own text, not cited evidence. "
                       "The team's notebooks (04, 04b) provide cited, scope-checked service evidence.")

            # Fixed-height container with independent scrolling
            with st.container(height=720, border=False):
                for i, (base_index, res) in enumerate(zip(top_indices, top), 1):
                    r = res["row"]
                    observation = observations.get(r["equity_facility_id"])

                    with st.container(border=True):
                        st.markdown(f"### {i}. {r['name']}")
                        st.caption(f"{r['city'] or 'city n/a'}, {r['state'] or 'state n/a'} · "
                                   f"PIN {r['pin'] or 'n/a'} · {r['ownership'] or 'ownership n/a'}")

                        metrics = [("Distance", f"{res['dist']:.1f} km"), ("Fit (/100)", f"{res['fit']:.1f}")]
                        if res["occ"] is not None:
                            metrics.append(("Occupancy", f"{res['occ']:.0%}"))
                        if observation:
                            metrics.append(("Available units", f"{observation['available_units']:g}"))
                        if advanced and priority_keys:
                            metrics.append(("Your match", f"{res['priority']:.0f}/100"))
                        for col, (lab, val) in zip(st.columns(len(metrics)), metrics):
                            with col:
                                st.metric(lab, val)

                        phone = (f"[{r['phone']}](tel:{re.sub(r'[^+0-9]', '', r['phone'])})" if r["phone"] else "Not listed")
                        st.markdown(f"**Address:** {r['address'] or 'Not listed'}  \n"
                                    f"**Phone:** {phone}  \n"
                                    f"**Hours:** {r['hours'] or 'Not listed (call to confirm)'}")
                        st.caption(CALL_PROMPT)

                        if observation:
                            seen = pd.Timestamp(observation["as_of"]).tz_convert("Asia/Kolkata")
                            start = pd.Timestamp(observation["window_start"]).tz_convert("Asia/Kolkata")
                            end = pd.Timestamp(observation["window_end"]).tz_convert("Asia/Kolkata")
                            st.caption(f"{observation['unit']} · observed {seen:%d %b %Y %H:%M} IST · "
                                       f"covers {start:%d %b %Y %H:%M} to {end:%d %b %Y %H:%M} IST")
                            st.markdown(f"[Availability source](<{observation['source_url']}>)")

                        if equity_visible:
                            st.caption(f"Rank before equity score {base_index + 1}")
                            score = equity.scores.get(base_index)
                            if score is None:
                                st.caption("Comparable equity score unavailable for this branch and service.")
                            else:
                                record = score.evidence
                                equity_label = "Fictional equity score" if record.fictional else "Equity score"
                                st.markdown(f"**{equity_label} {score.value:.0f}/100**")
                                with st.expander("Equity score evidence"):
                                    st.caption(f"Uncertainty range {score.lower:.0f}–{score.upper:.0f} out of 100")
                                    st.write(f"{record.respect_measure} · {record.respect_rate:.1%} (n={record.respect_n:,})")
                                    st.write(f"{record.outcome_measure} · {record.outcome_rate:.1%} (n={record.outcome_n:,})")
                                    st.caption(f"{record.population} · {record.period_start} to {record.period_end}")
                                    st.caption(f"Clinical adjustment · {record.adjustment_method}")
                                    if record.source_url:
                                        st.markdown(f"[{record.source_title}](<{record.source_url}>)")
                                    else:
                                        st.caption(record.source_title)

                        st.markdown("**Why this facility**")
                        st.markdown("\n".join(f"- {why}" for why in res["why"]))

                        if res["caveats"]:
                            st.markdown("**Caveats**")
                            st.markdown("\n".join(f"- {w}" for w in res["caveats"]))

                        with st.expander("Score breakdown and source"):
                            comp = pd.DataFrame({"component": list(res["comps"]),
                                                 "value (0-1)": [round(v, 2) for v in res["comps"].values()],
                                                 "weight": [weights[k] for k in res["comps"]]})
                            st.markdown("Fit score")
                            st.dataframe(comp, hide_index=True, use_container_width=True)
                            if res["engine"]:
                                e = res["engine"]
                                st.markdown("Recommended score (care_engine; lower is better)")
                                st.dataframe(pd.DataFrame({"part": ["Travel proxy", "Evidence penalty", "Load penalty", "Total"],
                                                           "value": [round(e["travel_part"], 1), round(e["evidence_penalty"], 1),
                                                                     round(e["load_penalty"], 1), round(e["score"], 1)]}),
                                             hide_index=True, use_container_width=True)
                            if advanced and priority_keys:
                                st.markdown("Your ranked criteria")
                                st.dataframe(pd.DataFrame(res["priority_parts"], columns=["criterion", "value (0-1)", "weight"]),
                                             hide_index=True, use_container_width=True)
                            st.caption(f"Source: {r['source']} · facility_id {r['facility_id']} · last verified: {res['verified'] or 'unknown'}")

        with map_col:
            st.subheader("Map")
            show_html(build_map((lat, lon), top, label), 720)
            st.caption("Click a pin for address, phone, hours and directions. Red = your location, numbered blue pins = results.")
            contacts = pd.DataFrame([{"#": i, "Facility": x["row"]["name"], "Phone": x["row"]["phone"] or "Not listed",
                                      "Hours": x["row"]["hours"] or "Not listed", "Address": x["row"]["address"] or "Not listed",
                                      "Distance (km)": round(x["dist"], 1)} for i, x in enumerate(top, 1)])
            with st.expander("View all facility contact details"):
                st.dataframe(contacts, hide_index=True, use_container_width=True)

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
