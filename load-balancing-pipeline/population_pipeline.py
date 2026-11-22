"""NFHS-5 / WorldPop district population enrichment.

Run prepare, review outputs/district_crosswalk.csv and PIN-vote confidence,
then run finalize. Never approve a district match without checking its state/geography.
"""
from __future__ import annotations
import argparse
import re
import zipfile
from pathlib import Path
import geopandas as gpd
import pandas as pd
from exactextract import exact_extract
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
ZIP = ROOT / "geoBoundaries-IND-ADM2-all.zip"
NFHS = ROOT / "NFHS-5 Dataset.csv"
CENSUS = ROOT / "india-districts-census-2011.csv"
PIN_FILE = ROOT / "india_post_pincode_directory.csv"
RASTER = ROOT / "ind_ppp_2020_UNadj_constrained.tif"
POLYGON_FILE = "geoBoundaries-IND-ADM2.geojson"
CROSSWALK = OUT / "district_crosswalk.csv"
OFFICIAL = ROOT / "india_state_population_2020.csv"

def key(value):
    value = str(value).lower().replace("&", " and ")
    value = re.sub(r"\b(district|distt?\.?)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()

def state_key(value):
    value = key(value)
    if value.startswith("the "):
        value = value[4:]
    aliases = {
        "nct of delhi": "delhi",
        "maharastra": "maharashtra",
        "dnh and dd": "dadra and nagar haveli and daman and diu",
        "dadra and nagar haveli and daman and diu": "dadra and nagar haveli and daman and diu",
        "dadra and nagar haveli": "dadra and nagar haveli and daman and diu",
        "daman and diu": "dadra and nagar haveli and daman and diu",
    }
    return aliases.get(value, value)

def polygons_path():
    OUT.mkdir(exist_ok=True)
    path = OUT / POLYGON_FILE
    if not path.exists():
        with zipfile.ZipFile(ZIP) as archive:
            archive.extract(POLYGON_FILE, OUT)
    return path

def prepare():
    """Assign polygon states from unique PIN votes, then match within state."""
    nfhs = pd.read_csv(NFHS, dtype={"district_name": str, "state_ut": str})
    polygons = gpd.read_file(polygons_path())
    if "shapeName" not in polygons or "shapeID" not in polygons:
        raise ValueError("Expected geoBoundaries properties shapeName and shapeID.")
    if not PIN_FILE.exists():
        raise FileNotFoundError(f"Missing PIN directory: {PIN_FILE}")
    polygons["polygon_id"] = polygons.shapeID.astype(str)
    polygons["polygon_name"] = polygons.shapeName.astype(str)
    pins = pd.read_csv(PIN_FILE, dtype={"pincode": str, "district": str, "statename": str},
                       low_memory=False)
    required = {"pincode", "district", "statename", "latitude", "longitude"}
    if not required.issubset(pins.columns):
        raise ValueError(f"PIN directory needs columns: {sorted(required)}")
    pins["latitude"] = pd.to_numeric(pins.latitude, errors="coerce")
    pins["longitude"] = pd.to_numeric(pins.longitude, errors="coerce")
    pins = pins.dropna(subset=["pincode", "district", "statename", "latitude", "longitude"])
    # One representative point for each PIN/state/district combination, so a PIN
    # with many post-office rows does not dominate the polygon vote.
    pin_points = pins.groupby(["pincode", "statename", "district"], as_index=False).agg(
        latitude=("latitude", "median"), longitude=("longitude", "median"))
    points = gpd.GeoDataFrame(
        pin_points,
        geometry=gpd.points_from_xy(pin_points.longitude, pin_points.latitude),
        crs="EPSG:4326",
    ).to_crs(polygons.crs)
    joined = gpd.sjoin(
        points, polygons[["polygon_id", "geometry"]],
        how="inner", predicate="within",
    )
    # A PIN counts at most once per polygon. Keep its modal source labels there.
    pin_votes = joined.groupby(["polygon_id", "pincode"], as_index=False).agg(
        pin_state=("statename", lambda x: x.mode().iloc[0]),
        pin_district=("district", lambda x: x.mode().iloc[0]),
    )
    pin_votes["state_vote_key"] = pin_votes.pin_state.map(state_key)
    vote_counts = pin_votes.groupby(["polygon_id", "state_vote_key"]).size().rename("votes").reset_index()
    vote_counts = vote_counts.sort_values(["polygon_id", "votes"], ascending=[True, False])
    top_votes = vote_counts.drop_duplicates("polygon_id").rename(
        columns={"state_vote_key": "pin_state_key", "votes": "state_votes"})
    pin_counts = pin_votes.groupby("polygon_id").pincode.nunique().rename("pin_count")
    state_votes = polygons[["polygon_id", "polygon_name"]].merge(
        pin_counts, on="polygon_id", how="left").merge(
        top_votes[["polygon_id", "pin_state_key", "state_votes"]], on="polygon_id", how="left")
    state_votes["pin_count"] = state_votes.pin_count.fillna(0).astype(int)
    state_votes["state_vote_share"] = state_votes.state_votes / state_votes.pin_count.replace(0, pd.NA)
    state_votes["state_assignment_status"] = state_votes.apply(
        lambda r: "no_geocoded_pins" if r.pin_count == 0 else
        ("review_fewer_than_3_pins" if r.pin_count < 3 else
         ("review_vote_below_80pct" if r.state_vote_share < .8 else "pin_supported")),
        axis=1,
    )
    district_modes = pin_votes.groupby("polygon_id").pin_district.agg(
        lambda x: x.mode().iloc[0] if not x.mode().empty else pd.NA).rename("pin_district_mode")
    district_counts = pin_votes.groupby(["polygon_id", "pin_district"]).size().rename(
        "district_votes").reset_index().sort_values(
            ["polygon_id", "district_votes"], ascending=[True, False])
    top_districts = district_counts.drop_duplicates("polygon_id").rename(
        columns={"district_votes": "pin_district_votes"})
    state_votes = state_votes.merge(district_modes, on="polygon_id", how="left").merge(
        top_districts[["polygon_id", "pin_district_votes"]], on="polygon_id", how="left")
    state_votes["pin_district_share"] = (
        state_votes.pin_district_votes / state_votes.pin_count.replace(0, pd.NA))
    nfhs["state_key"] = nfhs.state_ut.map(state_key)
    nfhs["nfhs_district_key"] = nfhs.apply(
        lambda r: f"{state_key(r.state_ut)}::{key(r.district_name)}", axis=1)
    states_by_key = nfhs.groupby("state_key").state_ut.first().to_dict()
    state_votes["nfhs_state"] = state_votes.pin_state_key.map(states_by_key)
    state_votes.to_csv(OUT / "polygon_state_votes.csv", index=False)
    districts = nfhs[["district_name", "state_ut", "state_key", "nfhs_district_key"]].drop_duplicates()
    rows = []
    for _, p in state_votes.iterrows():
        state_districts = districts[districts.state_key == p.pin_state_key]
        candidates = []
        for _, d in state_districts.iterrows():
            shape_score = fuzz.ratio(key(p.polygon_name), key(d.district_name))
            pin_score = fuzz.ratio(key(p.pin_district_mode), key(d.district_name)) if pd.notna(p.pin_district_mode) else 0
            score = max(shape_score, pin_score)
            method = ("exact_polygon_name" if shape_score == 100 else
                      ("exact_pin_district" if pin_score == 100 else
                       ("fuzzy_polygon_name" if shape_score >= pin_score else "fuzzy_pin_district")))
            candidates.append((d, score, method))
        candidates.sort(key=lambda x: x[1], reverse=True)
        if not candidates:
            rows.append({
                **p.to_dict(), "nfhs_district_key": "", "nfhs_district_name": "",
                "match_method": "state_not_matched_to_nfhs", "match_confidence": 0,
                "candidate_rank": 1, "status": "review",
            })
            continue
        # Offer one strong candidate; retain several only when no >=90 match exists.
        selected = [c for c in candidates if c[1] >= 90][:1] or candidates[:3]
        for rank, (d, score, method) in enumerate(selected, 1):
            auto_approved = (
                method == "exact_polygon_name"
                and p.state_assignment_status == "pin_supported"
                and score == 100
            )
            rows.append({
                **p.to_dict(),
                "nfhs_district_key": d.nfhs_district_key,
                "nfhs_district_name": d.district_name,
                "match_method": method if score >= 90 else f"manual_review_{method}",
                "match_confidence": round(score / 100, 3),
                "candidate_rank": rank,
                "status": "approved" if auto_approved else "review",
            })
    crosswalk = pd.DataFrame(rows)
    crosswalk.to_csv(CROSSWALK, index=False)
    unresolved = crosswalk[crosswalk.nfhs_district_key.fillna("") == ""]
    print(f"ADM2 polygons: {len(polygons):,}; NFHS districts: {districts.nfhs_district_key.nunique():,}")
    print(f"Geocoded PIN combinations used: {len(pin_points):,}; polygons with no vote: {(state_votes.pin_count == 0).sum():,}")
    print(f"Review {OUT / 'polygon_state_votes.csv'} for state vote share and low-count flags.")
    print(f"Review {CROSSWALK} for within-state district proposals; {len(unresolved):,} polygons lack an NFHS state match.")

def percent(value):
    raw = str(value).strip()
    if not raw or raw == "*" or raw.lower() in {"nan", "na", "null"}:
        return None, False
    low_confidence = raw.startswith("(") and raw.endswith(")")
    try:
        return float(raw.strip("() ").replace(",", "")), low_confidence
    except ValueError:
        return None, False

def finalize():
    if not CROSSWALK.exists():
        raise FileNotFoundError("Run population_pipeline.py prepare first.")
    if not OFFICIAL.exists() or pd.read_csv(OFFICIAL).empty:
        raise FileNotFoundError("Expected india_state_population_2020.csv with state_ut and persons columns.")
    polygons = gpd.read_file(polygons_path())
    polygons["polygon_id"] = polygons.shapeID.astype(str)
    cw = pd.read_csv(CROSSWALK, dtype=str)
    approved = cw[cw.status.str.lower() == "approved"].copy()
    if approved.empty:
        raise ValueError("No approved crosswalk rows.")
    approved["nfhs_state"] = approved.nfhs_state.fillna("").astype(str).str.strip()
    if approved.nfhs_state.eq("").any():
        raise ValueError("Approved rows need nfhs_state filled from reviewed PIN/state evidence.")
    if approved.polygon_id.duplicated().any():
        raise ValueError("Each ADM2 polygon must map to only one NFHS district.")
    missing_polygons = set(polygons.shapeID.astype(str)) - set(approved.polygon_id)
    nfhs = pd.read_csv(NFHS, dtype={"district_name": str, "state_ut": str})
    nfhs["nfhs_district_key"] = nfhs.apply(
        lambda r: f"{key(r.state_ut)}::{key(r.district_name)}", axis=1)
    missing_districts = set(nfhs.nfhs_district_key) - set(approved.nfhs_district_key)
    if missing_polygons or missing_districts:
        raise ValueError(
            f"Crosswalk is incomplete: {len(missing_polygons)} ADM2 polygons and "
            f"{len(missing_districts)} NFHS districts still lack an approved mapping. "
            "Add reviewed rows for every polygon and NFHS district before scaling."
        )
    if approved.groupby("nfhs_district_key").nfhs_state.nunique().gt(1).any():
        raise ValueError("An NFHS district is assigned to multiple states in the crosswalk.")
    matched = polygons.merge(approved, on="polygon_id", validate="one_to_one")
    zonal = exact_extract(
        str(RASTER), matched, ["sum"],
        include_cols=["polygon_id", "nfhs_district_key", "nfhs_district_name", "nfhs_state",
                      "pin_state_key", "pin_count", "state_vote_share", "state_assignment_status",
                      "pin_district_mode", "pin_district_share", "match_method", "match_confidence"],
        output="pandas",
    ).rename(columns={"sum": "raster_pop"})
    zonal["raster_pop"] = pd.to_numeric(zonal.raster_pop, errors="coerce")
    zonal["state_scale_key"] = zonal.nfhs_state.map(state_key)
    raw = zonal.groupby("state_scale_key", as_index=False).raster_pop.sum().rename(
        columns={"raster_pop": "raster_state_total"})
    official = pd.read_csv(OFFICIAL)
    if not {"state_ut", "persons"}.issubset(official.columns):
        raise ValueError("india_state_population_2020.csv must include state_ut and persons.")
    # Ignore the national total and separate pre-merger DNH/DD rows; use the combined
    # 2020 Union Territory row supplied in the state projection file.
    if "notes" in official.columns:
        official = official[~official.notes.fillna("").str.contains("merged into one UT", case=False)]
    official = official[official.state_ut.map(state_key) != state_key("India")].copy()
    official["state_scale_key"] = official.state_ut.map(state_key)
    official["population_2020"] = pd.to_numeric(official.persons, errors="coerce")
    official = official.drop_duplicates("state_scale_key", keep="last")
    states = raw.merge(official[["state_scale_key", "population_2020"]], on="state_scale_key",
                       how="left", validate="one_to_one")
    states["population_2020"] = pd.to_numeric(states.population_2020, errors="coerce")
    missing = states[states.population_2020.isna()].nfhs_state.tolist()
    if missing:
        raise ValueError("Missing official population for: " + ", ".join(missing))
    states["state_scale_factor"] = states.population_2020 / states.raster_state_total
    states["scale_flag"] = states.state_scale_factor.map(
        lambda x: "review outside 0.9-1.1" if x < .9 or x > 1.1 else "within 0.9-1.1")
    zonal = zonal.merge(states[["state_scale_key", "state_scale_factor", "scale_flag"]],
                        on="state_scale_key", validate="many_to_one")
    zonal["pop_2020_polygon"] = zonal.raster_pop * zonal.state_scale_factor
    district = zonal.groupby(
        ["nfhs_district_key", "nfhs_district_name", "nfhs_state"], as_index=False
    ).agg(
        raster_pop=("raster_pop", "sum"),
        pop_2020=("pop_2020_polygon", "sum"),
        state_scale_factor=("state_scale_factor", "first"),
        scale_flag=("scale_flag", "first"),
        polygon_ids=("polygon_id", lambda values: "|".join(map(str, values))),
        pin_state_key=("pin_state_key", "first"),
        pin_count=("pin_count", "min"),
        state_vote_share=("state_vote_share", "min"),
        state_assignment_status=("state_assignment_status", "first"),
        pin_district_mode=("pin_district_mode", "first"),
        pin_district_share=("pin_district_share", "min"),
    )
    crosswalk_summary = approved.groupby("nfhs_district_key", as_index=False).agg(
        match_method=("match_method", lambda values: "|".join(sorted(set(values)))),
        match_confidence=("match_confidence", lambda values: pd.to_numeric(values).min()),
    )
    district = district.merge(crosswalk_summary, on="nfhs_district_key", validate="one_to_one")
    parsed = nfhs.population_below_age_15_years_pct.map(percent)
    nfhs["population_below_age_15_years_pct_num"] = parsed.map(lambda x: x[0])
    nfhs["under_15_pct_low_confidence"] = parsed.map(lambda x: x[1])
    result = district.merge(nfhs[["nfhs_district_key", "population_below_age_15_years_pct_num",
                                  "under_15_pct_low_confidence"]],
                            on="nfhs_district_key", how="left", validate="one_to_one")
    result["pop_under_15_estimate"] = (
        result.pop_2020 * result.population_below_age_15_years_pct_num / 100)
    result["population_source"] = "WorldPop 2020 constrained raster, scaled to MoHFW/NCP state projection"
    result.to_csv(OUT / "nfhs_district_population_2020.csv", index=False)
    states.to_csv(OUT / "state_scaling_diagnostics.csv", index=False)
    census = pd.read_csv(CENSUS, dtype={"State name": str, "District name": str})
    census["census_key"] = census.apply(
        lambda r: f"{key(r['State name'])}::{key(r['District name'])}", axis=1)
    census = census[["census_key", "Population"]].drop_duplicates("census_key")
    validation = result[["nfhs_district_key", "nfhs_district_name", "nfhs_state", "pop_2020"]].copy()
    validation["census_key"] = validation.apply(
        lambda r: f"{state_key(r.nfhs_state)}::{key(r.nfhs_district_name)}", axis=1)
    validation = validation.merge(census, on="census_key", how="left", validate="one_to_one")
    validation["annualized_growth_2011_2020_pct"] = (
        ((validation.pop_2020 / pd.to_numeric(validation.Population, errors="coerce")) ** (1 / 9) - 1) * 100
    )
    validation["validation_note"] = validation.Population.map(
        lambda v: "exact state/name candidate; verify district boundaries were not split"
        if pd.notna(v) else "no exact Census 2011 state/name match")
    validation.to_csv(OUT / "census_2011_validation_candidates.csv", index=False)
    print(f"Wrote {OUT / 'nfhs_district_population_2020.csv'} and state_scaling_diagnostics.csv")
    print(f"Wrote {OUT / 'census_2011_validation_candidates.csv'} for manual vintage review")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["prepare", "finalize"])
    args = parser.parse_args()
    (prepare if args.step == "prepare" else finalize_reviewed)()

def resolve_crosswalk_reviewed(cw, nfhs):
    nfhs = nfhs.copy()
    nfhs["nfhs_district_key"] = nfhs.apply(
        lambda r: f"{state_key(r.state_ut)}::{key(r.district_name)}", axis=1)
    accepted, excluded = [], []
    for polygon_id, group in cw.groupby("polygon_id", sort=False):
        approved = group[group.status.str.lower().isin({"approved", "approve"})]
        if not approved.empty:
            row = approved.iloc[0].to_dict()
            row["resolution_method"] = "human_approved"
            row["resolution_status"] = "approved"
            row["approximate"] = False
            accepted.append(row)
            continue
        selected = []
        for _, row in group.iterrows():
            status = re.sub(r"[_-]+", " ", row.status.lower()).strip()
            shape, pin, target = key(row.polygon_name), key(row.pin_district_mode), key(row.nfhs_district_name)
            if status in {"polygon disagrees", "polygon differs"} and target and pin == target:
                selected.append((row, "pin_and_nfhs_agree", False))
            elif status == "pin disagrees" and target and shape == target:
                selected.append((row, "polygon_and_nfhs_agree", False))
            elif status == "polygon differs from pin" and target and shape == target:
                selected.append((row, "polygon_and_nfhs_agree", False))
            elif status == "polygon is subdistrict of nfhs district" and target and pin == target:
                selected.append((row, "manual_parent_district_aggregation", True))
        if len(selected) == 1:
            row, method, approximate = selected[0]
            row = row.to_dict()
            row["resolution_method"] = method
            row["resolution_status"] = str(row["status"])
            row["approximate"] = approximate
            accepted.append(row)
            continue
        if set(group.status.str.lower()) == {"nfhs disagrees"}:
            first = group.iloc[0]
            target = key(first.polygon_name)
            same = nfhs[nfhs.district_name.map(key).eq(target)]
            if target and key(first.pin_district_mode) == target and len(same) == 1:
                row = first.to_dict()
                row["nfhs_district_key"] = same.iloc[0].nfhs_district_key
                row["nfhs_district_name"] = same.iloc[0].district_name
                row["nfhs_state"] = same.iloc[0].state_ut
                row["resolution_method"] = "unique_polygon_pin_nfhs_name_override"
                row["resolution_status"] = "nfhs disagrees"
                row["approximate"] = False
                accepted.append(row)
                continue
        excluded.append(group.assign(exclusion_reason="review, rejected, or conflict not uniquely resolved"))
    resolved = pd.DataFrame(accepted)
    if not resolved.empty and resolved.polygon_id.duplicated().any():
        raise ValueError("More than one reviewed district mapping remains for a polygon.")
    omitted = pd.concat(excluded, ignore_index=True) if excluded else pd.DataFrame()
    return resolved, omitted

def infer_neighbor_states(polygons, state_votes, reviewed):
    votes = state_votes.copy()
    usable = {"pin_supported", "accepted", "approve", "state matches district does not"}
    votes["resolved_state"] = votes.apply(
        lambda r: r.nfhs_state if r.state_assignment_status.lower() in usable else "", axis=1)
    if not reviewed.empty:
        chosen = reviewed.set_index("polygon_id").nfhs_state.to_dict()
        votes["resolved_state"] = votes.apply(
            lambda r: chosen.get(r.polygon_id, "") or r.resolved_state, axis=1)
    geo = polygons.merge(votes[["polygon_id", "resolved_state"]], on="polygon_id", validate="one_to_one")
    metric = geo.to_crs("EPSG:6933")
    geom = metric.set_index("polygon_id").geometry
    for polygon_id in metric.loc[metric.resolved_state.eq(""), "polygon_id"].tolist():
        shape = geom.loc[polygon_id]
        idxs = metric.sindex.query(shape, predicate="touches")
        lengths, counts = {}, {}
        for idx in idxs:
            neighbor = metric.iloc[idx]
            if neighbor.polygon_id == polygon_id or not neighbor.resolved_state:
                continue
            shared = shape.boundary.intersection(neighbor.geometry.boundary).length
            if shared > 0:
                name = neighbor.resolved_state
                lengths[name] = lengths.get(name, 0) + shared
                counts[name] = counts.get(name, 0) + 1
        total = sum(lengths.values())
        if total:
            winner = max(lengths, key=lengths.get)
            if lengths[winner] / total >= .8 and counts[winner] >= 2:
                geo.loc[geo.polygon_id == polygon_id, "resolved_state"] = winner
    return geo[["polygon_id", "resolved_state"]]

def finalize_reviewed():
    """Finalize only supported/approved mappings; keep unresolved NFHS areas null."""
    polygons = gpd.read_file(polygons_path())
    polygons["polygon_id"] = polygons.shapeID.astype(str)
    nfhs = pd.read_csv(NFHS, dtype={"district_name": str, "state_ut": str})
    cw = pd.read_csv(CROSSWALK, dtype=str).fillna("")
    resolved, omitted = resolve_crosswalk_reviewed(cw, nfhs)
    votes = pd.read_csv(OUT / "polygon_state_votes.csv", dtype=str).fillna("")
    votes["polygon_id"] = votes.polygon_id.astype(str)
    states_by_polygon = infer_neighbor_states(polygons, votes, resolved)

    # Sum every polygon with a supported state assignment, even if its district
    # mapping was rejected. This prevents district exclusions shrinking state sums.
    all_zonal = exact_extract(str(RASTER), polygons, ["sum"],
                              include_cols=["polygon_id"], output="pandas").rename(
                                  columns={"sum": "raster_pop"})
    all_zonal["polygon_id"] = all_zonal.polygon_id.astype(str)
    all_zonal["raster_pop"] = pd.to_numeric(all_zonal.raster_pop, errors="coerce")
    all_zonal = all_zonal.merge(states_by_polygon, on="polygon_id", validate="one_to_one")
    unresolved_states = int(all_zonal.resolved_state.eq("").sum())
    all_zonal["state_scale_key"] = all_zonal.resolved_state.map(state_key)
    state_raw = all_zonal[all_zonal.state_scale_key.astype(str).str.strip().ne("")].groupby(
        "state_scale_key", as_index=False).agg(
            raster_state_total=("raster_pop", "sum"),
            polygons_in_raster=("polygon_id", "nunique"),
        )
    official = pd.read_csv(OFFICIAL)
    if not {"state_ut", "persons"}.issubset(official.columns):
        raise ValueError("india_state_population_2020.csv must include state_ut and persons.")
    if "notes" in official.columns:
        official = official[~official.notes.fillna("").str.contains("merged into one UT", case=False)]
    official = official[official.state_ut.map(state_key) != state_key("India")].copy()
    official["state_scale_key"] = official.state_ut.map(state_key)
    official["population_2020"] = pd.to_numeric(official.persons, errors="coerce")
    official = official.drop_duplicates("state_scale_key", keep="last")
    state_diag = state_raw.merge(official[["state_scale_key", "population_2020"]],
                                 on="state_scale_key", how="left", validate="one_to_one")
    state_diag["state_scale_factor"] = state_diag.population_2020 / state_diag.raster_state_total
    state_diag["scale_factor_flag"] = state_diag.state_scale_factor.map(
        lambda x: "review_outside_0.9_1.1" if pd.notna(x) and (x < .9 or x > 1.1) else
        ("missing_factor" if pd.isna(x) else "within_0.9_1.1"))
    state_diag["coverage_flag"] = (
        "partial_unknown_polygon" if unresolved_states else "all_polygon_states_assigned")
    state_diag["scale_flag"] = state_diag.scale_factor_flag + "|" + state_diag.coverage_flag
    state_diag["unresolved_polygon_count_global"] = unresolved_states
    state_diag.to_csv(OUT / "state_scaling_diagnostics.csv", index=False)

    if not resolved.empty:
        district_zonal = all_zonal.merge(
            resolved[["polygon_id", "nfhs_district_key", "nfhs_district_name", "nfhs_state",
                      "match_method", "match_confidence", "resolution_method",
                      "resolution_status", "approximate"]],
            on="polygon_id", how="inner", validate="one_to_one")
        district_zonal["state_scale_key"] = district_zonal.nfhs_state.map(state_key)
        district_zonal = district_zonal.merge(
            state_diag[["state_scale_key", "state_scale_factor", "scale_flag"]],
            on="state_scale_key", how="left", validate="many_to_one")
        district_zonal["pop_2020_polygon"] = district_zonal.raster_pop * district_zonal.state_scale_factor
        districts = district_zonal.groupby(
            ["nfhs_district_key", "nfhs_district_name", "nfhs_state"], as_index=False
        ).agg(
            raster_pop=("raster_pop", "sum"),
            pop_2020=("pop_2020_polygon", lambda s: s.sum(min_count=1)),
            state_scale_factor=("state_scale_factor", "first"),
            scale_flag=("scale_flag", "first"),
            polygon_count=("polygon_id", "nunique"),
            polygon_ids=("polygon_id", lambda values: "|".join(values)),
            match_method=("match_method", lambda values: "|".join(sorted(set(values)))),
            match_confidence=("match_confidence", lambda values: pd.to_numeric(values, errors="coerce").min()),
            resolution_method=("resolution_method", lambda values: "|".join(sorted(set(values)))),
            resolution_status=("resolution_status", lambda values: "|".join(sorted(set(values)))),
            approximate_boundary_match=("approximate", "max"),
        )
    else:
        districts = pd.DataFrame(columns=["nfhs_district_key"])
    nfhs["nfhs_district_key"] = nfhs.apply(
        lambda r: f"{state_key(r.state_ut)}::{key(r.district_name)}", axis=1)
    parsed = nfhs.population_below_age_15_years_pct.map(percent)
    nfhs["population_below_age_15_years_pct_num"] = parsed.map(lambda x: x[0])
    nfhs["under_15_pct_low_confidence"] = parsed.map(lambda x: x[1])
    result = nfhs[["nfhs_district_key", "district_name", "state_ut",
                   "population_below_age_15_years_pct_num", "under_15_pct_low_confidence"]].merge(
        districts, on="nfhs_district_key", how="left", validate="one_to_one")
    result["district_population_status"] = result.apply(
        lambda r: "available" if pd.notna(r.pop_2020) else
        ("no_resolved_polygon_mapping" if pd.isna(r.polygon_count) else
         "unscaled_state_incomplete"), axis=1)
    result["pop_under_15_estimate"] = result.pop_2020 * result.population_below_age_15_years_pct_num / 100
    result["population_source"] = "WorldPop 2020 constrained raster, state-scaled to MoHFW/NCP projection"
    result.to_csv(OUT / "nfhs_district_population_2020.csv", index=False)
    unassigned = all_zonal[all_zonal.resolved_state.eq("")].merge(
        polygons[["polygon_id", "shapeName"]].rename(columns={"shapeName": "polygon_name"}),
        on="polygon_id", how="left", validate="one_to_one")
    unassigned.to_csv(OUT / "excluded_unassigned_state_polygons.csv", index=False)
    if not omitted.empty:
        omitted.to_csv(OUT / "excluded_crosswalk_rows.csv", index=False)
    resolved.to_csv(OUT / "resolved_polygon_crosswalk.csv", index=False)
    census = pd.read_csv(CENSUS, dtype={"State name": str, "District name": str})
    census["census_key"] = census.apply(
        lambda r: f"{state_key(r['State name'])}::{key(r['District name'])}", axis=1)
    census = census[["census_key", "Population"]].drop_duplicates("census_key")
    validation = result[["nfhs_district_key", "district_name", "state_ut", "pop_2020"]].copy()
    validation["census_key"] = validation.apply(
        lambda r: f"{state_key(r.state_ut)}::{key(r.district_name)}", axis=1)
    validation = validation.merge(census, on="census_key", how="left", validate="one_to_one")
    validation["annualized_growth_2011_2020_pct"] = (
        ((validation.pop_2020 / pd.to_numeric(validation.Population, errors="coerce")) ** (1 / 9) - 1) * 100
    )
    validation["validation_note"] = validation.Population.map(
        lambda v: "exact state/name candidate; verify district boundaries were not split"
        if pd.notna(v) else "no exact Census 2011 state/name match")
    validation.to_csv(OUT / "census_2011_validation_candidates.csv", index=False)
    print(f"Included resolved polygon mappings: {len(resolved):,}; unresolved state polygons: {unresolved_states:,}")
    print(f"District estimates with non-null scaled population: {result.district_population_status.eq('available').sum():,}/{len(result):,}")
    print(f"Wrote {OUT / 'nfhs_district_population_2020.csv'} and state_scaling_diagnostics.csv")

if __name__ == "__main__":
    main()
