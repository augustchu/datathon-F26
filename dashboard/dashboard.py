"""Facility Finder: pick a service + a location, get ranked facilities with evidence.

Run locally:  streamlit run app.py
Data: uses synthetic demo data by default; upload a CSV (schema in the sidebar)
or edit load_data() to read your team's table (e.g. from Databricks SQL).
"""
import math
import re

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Facility Finder", page_icon="🏥", layout="wide")

# ----------------------------------------------------------------------------
# 1. SERVICE TAXONOMY  (edit freely; every service has a specialty + body area)
# ----------------------------------------------------------------------------
SERVICES = {
    "Dialysis": dict(specialty="Nephrology", area="Kidneys", urgent=False,
                     equip=["hemodialysis machine", "RO water plant"],
                     staff=["nephrologist", "dialysis technician"], related=[]),
    "Emergency C-section": dict(specialty="Obstetrics & Gynecology", area="Pregnancy & childbirth", urgent=True,
                                equip=["operation theatre", "blood bank"],
                                staff=["obstetrician", "anesthetist"], related=["Maternity / delivery"]),
    "Maternity / delivery": dict(specialty="Obstetrics & Gynecology", area="Pregnancy & childbirth", urgent=False,
                                 equip=["labour room", "operation theatre"],
                                 staff=["obstetrician"], related=[]),
    "NICU": dict(specialty="Neonatology", area="Newborn", urgent=True,
                 equip=["incubator", "ventilator", "phototherapy unit"],
                 staff=["neonatologist"], related=["Pediatric care"]),
    "Pediatric care": dict(specialty="Pediatrics", area="Children", urgent=False,
                           equip=["pediatric ward"], staff=["pediatrician"], related=[]),
    "Cardiac surgery": dict(specialty="Cardiology & Cardiac Surgery", area="Heart", urgent=True,
                            equip=["cath lab", "heart-lung machine", "ICU"],
                            staff=["cardiac surgeon", "perfusionist"], related=["Cardiology consultation"]),
    "Cardiology consultation": dict(specialty="Cardiology & Cardiac Surgery", area="Heart", urgent=False,
                                    equip=["ECG", "echocardiography"], staff=["cardiologist"], related=[]),
    "MRI": dict(specialty="Radiology", area="Imaging (whole body)", urgent=False,
                equip=["MRI scanner"], staff=["radiologist", "MRI technologist"], related=["CT scan"]),
    "CT scan": dict(specialty="Radiology", area="Imaging (whole body)", urgent=False,
                    equip=["CT scanner"], staff=["radiologist"], related=[]),
    "Burn care": dict(specialty="Plastic & Burn Surgery", area="Skin", urgent=True,
                      equip=["burn unit", "operation theatre", "ICU"],
                      staff=["plastic surgeon"], related=["Trauma / emergency"]),
    "Orthopedic surgery": dict(specialty="Orthopedics", area="Bones & joints", urgent=False,
                               equip=["operation theatre", "C-arm / X-ray"],
                               staff=["orthopedic surgeon", "anesthetist"], related=["Fracture care"]),
    "Fracture care": dict(specialty="Orthopedics", area="Bones & joints", urgent=True,
                          equip=["X-ray", "plaster room"], staff=["orthopedic surgeon"], related=["Trauma / emergency"]),
    "Dental care": dict(specialty="Dentistry", area="Teeth & mouth", urgent=False,
                        equip=["dental chair", "dental X-ray"], staff=["dentist"], related=["Oral surgery"]),
    "Oral surgery": dict(specialty="Dentistry", area="Teeth & mouth", urgent=False,
                         equip=["operation theatre", "dental X-ray"], staff=["oral surgeon"], related=["Dental care"]),
    "Cataract surgery": dict(specialty="Ophthalmology", area="Eyes", urgent=False,
                             equip=["operating microscope", "phaco machine"], staff=["ophthalmologist"], related=[]),
    "Stroke care": dict(specialty="Neurology", area="Brain & nerves", urgent=True,
                        equip=["CT scanner", "ICU", "thrombolysis protocol"],
                        staff=["neurologist"], related=["Trauma / emergency"]),
    "Chemotherapy": dict(specialty="Oncology", area="Cancer", urgent=False,
                         equip=["chemo day-care", "pharmacy"], staff=["oncologist"], related=[]),
    "Trauma / emergency": dict(specialty="Emergency Medicine", area="Whole body", urgent=True,
                               equip=["emergency bay", "ICU", "blood bank", "ambulance"],
                               staff=["emergency physician"], related=[]),
}


def group_services(by: str) -> dict:
    key = "specialty" if by == "Specialty" else "area"
    groups: dict = {}
    for name, meta in SERVICES.items():
        groups.setdefault(meta[key], []).append(name)
    return dict(sorted(groups.items()))


# ----------------------------------------------------------------------------
# 2. LOCATION VALIDATION
# ----------------------------------------------------------------------------
# Indian PIN first-two-digit postal regions that are actually assigned.
# (Format/region check only: it cannot prove a specific PIN is in use.)
def _valid_prefixes() -> set:
    ranges = [(11, 28), (30, 34), (36, 53), (56, 64), (67, 85)]
    return {f"{n:02d}" for lo, hi in ranges for n in range(lo, hi + 1)}


VALID_PIN_PREFIXES = _valid_prefixes()
INDIA_BBOX = dict(lat=(6.5, 37.5), lon=(68.0, 97.5))
PLACE_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-&()]{1,59}$")


def validate_pin(raw: str):
    """Return (pin, error). error is None when valid."""
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
    """Return (clean_name, error) for a city / district / state text box."""
    name = " ".join((raw or "").split())
    if not name:
        return None, f"Enter a {label} name."
    if not PLACE_RE.match(name):
        return None, f"{label.capitalize()} must be 2-60 characters, start with a letter, and use only letters, spaces, . ' - & ( )."
    return name, None


def validate_coordinates(lat_raw: str, lon_raw: str):
    """Return (lat, lon, error, warning)."""
    try:
        lat = float((lat_raw or "").strip())
        lon = float((lon_raw or "").strip())
    except ValueError:
        return None, None, "Latitude and longitude must be decimal numbers, e.g. 18.5204 and 73.8567.", None
    if not (-90 <= lat <= 90):
        return None, None, "Latitude must be between -90 and 90.", None
    if not (-180 <= lon <= 180):
        return None, None, "Longitude must be between -180 and 180.", None
    warn = None
    if not (INDIA_BBOX["lat"][0] <= lat <= INDIA_BBOX["lat"][1]
            and INDIA_BBOX["lon"][0] <= lon <= INDIA_BBOX["lon"][1]):
        warn = "These coordinates are outside India; results may be empty."
    return lat, lon, None, warn


# ----------------------------------------------------------------------------
# 3. DATA
# ----------------------------------------------------------------------------
REQUIRED_COLS = ["name", "city", "district", "state", "pin", "lat", "lon", "services"]
OPTIONAL_DEFAULTS = dict(equipment="", specialists="", open_24x7=False, accreditation="",
                         rating=np.nan, beds=np.nan, occupancy=np.nan, ownership="", last_verified=pd.NaT, source="not provided")
LIST_COLS = ["services", "equipment", "specialists"]


def _split(s) -> list:
    if isinstance(s, list):
        return s
    return [x.strip() for x in str(s).split(";") if x.strip() and str(s) != "nan"]


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df = df.copy()
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    df["pin"] = df["pin"].astype(str).str.extract(r"(\d+)")[0].str.zfill(6)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])
    for c in LIST_COLS:
        df[c] = df[c].apply(_split)
    df["open_24x7"] = df["open_24x7"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
    df["last_verified"] = pd.to_datetime(df["last_verified"], errors="coerce")
    df["occupancy"] = pd.to_numeric(df["occupancy"], errors="coerce")
    df["occupancy"] = df["occupancy"].where(df["occupancy"].isna() | (df["occupancy"] <= 1), df["occupancy"] / 100)
    return df.reset_index(drop=True)


@st.cache_data
def demo_data() -> pd.DataFrame:
    """Synthetic data so the app runs out of the box. Replace with real data."""
    rng = np.random.default_rng(7)
    cities = {  # city: (state, district, lat, lon, base PIN)
        "New Delhi": ("Delhi", "New Delhi", 28.6139, 77.2090, 110001),
        "Mumbai": ("Maharashtra", "Mumbai", 19.0760, 72.8777, 400001),
        "Pune": ("Maharashtra", "Pune", 18.5204, 73.8567, 411001),
        "Bengaluru": ("Karnataka", "Bengaluru Urban", 12.9716, 77.5946, 560001),
        "Chennai": ("Tamil Nadu", "Chennai", 13.0827, 80.2707, 600001),
        "Kolkata": ("West Bengal", "Kolkata", 22.5726, 88.3639, 700001),
        "Lucknow": ("Uttar Pradesh", "Lucknow", 26.8467, 80.9462, 226001),
        "Jaipur": ("Rajasthan", "Jaipur", 26.9124, 75.7873, 302001),
    }
    kinds = ["City Hospital", "Medical Centre", "Super Speciality Hospital", "Govt. Hospital",
             "Care Institute", "Memorial Hospital"]
    all_services = list(SERVICES)
    rows = []
    for city, (state, district, lat, lon, pin0) in cities.items():
        for i in range(8):
            offered = list(rng.choice(all_services, size=rng.integers(4, 11), replace=False))
            equip, staff = set(), set()
            for s in offered:
                equip |= {e for e in SERVICES[s]["equip"] if rng.random() < 0.85}
                staff |= {p for p in SERVICES[s]["staff"] if rng.random() < 0.85}
            rows.append(dict(
                name=f"{city} {kinds[i % len(kinds)]} {i + 1}", city=city, district=district, state=state,
                pin=str(pin0 + int(rng.integers(0, 40))),
                lat=lat + rng.uniform(-0.12, 0.12), lon=lon + rng.uniform(-0.12, 0.12),
                services=";".join(offered), equipment=";".join(sorted(equip)), specialists=";".join(sorted(staff)),
                open_24x7=bool(rng.random() < 0.55),
                accreditation=str(rng.choice(["NABH", "JCI", "", ""])),
                rating=round(float(rng.uniform(3.0, 4.9)), 1), beds=int(rng.integers(30, 600)), occupancy=round(float(rng.uniform(0.35, 0.98)), 2),
                ownership=str(rng.choice(["Government", "Private", "Trust"])),
                last_verified=(pd.Timestamp.today() - pd.Timedelta(days=int(rng.integers(10, 600)))).date().isoformat(),
                source="synthetic demo data"))
    return pd.DataFrame(rows)


def load_data() -> pd.DataFrame:
    st.sidebar.header("Data")
    mode = st.sidebar.radio("Facility data", ["Demo (synthetic)", "Upload CSV"])
    if mode == "Upload CSV":
        up = st.sidebar.file_uploader("CSV file", type="csv")
        st.sidebar.caption("Required columns: " + ", ".join(REQUIRED_COLS)
                           + ". Optional: equipment, specialists, open_24x7, accreditation, rating, beds, "
                             "ownership, occupancy (0-1 or percent), last_verified, source. List columns use `;` separators.")
        if up is None:
            st.info("Upload a facility CSV in the sidebar, or switch to demo data.")
            st.stop()
        return normalize(pd.read_csv(up))
    st.sidebar.warning("Demo data is synthetic. Do not use it for real decisions.")
    return normalize(demo_data())
    # To use Databricks instead, replace the line above with something like:
    #   from databricks.sdk.core import Config; from databricks import sql
    #   ... pd.read_sql / cursor.fetchall_arrow().to_pandas() ... then normalize(df)


# ----------------------------------------------------------------------------
# 4. LOCATION RESOLUTION + SCORING
# ----------------------------------------------------------------------------
def resolve_location(kind, vals, df):
    """Return (lat, lon, label, note) or raise ValueError with a user-facing message."""
    if kind == "Coordinates (lat/lon)":
        return vals["lat"], vals["lon"], f"{vals['lat']:.4f}, {vals['lon']:.4f}", None
    if kind == "PIN code":
        pin = vals["pin"]
        hit = df[df["pin"] == pin]
        if len(hit):
            return hit["lat"].mean(), hit["lon"].mean(), f"PIN {pin}", None
        near = df[df["pin"].str[:3] == pin[:3]]
        if len(near):
            return (near["lat"].mean(), near["lon"].mean(), f"PIN {pin}",
                    f"No facility on record in PIN {pin}; using the centre of PIN area {pin[:3]}xxx (approximate).")
        raise ValueError(f"No facilities on record near PIN {pin}. Try a city or enter coordinates.")
    col = "city" if kind == "City" else "district"
    hit = df[df[col].str.lower() == vals["name"].lower()]
    if vals.get("state"):
        hit = hit[hit["state"].str.lower() == vals["state"].lower()]
    if len(hit):
        return hit["lat"].mean(), hit["lon"].mean(), vals["name"], f"Using the centre of facilities in this {col}."
    raise ValueError(f"No facilities on record for {col} '{vals['name']}'. Check spelling or enter coordinates.")


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _covered(required, have):
    have_l = [h.lower() for h in have]
    found = [r for r in required if any(r.lower() in h or h in r.lower() for h in have_l)]
    return found, [r for r in required if r not in found]


def score_facilities(df, service, lat, lon, weights):
    """Score every facility that offers the service (or a related one); sorted nearest-first."""
    meta = SERVICES[service]
    d = haversine_km(lat, lon, df["lat"].values, df["lon"].values)
    pool = df.assign(distance_km=d)
    today = pd.Timestamp.today()
    results = []
    for _, r in pool.iterrows():
        have = {s.lower() for s in r["services"]}
        if service.lower() in have:
            match, match_note = 1.0, f"Lists **{service}** as an offered service."
        else:
            alt = [x for x in meta["related"] if x.lower() in have]
            if not alt:
                continue
            match, match_note = 0.5, f"Does not list {service} directly, but lists related service **{alt[0]}** (partial match)."

        eq_f, eq_m = _covered(meta["equip"], r["equipment"])
        st_f, st_m = _covered(meta["staff"], r["specialists"])
        capability = (len(eq_f) / max(len(meta["equip"]), 1) + len(st_f) / max(len(meta["staff"]), 1)) / 2
        availability = 1.0 if r["open_24x7"] else (0.3 if meta["urgent"] else 0.8)
        accredited = 1.0 if r["accreditation"] else 0.5
        rating = (r["rating"] / 5) if pd.notna(r["rating"]) else 0.6
        quality = 0.5 * accredited + 0.5 * rating
        if pd.isna(r["last_verified"]):
            age_m, confidence = None, 0.5
        else:
            age_m = (today - r["last_verified"]).days / 30
            confidence = 1.0 if age_m <= 6 else 0.8 if age_m <= 12 else 0.5

        comps = dict(capability=capability, availability=availability, quality=quality, confidence=confidence)
        fit = match * sum(weights[k] * v for k, v in comps.items()) / max(sum(weights.values()), 1e-9)
        occ = None if pd.isna(r["occupancy"]) else float(r["occupancy"])

        why = [match_note, f"**{r['distance_km']:.1f} km** from your location ({r['city']}, {r['state']})."]
        if occ is not None:
            why.append(f"Current occupancy about **{occ:.0%}** ({1 - occ:.0%} spare capacity).")
        if eq_f:
            why.append("Equipment on record: " + ", ".join(eq_f) + ".")
        if st_f:
            why.append("Specialists on record: " + ", ".join(st_f) + ".")
        if r["open_24x7"]:
            why.append("Open 24x7" + (" (important for this time-critical service)." if meta["urgent"] else "."))
        if r["accreditation"]:
            why.append(f"Accredited: {r['accreditation']}.")
        if pd.notna(r["rating"]):
            why.append(f"Patient rating {r['rating']}/5" + (f", {int(r['beds'])} beds." if pd.notna(r["beds"]) else "."))

        caveats = []
        if match < 1:
            caveats.append("Only a related service is listed. Call to confirm.")
        if eq_m:
            caveats.append("No record of: " + ", ".join(eq_m) + ".")
        if st_m:
            caveats.append("No record of: " + ", ".join(st_m) + ".")
        if meta["urgent"] and not r["open_24x7"]:
            caveats.append("Not listed as 24x7, but this service is often time-critical.")
        if occ is None:
            caveats.append("No occupancy data; load is unknown.")
        if age_m is None:
            caveats.append("Data has no verification date.")
        elif age_m > 12:
            caveats.append(f"Data last verified {age_m / 12:.1f} years ago and may be stale.")

        results.append(dict(row=r, dist=float(r["distance_km"]), fit=round(100 * fit, 1), match=match,
                            comps=comps, occ=occ, spare=None if occ is None else max(0.0, 1 - occ),
                            why=why, caveats=caveats,
                            verified=None if pd.isna(r["last_verified"]) else r["last_verified"].date().isoformat()))
    return sorted(results, key=lambda x: x["dist"])


def balance_rank(ranked, extra_km, lam):
    """Re-rank candidates within `extra_km` of the closest option, favouring spare capacity.

    balance = match * ( lam * spare_capacity + (1 - lam) * closeness )
    where closeness = 1 at the closest option and 0 at +extra_km. Unknown load counts as 50% spare.
    """
    nearest = ranked[0]
    cands = [x for x in ranked if x["dist"] - nearest["dist"] <= extra_km]
    for x in cands:
        spare = 0.5 if x["spare"] is None else x["spare"]
        closeness = 1 - (x["dist"] - nearest["dist"]) / max(extra_km, 1e-9)
        x["balance"] = round(100 * x["match"] * (lam * spare + (1 - lam) * closeness), 1)
        extra = x["dist"] - nearest["dist"]
        n_occ = "unknown" if nearest["occ"] is None else f"{nearest['occ']:.0%}"
        x_occ = "unknown" if x["occ"] is None else f"{x['occ']:.0%}"
        if x is nearest:
            x["balance_note"] = f"Load balancing: this is also the closest option (occupancy {x_occ})."
        else:
            x["balance_note"] = (f"Load balancing: **{extra:.1f} km farther** than the closest option "
                                 f"({nearest['row']['name']}), with occupancy {x_occ} vs {n_occ} there.")
    return sorted(cands, key=lambda x: -x["balance"]), nearest


# ----------------------------------------------------------------------------
# 5. UI
# ----------------------------------------------------------------------------
df = load_data()

with st.sidebar:
    st.header("Results")
    top_n = st.slider("Results to show", 1, 10, 5)
    with st.expander("Fit-score weights"):
        st.caption("The fit score rates each facility's ability to deliver the service. Results are ordered by distance.")
        weights = dict(
            capability=st.slider("Equipment & staff match", 0.0, 1.0, 0.40, 0.05),
            availability=st.slider("Availability (24x7)", 0.0, 1.0, 0.25, 0.05),
            quality=st.slider("Accreditation & rating", 0.0, 1.0, 0.20, 0.05),
            confidence=st.slider("Data freshness", 0.0, 1.0, 0.15, 0.05),
        )

st.title("🏥 Facility Finder")
st.caption("Choose a service and a location. Facilities are listed nearest first, with the evidence behind each.")

left, right = st.columns(2)

with left:
    st.subheader("1. Service")
    by = st.radio("Browse services by", ["Specialty", "Afflicted body area"], horizontal=True)
    groups = group_services("Specialty" if by == "Specialty" else "Area")
    group = st.selectbox(by, list(groups))
    service = st.selectbox("Service", groups[group])
    m = SERVICES[service]
    st.caption(f"{m['specialty']} · {m['area']}" + (" · time-critical" if m["urgent"] else ""))

with right:
    st.subheader("2. Location")
    kind = st.selectbox("Location type", ["PIN code", "City", "District", "Coordinates (lat/lon)"])
    vals, errors, warnings = {}, [], []
    if kind == "PIN code":
        raw = st.text_input("PIN code", max_chars=6, placeholder="e.g. 411001")
        if raw:
            vals["pin"], err = validate_pin(raw)
            errors += [err] if err else []
    elif kind in ("City", "District"):
        raw = st.text_input(kind, placeholder="e.g. Pune")
        state = st.text_input("State (optional, to disambiguate)", placeholder="e.g. Maharashtra")
        if raw:
            vals["name"], err = validate_place(raw, kind.lower())
            errors += [err] if err else []
        if state:
            vals["state"], err = validate_place(state, "state")
            errors += [err] if err else []
    else:
        c1, c2 = st.columns(2)
        lat_raw = c1.text_input("Latitude", placeholder="18.5204")
        lon_raw = c2.text_input("Longitude", placeholder="73.8567")
        if lat_raw or lon_raw:
            la, lo, err, warn = validate_coordinates(lat_raw, lon_raw)
            vals.update(lat=la, lon=lo)
            errors += [err] if err else []
            warnings += [warn] if warn else []
    for e in errors:
        st.error(e)
    for w in warnings:
        st.warning(w)

st.subheader("3. Options")
balance = st.toggle("⚖️ Balance load: also consider farther facilities with more spare capacity", value=False)
extra_km, lam = 25, 0.6
if balance:
    o1, o2 = st.columns(2)
    extra_km = o1.slider("Extra travel accepted vs. the closest option (km)", 5, 150, 25, 5)
    lam = o2.slider("Favor spare capacity over closeness", 0.0, 1.0, 0.6, 0.1)
    if m["urgent"]:
        st.warning(f"{service} is time-critical. Extra travel time can cost more than a shorter wait; "
                   "use load balancing only when the patient is stable.")

ready = not errors and (
    ("pin" in vals and vals["pin"]) or ("name" in vals and vals["name"]) or ("lat" in vals and vals["lat"] is not None))

if st.button("Find facilities", type="primary", disabled=not ready):
    try:
        lat, lon, label, note = resolve_location(kind, vals, df)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    if note:
        st.info(note)

    ranked = score_facilities(df, service, lat, lon, weights)
    if not ranked:
        st.warning(f"No facility in the data lists {service} or a related service.")
        st.stop()

    if balance:
        pool, nearest = balance_rank(ranked, extra_km, lam)
        top = pool[:top_n]
        st.subheader(f"Load-balanced picks for {service} near {label}")
        st.caption(f"Candidates: facilities within {extra_km} km of the closest option "
                   f"({nearest['row']['name']}, {nearest['dist']:.1f} km). Ordered by balance score.")
    else:
        top = ranked[:top_n]
        st.subheader(f"Nearest {len(top)} for {service} near {label}")

    for i, res in enumerate(top, 1):
        r = res["row"]
        with st.container(border=True):
            metrics = [("Distance", f"{res['dist']:.1f} km"), ("Fit", f"{res['fit']}/100"),
                       ("Occupancy", "n/a" if res["occ"] is None else f"{res['occ']:.0%}")]
            if balance:
                metrics.append(("Balance", f"{res['balance']}/100"))
            cols = st.columns([4] + [1] * len(metrics))
            cols[0].markdown(f"### {i}. {r['name']}")
            cols[0].caption(f"{r['city']}, {r['district']}, {r['state']} · PIN {r['pin']} · {r['ownership'] or 'ownership n/a'}")
            for col, (lab, val) in zip(cols[1:], metrics):
                col.metric(lab, val)
            st.markdown("**Why this facility**")
            bullets = ([res["balance_note"]] if balance else []) + res["why"]
            st.markdown("\n".join(f"- {w}" for w in bullets))
            if res["caveats"]:
                st.markdown("**Caveats**")
                st.markdown("\n".join(f"- {w}" for w in res["caveats"]))
            with st.expander("Fit breakdown and source"):
                comp = pd.DataFrame({"component": list(res["comps"]),
                                     "value (0-1)": [round(v, 2) for v in res["comps"].values()],
                                     "weight": [weights[k] for k in res["comps"]]})
                st.dataframe(comp, hide_index=True, use_container_width=True)
                st.caption(f"Source: {r['source']} · last verified: {res['verified'] or 'unknown'}")

    if balance and not any(x is nearest for x in top):
        st.caption(f"For comparison, the closest option is {nearest['row']['name']} "
                   f"({nearest['dist']:.1f} km, occupancy "
                   f"{'unknown' if nearest['occ'] is None else format(nearest['occ'], '.0%')}).")

    pts = pd.DataFrame([dict(lat=lat, lon=lon, color="#e63946", size=900)]
                       + [dict(lat=x["row"]["lat"], lon=x["row"]["lon"], color="#1d70b8", size=600) for x in top])
    st.map(pts, latitude="lat", longitude="lon", color="color", size="size")
    st.caption("Red = your location, blue = recommended facilities.")
elif not ready:
    st.caption("Enter a valid location to enable search.")