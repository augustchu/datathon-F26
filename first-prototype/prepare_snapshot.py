# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# Prepare the facility snapshot once, then let the Streamlit App query its output.
# Run this file as a notebook from the Databricks Git folder (Run all).

import os
import re
import sys
import importlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# If you rename Marketplace objects or move the uploaded geography volume, change these values.
FACILITIES_SOURCE = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities"
PINS_SOURCE = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory"
NFHS_SOURCE = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators"
BOUNDARY_ZIP = "/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip"
POPULATION_CSV_RELATIVE = Path("load-balancing-pipeline") / "outputs" / "nfhs_district_population_2020.csv"
CAPACITY_RELEASE_RELATIVE = Path("max-supplemented-data") / "capacity_and_confidence"
OUTPUT_CATALOG = "additional_india_data"
OUTPUT_SCHEMA = "default"
OUTPUT_PREFIX = "first_prototype"

current = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
repo_root = "/Workspace" + str(Path(current).parent)
POPULATION_CSV = os.getenv(
    "POPULATION_CSV",
    str(Path(repo_root).parent / POPULATION_CSV_RELATIVE),
)
CAPACITY_RELEASE_CSV = Path(repo_root).parent / CAPACITY_RELEASE_RELATIVE / "facilities_newest_capacity.csv"
CAPACITY_CONFIDENCE_CSV = Path(repo_root).parent / CAPACITY_RELEASE_RELATIVE / "facility_confidence.csv"
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
pipeline_module = importlib.import_module("pipeline")
# Notebook kernels retain imported modules after Git folders are refreshed. Reload so this
# notebook picks up helpers added in the current first-prototype/pipeline.py file.
pipeline_module = importlib.reload(pipeline_module)
required_helpers = ("SERVICE_TERMS", "CLASSIC_DISPLAY_FIELDS", "attach_regions", "attach_population",
                    "attach_capacity_release", "prepare_facilities", "read_geojson_archive")
missing_helpers = [name for name in required_helpers if not hasattr(pipeline_module, name)]
if missing_helpers:
    raise ImportError(
        "The workspace copy of first-prototype/pipeline.py is out of date; "
        f"it is missing {missing_helpers}. Pull/sync the first-prototype folder in the Databricks Git folder, "
        "then restart Python and rerun this notebook. Loaded: " + str(pipeline_module.__file__)
    )
SERVICE_TERMS = pipeline_module.SERVICE_TERMS
attach_regions = pipeline_module.attach_regions
attach_population = pipeline_module.attach_population
attach_capacity_release = pipeline_module.attach_capacity_release
prepare_facilities = pipeline_module.prepare_facilities
read_geojson_archive = pipeline_module.read_geojson_archive

# COMMAND ----------

# Validate access and inspect schemas before collecting the modest facility table to pandas.
for source_name in (FACILITIES_SOURCE, PINS_SOURCE, NFHS_SOURCE):
    print(f"{source_name}: {spark.table(source_name).count():,} rows")
    print(spark.table(source_name).columns)

raw_spark = spark.table(FACILITIES_SOURCE)
raw = raw_spark.toPandas()
pins_spark = spark.table(PINS_SOURCE)
nfhs_spark = spark.table(NFHS_SOURCE)
nfhs = nfhs_spark.toPandas()

# COMMAND ----------

# Read the tracked capacity and confidence release from the repository. These CSVs
# arrive through the Databricks Git-folder sync; no separate Volume upload is needed.
for release_path in (CAPACITY_RELEASE_CSV, CAPACITY_CONFIDENCE_CSV):
    if not release_path.exists():
        raise FileNotFoundError(f"Required release file is missing from the Databricks Git folder: {release_path}")
capacity_release_columns = [
    "row_index", "facility_id", "unique_id", "name", "state", "district", "city", "pin", "latitude", "longitude",
    "capacity", "description", "specialties", "capability", "procedure", "equipment", "source_urls",
    "capacity_status", "capacity_source",
    "capacity_source_url", "capacity_prediction_lower", "capacity_prediction_upper", "capacity_model_id",
    "original_capacity", "published_phone", "officialPhone", "phone_numbers", "phone_source_url",
    "officialWebsite", "website", "websites",
]
classic_columns = {column for aliases in pipeline_module.CLASSIC_DISPLAY_FIELDS.values() for column in aliases}
release_header = pd.read_csv(CAPACITY_RELEASE_CSV, nrows=0).columns
capacity_release_columns.extend(
    name for name in release_header if name in classic_columns and name not in capacity_release_columns
)
capacity_release = pd.read_csv(CAPACITY_RELEASE_CSV, usecols=capacity_release_columns, dtype={"unique_id": "string"})
confidence_release = pd.read_csv(CAPACITY_CONFIDENCE_CSV)
display_source = attach_capacity_release(raw, capacity_release, confidence_release)
# From this point, facility display/service/location fields come from the cleaned
# release rows. The Marketplace snapshot is used only for capacity cross-checks.
prepared = prepare_facilities(display_source)
capacity_release_summary = (
    f"newest-source capacity release; {int(prepared['beds_for_scenario'].notna().sum()):,} capacity values; "
    f"{int(prepared['capacity_release_status'].astype(str).str.casefold().eq('xgboost_estimate').sum()):,} marked estimates"
)
print("Capacity source:", capacity_release_summary)
print("Capacity cross-check against Marketplace source:", prepared["capacity_crosscheck_status"].value_counts(dropna=False).to_dict())

# Read the GeoJSON member from the supplied Unity Catalog Volume ZIP with Python's
# standard library. No geopandas/geospatial package is needed in serverless compute.
if not Path(BOUNDARY_ZIP).exists():
    raise FileNotFoundError(f"Boundary archive not found at {BOUNDARY_ZIP}")
boundaries = read_geojson_archive(BOUNDARY_ZIP)
prepared = attach_regions(prepared, boundaries, nfhs)
if not Path(POPULATION_CSV).exists():
    raise FileNotFoundError(
        f"District population estimates not found at {POPULATION_CSV}. Confirm the full "
        "repository is synced to the Databricks Git folder, or set POPULATION_CSV to an "
        "alternate Volume path."
    )
population = pd.read_csv(POPULATION_CSV)
prepared = attach_population(prepared, population)

# Include source-row traceability and a stable timestamp with each preparation run.
prepared["snapshot_prepared_at"] = datetime.now(timezone.utc).isoformat()
prepared["facility_source_table"] = FACILITIES_SOURCE
prepared["facility_display_source"] = prepared["facility_display_source"].replace(
    "", "max-supplemented-data/capacity_and_confidence/facilities_newest_capacity.csv"
)
prepared["capacity_release_summary"] = capacity_release_summary

# COMMAND ----------

# Publish into the existing catalog/schema confirmed in the Databricks Volumes browser.
# This avoids `main` (which is absent in this workspace) and avoids CREATE SCHEMA.
for identifier in (OUTPUT_CATALOG, OUTPUT_SCHEMA, OUTPUT_PREFIX):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError(f"Invalid Unity Catalog identifier: {identifier!r}")
output_schema = f"{OUTPUT_CATALOG}.{OUTPUT_SCHEMA}"
spark.sql(f"DESCRIBE SCHEMA `{OUTPUT_CATALOG}`.`{OUTPUT_SCHEMA}`").show(truncate=False)
print(f"Publishing prototype tables under {output_schema}")

# Use explicit scalar types so nullable model outputs and claim flags stay consistent in Delta.
numeric_cols = {
    "latitude", "longitude", "beds_for_scenario", "regional_population", "confidence_score", "confidence_penalty",
    "capacity_prediction_lower", "capacity_prediction_upper", "capacity_release_original",
}
boolean_cols = {c for c in prepared.columns if c.startswith("claim_citable_")}
for col in numeric_cols:
    prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
for col in boolean_cols:
    prepared[col] = prepared[col].fillna(False).astype(bool)
for col in prepared.columns:
    if col not in numeric_cols | boolean_cols:
        prepared[col] = prepared[col].where(pd.notna(prepared[col]), None).astype(object)
        prepared[col] = prepared[col].map(lambda value: None if value is None else str(value))

facility_sdf = spark.createDataFrame(prepared)
facility_table = f"{output_schema}.{OUTPUT_PREFIX}_facilities"
pin_table = f"{output_schema}.{OUTPUT_PREFIX}_pin_directory"
nfhs_table = f"{output_schema}.{OUTPUT_PREFIX}_nfhs_district_indicators"
metadata_table = f"{output_schema}.{OUTPUT_PREFIX}_snapshot_metadata"
facility_sdf.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(facility_table)

# Keep the supplemental source snapshots available for downstream iteration and auditing.
pins_spark.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(pin_table)
nfhs_spark.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(nfhs_table)

metadata = spark.createDataFrame([{
    "prepared_at": datetime.now(timezone.utc).isoformat(),
    "facility_source_table": FACILITIES_SOURCE,
    "facility_display_source": "max-supplemented-data/capacity_and_confidence/facilities_newest_capacity.csv",
    "pin_source_table": PINS_SOURCE,
    "nfhs_source_table": NFHS_SOURCE,
    "boundary_archive": BOUNDARY_ZIP,
    "population_estimates": POPULATION_CSV,
    "capacity_release": str(CAPACITY_RELEASE_CSV),
    "confidence_release": str(CAPACITY_CONFIDENCE_CSV),
    "facility_rows": int(len(prepared)),
    "capacity_values": int(prepared["beds_for_scenario"].notna().sum()),
    "capacity_estimates": int(prepared["capacity_release_status"].astype(str).str.casefold().eq("xgboost_estimate").sum()),
    "capacity_crosscheck_nonmatches": int((~prepared["capacity_crosscheck_status"].isin(["match", "both blank"])).sum()),
    "capacity_source_alignment_differences": int(prepared["capacity_source_alignment_status"].eq("difference").sum()),
    "confidence_mean": float(prepared["confidence_score"].mean()),
    "citable_service_claims": int(sum(prepared[f"claim_citable_{key}"].sum() for key in [
        "dialysis", "emergency_c_section", "nicu", "cardiac_surgery", "mri", "burn_care",
        "trauma_emergency", "maternity_delivery", "general_care"])),
    "capacity_release_summary": capacity_release_summary,
}])
metadata.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(metadata_table)

display(prepared[["facility_name", "city", "state", "adm2_district", "nfhs_district", "region_match_status", "regional_population", "population_status", "beds_for_scenario", "beds_provenance", "capacity_crosscheck_status", "capacity_source_alignment_status", "confidence_score", "confidence_tier", "phone", "website_url"]].head(20))
display(spark.table(metadata_table))
print(f"Published {len(prepared):,} facility rows to {facility_table}")
print(f"Population matched: {prepared.regional_population.notna().sum():,} / {len(prepared):,} facility rows")
print("Capacity cross-check against Marketplace field:", prepared.capacity_crosscheck_status.value_counts(dropna=False).to_dict())
print(f"Capacity release estimates: {prepared.capacity_release_status.astype(str).str.casefold().eq('xgboost_estimate').sum():,}")
print(f"App tables: {facility_table}, {metadata_table}")
