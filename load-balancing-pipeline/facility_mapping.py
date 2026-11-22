"""Facility ingestion and ADM2 -> NFHS district assignment."""
from __future__ import annotations

from pathlib import Path
import json
import re
import pandas as pd
import geopandas as gpd


def _as_items(value):
    """Decode JSON-encoded arrays from the facility export, with plain-text fallback."""
    if pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [text]


def _extract_urls(value):
    items = _as_items(value)
    urls = []
    for item in items:
        urls.extend(re.findall(r"https?://[^\s\"'<>]+", item))
    return list(dict.fromkeys(url.rstrip(",.;)") for url in urls))


def normalize_facilities(frame, name_col, lat_col, lon_col, service_cols,
                         capacity_col=None, source_col=None, facility_type_col=None,
                         id_col=None, city_col=None, state_col=None, postcode_col=None):
    """Build the small canonical schema expected by the recommendation engine."""
    result = pd.DataFrame(index=frame.index)
    if id_col:
        source_ids = frame[id_col].fillna("").astype(str).str.strip()
    else:
        source_ids = pd.Series("", index=frame.index)
    row_ids = []
    seen = set()
    for i, source_id in enumerate(source_ids):
        base = source_id if source_id else f"facility_{i}"
        candidate = base
        suffix = 1
        while candidate in seen:
            candidate = f"{base}__row_{i}_{suffix}"
            suffix += 1
        seen.add(candidate)
        row_ids.append(candidate)
    result["facility_id"] = row_ids
    result["source_facility_id"] = source_ids.values
    result["facility_name"] = frame[name_col].fillna("Unnamed facility").astype(str).values
    result["facility_type"] = (
        frame[facility_type_col].fillna("").astype(str).values
        if facility_type_col else ""
    )
    result["latitude"] = pd.to_numeric(frame[lat_col], errors="coerce").values
    result["longitude"] = pd.to_numeric(frame[lon_col], errors="coerce").values
    # Keep source-field labels so the rules engine can distinguish structured
    # service claims (capability/procedure/specialty/equipment) from description.
    result["service_text"] = frame[service_cols].apply(
        lambda row: " | ".join(
            f"{column}: " + "; ".join(_as_items(value))
            for column, value in row.items() if _as_items(value)
        ), axis=1
    ).values
    if facility_type_col:
        result["service_text"] = result.apply(
            lambda row: row.service_text + (f" | facility type: {row.facility_type}"
                                             if row.facility_type.strip() else ""), axis=1
        )
    result["evidence_text"] = result["service_text"]
    result["capacity_units"] = (
        pd.to_numeric(frame[capacity_col], errors="coerce").values
        if capacity_col else float("nan")
    )
    if source_col:
        result["source_urls"] = frame[source_col].apply(_extract_urls).values
        result["source_url"] = result.source_urls.apply(lambda urls: urls[0] if urls else "")
    else:
        result["source_urls"] = [[] for _ in range(len(frame))]
        result["source_url"] = ""
    for target, column in (("address_city", city_col), ("address_state", state_col),
                           ("address_postcode", postcode_col)):
        result[target] = frame[column].fillna("").astype(str).values if column else ""
    result["source_row"] = frame.index.astype(str)
    return result


def assign_facility_regions(facilities, polygons_path, resolved_crosswalk_path):
    """Spatially assign facilities only to ADM2 polygons with a resolved NFHS link."""
    polygons = gpd.read_file(polygons_path)
    polygons["polygon_id"] = polygons["shapeID"].astype(str)
    crosswalk = pd.read_csv(resolved_crosswalk_path, dtype=str)
    links = crosswalk[["polygon_id", "nfhs_district_key", "nfhs_district_name",
                       "nfhs_state", "resolution_status", "resolution_method"]].drop_duplicates()
    polygons = polygons.merge(links, on="polygon_id", how="inner", validate="one_to_one")
    facilities = facilities.copy()
    facilities["latitude"] = pd.to_numeric(facilities.latitude, errors="coerce")
    facilities["longitude"] = pd.to_numeric(facilities.longitude, errors="coerce")
    valid_coords = (
        facilities.latitude.between(-90, 90) & facilities.longitude.between(-180, 180)
    )
    invalid = facilities.loc[~valid_coords].copy()
    facilities = facilities.loc[valid_coords].copy()
    points = gpd.GeoDataFrame(
        facilities.copy(),
        geometry=gpd.points_from_xy(facilities.longitude, facilities.latitude),
        crs="EPSG:4326",
    ).to_crs(polygons.crs)
    joined = gpd.sjoin(
        points,
        polygons[["polygon_id", "nfhs_district_key", "nfhs_district_name",
                  "nfhs_state", "resolution_status", "resolution_method", "geometry"]],
        how="left",
        predicate="within",
    )
    counts = joined.groupby("facility_id").polygon_id.nunique()
    joined["region_match_count"] = joined.facility_id.map(counts).fillna(0).astype(int)
    joined["region_match_status"] = joined.region_match_count.map(
        lambda n: "matched" if n == 1 else ("ambiguous_overlap" if n > 1 else "unmapped")
    )
    joined.loc[joined.region_match_count != 1,
               ["nfhs_district_key", "nfhs_district_name", "nfhs_state"]] = pd.NA
    invalid["region_match_count"] = 0
    invalid["region_match_status"] = "invalid_coordinates"
    for column in ["polygon_id", "nfhs_district_key", "nfhs_district_name", "nfhs_state",
                   "resolution_status", "resolution_method"]:
        invalid[column] = pd.NA
    combined = pd.concat([
        pd.DataFrame(joined.drop(columns=["geometry", "index_right"], errors="ignore")),
        invalid,
    ], ignore_index=True, sort=False)
    return combined


def build_region_geometries(polygons_path, resolved_crosswalk_path):
    """Union resolved ADM2 component polygons back to their NFHS-5 region."""
    polygons = gpd.read_file(polygons_path)
    polygons["polygon_id"] = polygons["shapeID"].astype(str)
    crosswalk = pd.read_csv(resolved_crosswalk_path, dtype=str)
    polygons = polygons.merge(
        crosswalk[["polygon_id", "nfhs_district_key", "nfhs_district_name", "nfhs_state"]],
        on="polygon_id", how="inner", validate="one_to_one",
    )
    regions = polygons.dissolve(
        by=["nfhs_district_key", "nfhs_district_name", "nfhs_state"],
        as_index=False,
    )
    return regions

